#!/usr/bin/env python3

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


CPP_NODE_CTOR_RE = re.compile(r'\b(?:Node|LifecycleNode)\s*\(\s*"(?P<name>[^"]+)"')
PY_NODE_CTOR_RE = re.compile(r'\bsuper\(\)\.__init__\s*\(\s*"(?P<name>[^"]+)"')

CPP_SERVICE_CLIENT_RE = re.compile(r'create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<name>[^"]+)"')
CPP_SERVICE_SERVER_RE = re.compile(r'create_service\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<name>[^"]+)"')
CPP_ACTION_CLIENT_RE = re.compile(
    r'rclcpp_action::create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?"(?P<name>[^"]+)"',
    re.MULTILINE,
)
CPP_ACTION_SERVER_RE = re.compile(
    r'rclcpp_action::create_server\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?"(?P<name>[^"]+)"',
    re.MULTILINE,
)

PY_SERVICE_CLIENT_RE = re.compile(r'create_client\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_SERVICE_SERVER_RE = re.compile(r'create_service\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_ACTION_CLIENT_RE = re.compile(r'ActionClient\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_ACTION_SERVER_RE = re.compile(r'ActionServer\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')

BLOCKING_PATTERNS = [
    re.compile(r'\bfuture\s*\.\s*get\s*\('),
    re.compile(r'\bspin_until_future_complete\s*\('),
    re.compile(r'\bwait_for\s*\('),
    re.compile(r'\bwait_for_service\s*\('),
    re.compile(r'\bwait_for_action_server\s*\('),
    re.compile(r'\btime\s*\.\s*sleep\s*\('),
    re.compile(r'\bstd::this_thread::sleep_for\s*\('),
    re.compile(r'\brclcpp::sleep_for\s*\('),
]

CALLBACK_TRIGGER_PATTERNS = [
    re.compile(r'create_subscription\s*\('),
    re.compile(r'create_subscription\s*<'),
    re.compile(r'create_service\s*\('),
    re.compile(r'create_service\s*<'),
    re.compile(r'create_wall_timer\s*\('),
    re.compile(r'ActionServer\s*\('),
    re.compile(r'rclcpp_action::create_server\s*<'),
]


@dataclass
class SourceNode:
    name: str
    file: Path
    text: str
    lines: List[str]


@dataclass
class WaitEdge:
    source: str
    target: str
    edge_type: str  # blocking | potential | executor_block
    deadlock_class: str  # communication | service_action | callback_blocking
    reason: str
    evidence: Dict[str, object]
    provenance: str = 'grounded'  # grounded | inferred
    confidence: str = 'high'  # high | medium | low
    definite_eligible: bool = True
    level: str = 'node'  # node | callback | resource
    relation: str = 'waits_for'  # waits_for | holds | requests | completion_depends_on | scheduled_by


@dataclass
class EndpointDecl:
    node: str
    kind: str  # service_client/service_server/action_client/action_server
    name: str
    line: int
    type_name: str


def _full(name: str) -> str:
    return name if name.startswith('/') else '/' + name


def _line_number(text: str, idx: int) -> int:
    return text.count('\n', 0, idx) + 1


def load_static_graph(path: Path) -> Dict:
    return json.loads(path.read_text(encoding='utf-8'))


def load_spec_semantics(path: Optional[Path]) -> Dict[str, Dict[str, object]]:
    if path is None or not path.exists():
        return {}
    try:
        spec = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}

    out: Dict[str, Dict[str, object]] = {}
    gs = spec.get('graph_seed', {})
    executors = []
    for ex in gs.get('executors', []):
        if not isinstance(ex, dict):
            continue
        ex_id = str(ex.get('id', '')).strip()
        if not ex_id:
            continue
        kind = str(ex.get('kind', 'unknown')).strip() or 'unknown'
        cap = ex.get('thread_capacity', 1)
        try:
            cap = int(cap)
        except Exception:
            cap = 1
        executors.append({'id': ex_id, 'kind': kind, 'thread_capacity': max(1, cap)})

    callback_groups = []
    for cg in gs.get('callback_groups', []):
        if not isinstance(cg, dict):
            continue
        cg_id = str(cg.get('id', '')).strip()
        node = str(cg.get('node', '')).strip()
        if not cg_id or not node:
            continue
        callback_groups.append(
            {
                'id': cg_id,
                'node': _full(node),
                'type': str(cg.get('type', 'MutuallyExclusive')),
                'executor': str(cg.get('executor', '')).strip(),
                'origin': str(cg.get('origin', 'explicit')).strip() or 'explicit',
            }
        )
    gs = spec.get('graph_seed', {})
    global_semantic_edges = [x for x in gs.get('semantic_edges', []) if isinstance(x, dict)]
    for s in gs.get('node_semantics', []):
        if not isinstance(s, dict):
            continue
        name = s.get('name')
        if not isinstance(name, str) or not name:
            continue
        node = _full(name)
        callbacks = []
        for cb in s.get('callbacks', []):
            if not isinstance(cb, dict):
                continue
            cb_id = str(cb.get('id', '')).strip()
            if not cb_id:
                continue
            callbacks.append(
                {
                    'id': cb_id,
                    'source': str(cb.get('source', 'unknown')),
                    'waits_for_callbacks': [str(x) for x in cb.get('waits_for_callbacks', []) if isinstance(x, str) and x],
                    'waits_for_services': [str(x) for x in cb.get('waits_for_services', []) if isinstance(x, str) and x],
                    'blocking_calls': [str(x) for x in cb.get('blocking_calls', []) if isinstance(x, str)],
                    'callback_group': str(cb.get('callback_group', '')).strip(),
                }
            )

        out[node] = {
            'executor': str(s.get('executor', 'unknown')),
            'callback_wait_edges': [x for x in s.get('callback_wait_edges', []) if isinstance(x, dict)],
            'semantic_edges': [x for x in s.get('semantic_edges', []) if isinstance(x, dict)],
            'callbacks': callbacks,
            'service_clients': [str(x) for x in s.get('service_clients', []) if isinstance(x, str)],
            'service_servers': [str(x) for x in s.get('service_servers', []) if isinstance(x, str)],
        }
    out['__global__'] = {
        'executors': executors,
        'callback_groups': callback_groups,
        'semantic_edges': global_semantic_edges,
    }
    return out


def discover_source_nodes(src_root: Path, graph_nodes: Set[str]) -> Dict[str, SourceNode]:
    nodes: Dict[str, SourceNode] = {}
    for ext in ('*.cpp', '*.hpp', '*.h', '*.py'):
        for fp in src_root.rglob(ext):
            try:
                text = fp.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            lines = text.splitlines()
            is_py = fp.suffix == '.py'
            re_ctor = PY_NODE_CTOR_RE if is_py else CPP_NODE_CTOR_RE
            for m in re_ctor.finditer(text):
                n = _full(m.group('name'))
                if n not in graph_nodes:
                    continue
                # Prefer file with richer code if duplicate node declarations exist.
                prev = nodes.get(n)
                if prev is None or len(text) > len(prev.text):
                    nodes[n] = SourceNode(name=n, file=fp, text=text, lines=lines)
    return nodes


def extract_endpoints(node: SourceNode) -> List[EndpointDecl]:
    text = node.text
    is_py = node.file.suffix == '.py'
    out: List[EndpointDecl] = []

    def add_matches(pattern: re.Pattern, kind: str) -> None:
        for m in pattern.finditer(text):
            out.append(
                EndpointDecl(
                    node=node.name,
                    kind=kind,
                    name=_full(m.group('name')),
                    line=_line_number(text, m.start()),
                    type_name=m.group('type').strip(),
                )
            )

    if is_py:
        add_matches(PY_SERVICE_CLIENT_RE, 'service_client')
        add_matches(PY_SERVICE_SERVER_RE, 'service_server')
        add_matches(PY_ACTION_CLIENT_RE, 'action_client')
        add_matches(PY_ACTION_SERVER_RE, 'action_server')
    else:
        add_matches(CPP_SERVICE_CLIENT_RE, 'service_client')
        add_matches(CPP_SERVICE_SERVER_RE, 'service_server')
        add_matches(CPP_ACTION_CLIENT_RE, 'action_client')
        add_matches(CPP_ACTION_SERVER_RE, 'action_server')

    return out


def blocking_lines(node: SourceNode) -> List[int]:
    lines: List[int] = []
    for i, line in enumerate(node.lines, start=1):
        if any(p.search(line) for p in BLOCKING_PATTERNS):
            lines.append(i)
    return lines


def callback_blocking_lines(node: SourceNode, window: int = 30) -> List[int]:
    lines = node.lines
    hits: Set[int] = set()
    for i, line in enumerate(lines):
        if not any(p.search(line) for p in CALLBACK_TRIGGER_PATTERNS):
            continue
        end = min(len(lines), i + 1 + window)
        for j in range(i, end):
            if any(p.search(lines[j]) for p in BLOCKING_PATTERNS):
                hits.add(j + 1)
    return sorted(hits)


def uses_single_thread_executor(node: SourceNode) -> bool:
    t = node.text
    return (
        'SingleThreadedExecutor' in t
        or 'rclpy.executors.SingleThreadedExecutor' in t
    )


def build_topic_maps(edges: List[Dict[str, str]]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    pub_by_node: Dict[str, Set[str]] = defaultdict(set)
    subs_by_topic: Dict[str, Set[str]] = defaultdict(set)
    for e in edges:
        kind = e.get('kind')
        src = e.get('from')
        dst = e.get('to')
        if kind == 'pub':
            pub_by_node[src].add(dst)
        elif kind == 'sub':
            subs_by_topic[src].add(dst)
    return pub_by_node, subs_by_topic


def _has_blocking_calls(calls: List[str]) -> bool:
    return any(str(c).strip() for c in calls)


def _blocking_primitives(calls: List[str]) -> Set[str]:
    out: Set[str] = set()
    for c in calls:
        cs = str(c or '').strip().lower()
        if not cs:
            continue
        if 'future_get' in cs or cs == 'future.get':
            out.add('future_get')
        elif 'spin_until_future_complete' in cs:
            out.add('spin_until_future_complete')
        elif 'wait_for_service' in cs:
            out.add('wait_for_service')
        elif 'condition_variable' in cs and 'wait' in cs:
            out.add('condition_variable_wait')
        elif cs == 'join' or cs.endswith('.join'):
            out.add('join')
        elif 'sleep' in cs:
            out.add('sleep')
        else:
            out.add(cs)
    return out


def _edge_type_from_reason_and_calls(reason: str, blocking_calls: List[str]) -> str:
    r = str(reason or '').lower()
    primitives = _blocking_primitives(blocking_calls)
    if 'future_get' in primitives or 'join' in primitives or 'condition_variable_wait' in primitives:
        return 'blocking'
    if 'spin_until_future_complete' in primitives:
        return 'potential'
    if 'wait_for_service' in primitives:
        return 'potential'
    if any(k in r for k in ('blocking', 'future', 'wait', 'service_call')):
        return 'blocking'
    return 'potential'


def _class_from_reason_and_source(reason: str, cb_source: str) -> str:
    r = str(reason or '').lower()
    source = str(cb_source or '').lower()
    if any(k in r for k in ('service', 'action')):
        return 'service_action'
    if any(k in r for k in ('topic', 'message', 'publish', 'subscribe', 'communication')):
        return 'communication'
    if source in ('subscription', 'topic'):
        return 'communication'
    return 'callback_blocking'


def _is_inferred_reason(reason: str) -> bool:
    r = str(reason or '').lower()
    return any(k in r for k in ('cyclic_dependency', 'semantic_callback_wait', 'inferred'))


def _is_callback_id(name: str) -> bool:
    n = str(name or '')
    return n.startswith('/') and ':' in n and not n.startswith('resource:')


def _normalize_semantic_endpoint(value: str, node_ctx: str = '') -> str:
    s = str(value or '').strip()
    if not s:
        return ''
    if s.startswith('resource:') or s.startswith('future:'):
        return s
    if s.startswith('/') and ':' in s:
        head, tail = s.split(':', 1)
        return f'{_full(head)}:{tail}'
    if (not s.startswith('/')) and ':' in s and node_ctx:
        # local callback endpoint like "cb_timer"
        first = s.split(':', 1)[0]
        if '/' not in first:
            return f'{node_ctx}:{s}'
    if s.startswith('/'):
        return _full(s)
    return _full(s)


def assess_cycles(
    nodes: Set[str],
    edges: List[Dict[str, object]],
    single_thread: Optional[Dict[str, bool]] = None,
) -> Dict[str, object]:
    if single_thread is None:
        single_thread = {}
    adj: Dict[str, Set[str]] = defaultdict(set)
    radj: Dict[str, Set[str]] = defaultdict(set)
    edge_types_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    edge_classes_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    edge_reasons_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    edge_relations_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    edge_provenance_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    edge_uncertain_by_pair: Dict[Tuple[str, str], bool] = defaultdict(bool)
    blocking_eligible_by_pair: Dict[Tuple[str, str], bool] = defaultdict(bool)

    for n in nodes:
        adj[n] = set()
        radj[n] = set()
    for e in edges:
        src = str(e.get('from', ''))
        dst = str(e.get('to', ''))
        if not src or not dst:
            continue
        adj[src].add(dst)
        radj[dst].add(src)
        pair = (src, dst)
        et = str(e.get('type', 'potential'))
        edge_types_by_pair[pair].add(et)
        edge_classes_by_pair[pair].add(str(e.get('deadlock_class', 'callback_blocking')))
        edge_reasons_by_pair[pair].add(str(e.get('reason', '')))
        edge_relations_by_pair[pair].add(str(e.get('relation', 'waits_for')))
        edge_provenance_by_pair[pair].add(str(e.get('provenance', 'inferred')))
        if bool(e.get('model_uncertainty', False)):
            edge_uncertain_by_pair[pair] = True
        if et == 'blocking' and bool(e.get('definite_eligible', True)):
            blocking_eligible_by_pair[pair] = True

    visited: Set[str] = set()
    order: List[str] = []

    def dfs1(u: str) -> None:
        visited.add(u)
        for v in adj[u]:
            if v not in visited:
                dfs1(v)
        order.append(u)

    for n in sorted(nodes):
        if n not in visited:
            dfs1(n)

    visited.clear()
    sccs: List[List[str]] = []

    def dfs2(u: str, comp: List[str]) -> None:
        visited.add(u)
        comp.append(u)
        for v in radj[u]:
            if v not in visited:
                dfs2(v, comp)

    for n in reversed(order):
        if n not in visited:
            comp: List[str] = []
            dfs2(n, comp)
            sccs.append(sorted(comp))

    def normalize_cycle(cycle_nodes: List[str]) -> Tuple[str, ...]:
        m = len(cycle_nodes)
        rots = [tuple(cycle_nodes[i:] + cycle_nodes[:i]) for i in range(m)]
        return min(rots)

    def enumerate_cycles_in_scc(comp_nodes: List[str], max_cycles: int = 2000) -> List[List[str]]:
        comp_set = set(comp_nodes)
        ordered = sorted(comp_nodes)
        rank = {n: i for i, n in enumerate(ordered)}
        seen: Set[Tuple[str, ...]] = set()
        out: List[List[str]] = []

        for start in ordered:
            stack = [start]
            seen_local = {start}

            def dfs(u: str) -> None:
                if len(out) >= max_cycles:
                    return
                for v in sorted(adj[u]):
                    if v not in comp_set:
                        continue
                    if v == start and len(stack) >= 2:
                        cyc = stack.copy()
                        key = normalize_cycle(cyc)
                        if key not in seen:
                            seen.add(key)
                            out.append(cyc)
                        continue
                    if v in seen_local or rank[v] < rank[start]:
                        continue
                    seen_local.add(v)
                    stack.append(v)
                    dfs(v)
                    stack.pop()
                    seen_local.remove(v)

            dfs(start)
            if len(out) >= max_cycles:
                break
        return out

    deadlock_definite = []
    deadlock_potential = []
    cycle_candidates = []

    def cycle_signature(entry: Dict[str, object]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        semantic_nodes: List[str] = []
        for n in entry.get('nodes', []):
            ns = str(n)
            if ns.startswith('resource:callback_group:'):
                semantic_nodes.append('resource:callback_group')
            elif ns.startswith('resource:executor_slot:'):
                semantic_nodes.append('resource:executor_slot')
            elif ns.startswith('future:'):
                # Futures are part of the core waiting chain, but collapse exact IDs.
                semantic_nodes.append('future:service_response')
            elif ns.startswith('/') and ':' in ns and not ns.endswith(':service_server'):
                semantic_nodes.append(f'{ns.split(":", 1)[0]}:callback')
            else:
                semantic_nodes.append(ns)
        return (tuple(sorted(set(semantic_nodes))), tuple(sorted(entry.get('deadlock_classes', []))))

    def cycle_rank(entry: Dict[str, object]) -> Tuple[int, int, int]:
        path = {'low': 0, 'medium': 1, 'high': 2}.get(str(entry.get('path_certainty', 'low')), 0)
        feas = {'unknown': 0, 'partial': 1, 'proven': 2}.get(str(entry.get('resource_feasibility', 'unknown')), 0)
        run = {'low': 0, 'medium': 1, 'high': 2}.get(str(entry.get('runtime_confirmability', 'low')), 0)
        return (path, feas, run)

    for comp in sccs:
        if len(comp) > 1:
            cycle_candidates.append({'nodes': comp, 'kind': 'scc'})
            cycles = enumerate_cycles_in_scc(comp)
        else:
            u = comp[0]
            if u in adj[u]:
                cycle_candidates.append({'nodes': comp, 'kind': 'self_loop'})
                cycles = [[u]]
            else:
                cycles = []

        for cyc_nodes in cycles:
            pair_seq = [(cyc_nodes[i], cyc_nodes[(i + 1) % len(cyc_nodes)]) for i in range(len(cyc_nodes))]
            hop_type_sets = [edge_types_by_pair.get(p, set()) for p in pair_seq]
            if any(not s for s in hop_type_sets):
                continue
            reasons: Set[str] = set()
            relations: Set[str] = set()
            provenances: Set[str] = set()
            has_uncertainty = False
            for p in pair_seq:
                reasons |= edge_reasons_by_pair.get(p, set())
                relations |= edge_relations_by_pair.get(p, set())
                provenances |= edge_provenance_by_pair.get(p, set())
                if edge_uncertain_by_pair.get(p, False):
                    has_uncertainty = True
            # Ignore SCCs made only by scheduling/structural links.
            if relations and relations.issubset({'scheduled_by', 'requests', 'holds'}):
                continue
            if 'waits_for' not in relations and 'completion_depends_on' not in relations:
                continue
            all_blocking = all('blocking' in s for s in hop_type_sets)
            all_blocking_eligible = all(blocking_eligible_by_pair.get(p, False) for p in pair_seq)
            hop_class_sets = [edge_classes_by_pair.get(p, set()) for p in pair_seq]
            cycle_classes = sorted(set.union(*hop_class_sets)) if hop_class_sets else []
            involved_all_single = all(single_thread.get(n, False) for n in set(cyc_nodes))
            has_holds = 'holds' in relations
            has_requests = 'requests' in relations
            has_waits = 'waits_for' in relations
            has_completion_dep = 'completion_depends_on' in relations

            if has_holds and has_requests and has_waits and has_completion_dep:
                resource_feasibility = 'proven'
            elif (has_waits and has_completion_dep) or (has_waits and has_requests):
                resource_feasibility = 'partial'
            else:
                resource_feasibility = 'unknown'

            has_inferred = 'inferred' in provenances
            if (not has_inferred) and (not has_uncertainty) and all_blocking_eligible:
                path_certainty = 'high'
            elif (not has_uncertainty):
                path_certainty = 'medium'
            else:
                path_certainty = 'low'

            if path_certainty == 'high' and resource_feasibility == 'proven':
                runtime_confirmability = 'high'
            elif resource_feasibility in ('proven', 'partial'):
                runtime_confirmability = 'medium'
            else:
                runtime_confirmability = 'low'

            certainty = 'potential'
            rule = 'contains_inferred_or_nonblocking_edges_or_unknown_resources'
            if all_blocking and all_blocking_eligible and (not has_inferred) and (not has_uncertainty) and resource_feasibility == 'proven':
                certainty = 'definite'
                rule = 'all_edges_blocking_grounded_and_resource_feasible'
            elif 'callback_blocking' in cycle_classes and involved_all_single and all_blocking_eligible:
                # Single-thread callback cycles are still potential if resource ownership is not explicit.
                certainty = 'potential'
                rule = 'single_thread_cycle_without_full_resource_proof'

            cycle_entry = {
                'nodes': cyc_nodes,
                'hops': [
                    {
                        'from': a,
                        'to': b,
                        'edge_types': sorted(list(edge_types_by_pair.get((a, b), set()))),
                        'deadlock_classes': sorted(list(edge_classes_by_pair.get((a, b), set()))),
                    }
                    for (a, b) in pair_seq
                ],
                'deadlock_classes': cycle_classes,
                'certainty': certainty,
                'rule': rule,
                'path_certainty': path_certainty,
                'resource_feasibility': resource_feasibility,
                'runtime_confirmability': runtime_confirmability,
                'relations': sorted(list(relations)),
            }
            if certainty == 'definite':
                deadlock_definite.append(cycle_entry)
            else:
                deadlock_potential.append(cycle_entry)

    def _merge_cycles(cycles: List[Dict[str, object]]) -> List[Dict[str, object]]:
        merged: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], Dict[str, object]] = {}
        for c in cycles:
            sig = cycle_signature(c)
            prev = merged.get(sig)
            if prev is None or cycle_rank(c) > cycle_rank(prev):
                merged[sig] = c
        return sorted(
            merged.values(),
            key=lambda x: (
                {'high': 2, 'medium': 1, 'low': 0}.get(str(x.get('path_certainty', 'low')), 0),
                {'proven': 2, 'partial': 1, 'unknown': 0}.get(str(x.get('resource_feasibility', 'unknown')), 0),
                len(x.get('nodes', [])),
            ),
            reverse=True,
        )

    deadlock_definite = _merge_cycles(deadlock_definite)
    deadlock_potential = _merge_cycles(deadlock_potential)
    overall = (
        'definite_deadlock'
        if deadlock_definite
        else ('potential_deadlock' if deadlock_potential else 'no_deadlock_cycle')
    )

    return {
        'cycle_candidates': cycle_candidates,
        'definite_deadlocks': deadlock_definite,
        'potential_deadlocks': deadlock_potential,
        'overall': overall,
        'edge_types_by_pair': edge_types_by_pair,
        'edge_classes_by_pair': edge_classes_by_pair,
    }


def build_wait_for_graph(
    graph: Dict,
    src_nodes: Dict[str, SourceNode],
    spec_semantics: Optional[Dict[str, Dict[str, object]]] = None,
    use_llm_relations: bool = False,
    full_output: bool = False,
) -> Dict:
    if spec_semantics is None:
        spec_semantics = {}
    spec_globals = spec_semantics.get('__global__', {})
    spec_nodes = {k: v for k, v in spec_semantics.items() if isinstance(k, str) and k.startswith('/')}
    nodes: Set[str] = set(graph.get('nodes', []))
    edges_comm: List[Dict[str, str]] = graph.get('edges', [])

    pub_by_node, subs_by_topic = build_topic_maps(edges_comm)

    endpoints: List[EndpointDecl] = []
    b_lines: Dict[str, List[int]] = {}
    cb_b_lines: Dict[str, List[int]] = {}
    single_thread: Dict[str, bool] = {}

    for n, src in src_nodes.items():
        eps = extract_endpoints(src)
        endpoints.extend(eps)
        b_lines[n] = blocking_lines(src)
        cb_b_lines[n] = callback_blocking_lines(src)
        single_thread[n] = uses_single_thread_executor(src)

    # Semantics from spec are static metadata and can complement source-only inference.
    for n in nodes:
        sem = spec_nodes.get(n, {})
        ex = str(sem.get('executor', 'unknown')).lower()
        if ex in ('single', 'singlethreaded', 'single_threaded'):
            single_thread[n] = True
        elif ex in ('multi', 'multithreaded', 'multi_threaded') and n not in single_thread:
            single_thread[n] = False

    service_servers: Dict[str, Set[str]] = defaultdict(set)
    action_servers: Dict[str, Set[str]] = defaultdict(set)
    for ep in endpoints:
        if ep.kind == 'service_server':
            service_servers[ep.name].add(ep.node)
        elif ep.kind == 'action_server':
            action_servers[ep.name].add(ep.node)
    for node_name, sem in spec_nodes.items():
        if node_name not in nodes:
            continue
        for srv in sem.get('service_servers', []):
            service_servers[_full(srv)].add(node_name)

    callback_meta_by_node: Dict[str, Dict[str, Dict[str, object]]] = defaultdict(dict)
    for node_name, sem in spec_nodes.items():
        if node_name not in nodes:
            continue
        for cb in sem.get('callbacks', []):
            cb_id = str(cb.get('id', '')).strip()
            if not cb_id:
                continue
            callback_meta_by_node[node_name][cb_id] = cb

    communication_trigger_edges: List[Dict[str, str]] = []
    for src in sorted(nodes):
        for topic in sorted(pub_by_node.get(src, set())):
            for dst in sorted(subs_by_topic.get(topic, set())):
                if dst not in nodes:
                    continue
                communication_trigger_edges.append(
                    {
                        'from': src,
                        'to': dst,
                        'topic': topic,
                        'kind': 'pub_sub_trigger',
                    }
                )

    wait_edges: List[WaitEdge] = []
    callback_wait_graph: List[Dict[str, object]] = []
    executor_semantics: List[Dict[str, object]] = []

    def add_edge(
        src: str,
        dst: str,
        edge_type: str,
        deadlock_class: str,
        reason: str,
        evidence: Dict[str, object],
        allow_self: bool = False,
        provenance: str = 'grounded',
        confidence: str = 'high',
        definite_eligible: bool = True,
        level: str = 'node',
        relation: str = 'waits_for',
    ) -> None:
        if src == dst and not allow_self:
            return
        wait_edges.append(
            WaitEdge(
                source=src,
                target=dst,
                edge_type=edge_type,
                deadlock_class=deadlock_class,
                reason=reason,
                evidence=evidence,
                provenance=provenance,
                confidence=confidence,
                definite_eligible=definite_eligible,
                level=level,
                relation=relation,
            )
        )

    # Rule 1: blocking service/action waits
    for ep in endpoints:
        src = ep.node
        if src not in nodes:
            continue
        if not b_lines.get(src):
            continue

        if ep.kind == 'service_client':
            for dst in sorted(service_servers.get(ep.name, set())):
                add_edge(
                    src,
                    dst,
                    'blocking',
                    'service_action',
                    'blocking_service_call',
                    {
                        'name': ep.name,
                        'client_file': str(src_nodes[src].file),
                        'client_line': ep.line,
                        'blocking_lines': b_lines[src],
                    },
                )
        elif ep.kind == 'action_client':
            for dst in sorted(action_servers.get(ep.name, set())):
                add_edge(
                    src,
                    dst,
                    'blocking',
                    'service_action',
                    'blocking_action_wait',
                    {
                        'name': ep.name,
                        'client_file': str(src_nodes[src].file),
                        'client_line': ep.line,
                        'blocking_lines': b_lines[src],
                    },
                )

    # Rule 2: callback-level potential waiting via pub->sub dependencies
    for src in sorted(nodes):
        if not cb_b_lines.get(src):
            continue
        for topic in sorted(pub_by_node.get(src, set())):
            for dst in sorted(subs_by_topic.get(topic, set())):
                if dst not in nodes:
                    continue
                add_edge(
                    src,
                    dst,
                    'potential',
                    'communication',
                    'blocking_callback_with_published_topic_dependency',
                    {
                        'topic': topic,
                        'file': str(src_nodes[src].file) if src in src_nodes else None,
                        'blocking_callback_lines': cb_b_lines.get(src, []),
                    },
                    allow_self=True,
                    provenance='inferred',
                    confidence='medium',
                    definite_eligible=False,
                )

    # Rule 3: single-thread executor amplified blocking risk
    for src in sorted(nodes):
        if not single_thread.get(src, False):
            continue
        if not cb_b_lines.get(src):
            continue
        for topic in sorted(pub_by_node.get(src, set())):
            for dst in sorted(subs_by_topic.get(topic, set())):
                if dst not in nodes:
                    continue
                add_edge(
                    src,
                    dst,
                    'executor_block',
                    'callback_blocking',
                    'single_thread_executor_with_blocking_callback',
                    {
                        'topic': topic,
                        'file': str(src_nodes[src].file) if src in src_nodes else None,
                        'blocking_callback_lines': cb_b_lines.get(src, []),
                    },
                    allow_self=True,
                    provenance='grounded',
                    confidence='medium',
                    definite_eligible=True,
                )

    # Rule 4: static semantic callback waits from spec (still static, not runtime data).
    service_owner: Dict[str, Set[str]] = defaultdict(set)
    for ep in endpoints:
        if ep.kind == 'service_server':
            service_owner[ep.name].add(ep.node)
    for node_name, sem in spec_nodes.items():
        if node_name not in nodes:
            continue
        for srv in sem.get('service_servers', []):
            service_owner[_full(srv)].add(node_name)
    for node_name, sem in spec_nodes.items():
        if node_name not in nodes:
            continue
        if use_llm_relations:
            for cwe in sem.get('callback_wait_edges', []):
                src_cb = str(cwe.get('from', f'{node_name}:callback'))
                dst_cb = cwe.get('to')
                if not isinstance(dst_cb, str) or not dst_cb:
                    continue
                src_node = _full(src_cb.split(':', 1)[0])
                dst_node = _full(dst_cb.split(':', 1)[0])
                if src_node not in nodes or dst_node not in nodes:
                    continue
                reason = str(cwe.get('reason', 'semantic_callback_wait'))
                src_cb_id = src_cb.split(':', 1)[1] if ':' in src_cb else ''
                src_cb_meta = callback_meta_by_node.get(src_node, {}).get(src_cb_id, {})
                src_cb_blocking_calls = [str(x) for x in src_cb_meta.get('blocking_calls', []) if isinstance(x, str)]
                src_cb_source = str(src_cb_meta.get('source', 'unknown'))
                edge_type = _edge_type_from_reason_and_calls(reason, src_cb_blocking_calls)
                deadlock_class = _class_from_reason_and_source(reason, src_cb_source)
                inferred = _is_inferred_reason(reason)
                provenance = 'inferred' if inferred else 'grounded'
                confidence = 'low' if inferred else ('medium' if edge_type == 'potential' else 'high')
                definite_eligible = not inferred and edge_type == 'blocking'
                callback_wait_graph.append(
                    {
                        'from': src_cb,
                        'to': dst_cb,
                        'type': edge_type,
                        'deadlock_class': deadlock_class,
                        'reason': reason,
                        'source': 'llm_spec',
                        'provenance': provenance,
                        'confidence': confidence,
                        'definite_eligible': definite_eligible,
                        'relation': 'waits_for',
                        'model_uncertainty': False,
                    }
                )
                add_edge(
                    src_node,
                    dst_node,
                    edge_type,
                    deadlock_class,
                    reason,
                    {'source': 'llm_spec_callback_wait_edges'},
                    allow_self=(src_node == dst_node),
                    provenance=provenance,
                    confidence=confidence,
                    definite_eligible=definite_eligible,
                    relation='waits_for',
                )

        for cb in sem.get('callbacks', []):
            if not isinstance(cb, dict):
                continue
            cb_id = str(cb.get('id', '')).strip()
            if not cb_id:
                continue
            src_cb = f'{node_name}:{cb_id}'
            cb_blocking_calls = [str(x) for x in cb.get('blocking_calls', []) if isinstance(x, str)]
            cb_source = str(cb.get('source', 'unknown'))
            if use_llm_relations:
                for dst_cb in cb.get('waits_for_callbacks', []):
                    if not isinstance(dst_cb, str) or not dst_cb:
                        continue
                    dst_node = _full(dst_cb.split(':', 1)[0])
                    if dst_node not in nodes:
                        continue
                    primitives = _blocking_primitives(cb_blocking_calls)
                    if 'future_get' in primitives or 'join' in primitives or 'condition_variable_wait' in primitives:
                        cb_edge_type = 'blocking'
                        cb_definite_eligible = True
                    elif 'spin_until_future_complete' in primitives:
                        cb_edge_type = 'potential'
                        cb_definite_eligible = False
                    else:
                        cb_edge_type = 'potential'
                        cb_definite_eligible = False
                    cb_deadlock_class = 'communication' if cb_source == 'subscription' else 'callback_blocking'
                    callback_wait_graph.append(
                        {
                            'from': src_cb,
                            'to': dst_cb,
                            'type': cb_edge_type,
                            'deadlock_class': cb_deadlock_class,
                            'reason': 'callback_waits_for_callback',
                            'source': 'llm_spec',
                            'provenance': 'inferred' if cb_edge_type == 'potential' else 'grounded',
                            'confidence': 'low' if cb_edge_type == 'potential' else 'high',
                            'definite_eligible': cb_definite_eligible,
                            'relation': 'waits_for',
                            'model_uncertainty': False,
                        }
                    )
                    add_edge(
                        node_name,
                        dst_node,
                        cb_edge_type,
                        cb_deadlock_class,
                        'callback_waits_for_callback',
                        {'source': 'llm_spec_callbacks'},
                        allow_self=(node_name == dst_node),
                        provenance='inferred' if cb_edge_type == 'potential' else 'grounded',
                        confidence='low' if cb_edge_type == 'potential' else 'high',
                        definite_eligible=cb_definite_eligible,
                        relation='waits_for',
                    )

            for srv in cb.get('waits_for_services', []):
                srv_full = _full(srv)
                owners = service_owner.get(srv_full, set())
                primitives = _blocking_primitives(cb_blocking_calls)
                uses_wait_for_service = 'wait_for_service' in primitives
                uses_future_get = 'future_get' in primitives
                uses_spin = 'spin_until_future_complete' in primitives
                if uses_wait_for_service and not (uses_future_get or uses_spin):
                    callback_wait_graph.append(
                        {
                            'from': src_cb,
                            'to': f'resource:service_availability:{srv_full}',
                            'type': 'potential',
                            'deadlock_class': 'service_action',
                            'reason': 'wait_for_service_availability',
                            'service': srv_full,
                            'source': 'llm_spec',
                            'provenance': 'grounded',
                            'confidence': 'medium',
                            'definite_eligible': False,
                            'relation': 'waits_for',
                            'model_uncertainty': False,
                        }
                    )
                    continue
                for dst_node in sorted(owners):
                    et = 'blocking' if uses_future_get else 'potential'
                    de = bool(uses_future_get)
                    callback_wait_graph.append(
                        {
                            'from': src_cb,
                            'to': f'{dst_node}:service_server',
                            'type': et,
                            'deadlock_class': 'service_action',
                            'reason': 'service_call',
                            'service': srv_full,
                            'source': 'llm_spec',
                            'provenance': 'grounded' if uses_future_get else 'inferred',
                            'confidence': 'high' if uses_future_get else ('medium' if uses_spin else 'low'),
                            'definite_eligible': de,
                            'relation': 'waits_for',
                            'model_uncertainty': False,
                        }
                    )
                    add_edge(
                        node_name,
                        dst_node,
                        et,
                        'service_action',
                        'service_call',
                        {'source': 'llm_spec_callbacks', 'service': srv_full},
                        allow_self=(node_name == dst_node),
                        provenance='grounded' if uses_future_get else 'inferred',
                        confidence='high' if uses_future_get else ('medium' if uses_spin else 'low'),
                        definite_eligible=de,
                        relation='waits_for',
                    )

        # Rule 4b: fallback service waits from semantic node-level clients.
        sem_blocking_calls = [str(x) for x in sem.get('blocking_calls', []) if isinstance(x, str)]
        sem_service_clients = [_full(str(x)) for x in sem.get('service_clients', []) if isinstance(x, str)]
        sem_primitives = _blocking_primitives(sem_blocking_calls)
        if _has_blocking_calls(sem_blocking_calls):
            for srv in sem_service_clients:
                for dst_node in sorted(service_owner.get(srv, set())):
                    if 'future_get' in sem_primitives:
                        et = 'blocking'
                        de = True
                        confidence = 'high'
                        provenance = 'grounded'
                    elif 'spin_until_future_complete' in sem_primitives:
                        et = 'potential'
                        de = False
                        confidence = 'medium'
                        provenance = 'inferred'
                    elif 'wait_for_service' in sem_primitives:
                        et = 'potential'
                        de = False
                        confidence = 'medium'
                        provenance = 'grounded'
                    else:
                        et = 'potential'
                        de = False
                        confidence = 'low'
                        provenance = 'inferred'
                    add_edge(
                        node_name,
                        dst_node,
                        et,
                        'service_action',
                        'semantic_blocking_service_client',
                        {'source': 'llm_spec_node_semantics', 'service': srv},
                        allow_self=(node_name == dst_node),
                        provenance=provenance,
                        confidence=confidence,
                        definite_eligible=de,
                    )

    # Deduplicate identical edges by (src,dst,type,reason,name/topic)
    unique = {}
    for e in wait_edges:
        key_basis = e.evidence.get('name') or e.evidence.get('topic') or e.evidence.get('service') or ''
        key = (
            e.source,
            e.target,
            e.edge_type,
            e.deadlock_class,
            e.reason,
            str(key_basis),
            e.provenance,
            e.definite_eligible,
        )
        if key not in unique:
            unique[key] = e
    wait_edges = list(unique.values())

    # Prefer stronger edge type for same pair/class: blocking > executor_block > potential.
    rank = {'blocking': 3, 'executor_block': 2, 'potential': 1}
    pair_best: Dict[Tuple[str, str, str], WaitEdge] = {}
    for e in wait_edges:
        k = (e.source, e.target, e.deadlock_class)
        prev = pair_best.get(k)
        score = (
            rank.get(e.edge_type, 0),
            1 if e.definite_eligible else 0,
            1 if e.provenance == 'grounded' else 0,
            {'low': 0, 'medium': 1, 'high': 2}.get(e.confidence, 0),
        )
        prev_score = (
            rank.get(prev.edge_type, 0),
            1 if prev.definite_eligible else 0,
            1 if prev.provenance == 'grounded' else 0,
            {'low': 0, 'medium': 1, 'high': 2}.get(prev.confidence, 0),
        ) if prev is not None else (-1, -1, -1, -1)
        if prev is None or score > prev_score:
            pair_best[k] = e
    wait_edges = list(pair_best.values())

    # Executor semantics output for explainability.
    for n in sorted(nodes):
        executor_semantics.append(
            {
                'node': n,
                'executor': 'single_threaded' if single_thread.get(n, False) else 'unknown_or_multi',
                'source_file': str(src_nodes[n].file) if n in src_nodes else None,
            }
        )

    # Deduplicate callback graph (purely informative).
    cb_unique = {}
    for e in callback_wait_graph:
        key = (
            e.get('from'),
            e.get('to'),
            e.get('type'),
            e.get('deadlock_class'),
            e.get('reason'),
            e.get('provenance'),
            e.get('definite_eligible'),
        )
        if key not in cb_unique:
            cb_unique[key] = e
    callback_wait_graph = sorted(
        cb_unique.values(),
        key=lambda x: (x.get('from', ''), x.get('to', ''), x.get('type', ''), x.get('reason', '')),
    )

    # Callback/resource-level graph is the primary graph for deadlock assessment.
    callback_wait_edges = []
    callback_nodes: Set[str] = set()
    callback_single_thread: Dict[str, bool] = {}
    for node_name, sem in spec_nodes.items():
        for cb in sem.get('callbacks', []):
            cb_id = str(cb.get('id', '')).strip()
            if not cb_id:
                continue
            cb_full = f'{node_name}:{cb_id}'
            callback_nodes.add(cb_full)
            callback_single_thread[cb_full] = bool(single_thread.get(node_name, False))

    for e in callback_wait_graph:
        src = str(e.get('from', ''))
        dst = str(e.get('to', ''))
        if not src or not dst:
            continue
        if _is_callback_id(src):
            callback_nodes.add(src)
        if _is_callback_id(dst):
            callback_nodes.add(dst)
        callback_wait_edges.append(
            {
                'from': src,
                'to': dst,
                'type': str(e.get('type', 'potential')),
                'deadlock_class': str(e.get('deadlock_class', 'callback_blocking')),
                'reason': str(e.get('reason', '')),
                'definite_eligible': bool(e.get('definite_eligible', False)),
                'provenance': str(e.get('provenance', 'inferred')),
                'relation': str(e.get('relation', 'waits_for')),
                'model_uncertainty': bool(e.get('model_uncertainty', False)),
            }
        )

    # Build resource nodes (executor slots + callback groups + service availability).
    executors = {str(x.get('id')): x for x in spec_globals.get('executors', []) if isinstance(x, dict) and x.get('id')}
    callback_groups = {str(x.get('id')): x for x in spec_globals.get('callback_groups', []) if isinstance(x, dict) and x.get('id')}
    default_executor_by_node: Dict[str, str] = {}
    default_group_by_node: Dict[str, str] = {}
    for n in sorted(nodes):
        ex_id = f'exec::unknown::{n}'
        if ex_id not in executors:
            executors[ex_id] = {
                'id': ex_id,
                'kind': 'UnknownExecutor',
                'thread_capacity': 1 if single_thread.get(n, False) else 2,
                'origin': 'inferred_node_local',
            }
        default_executor_by_node[n] = ex_id
        cg_id = f'cg::{n}::default'
        if cg_id not in callback_groups:
            callback_groups[cg_id] = {
                'id': cg_id,
                'node': n,
                'type': 'Unknown',
                'executor': ex_id,
                'origin': 'implicit_default',
            }
        default_group_by_node[n] = cg_id

    callback_to_group: Dict[str, str] = {}
    callback_to_exec: Dict[str, str] = {}
    callback_to_calls: Dict[str, Set[str]] = {}
    callback_to_services: Dict[str, List[str]] = {}
    service_owner_callbacks: Dict[str, Set[str]] = defaultdict(set)
    for node_name, sem in spec_nodes.items():
        for cb in sem.get('callbacks', []):
            cb_id = str(cb.get('id', '')).strip()
            if not cb_id:
                continue
            cb_full = f'{node_name}:{cb_id}'
            cg = str(cb.get('callback_group', '')).strip() or default_group_by_node.get(node_name, f'cg::{node_name}::default')
            callback_to_group[cb_full] = cg
            callback_to_exec[cb_full] = str(callback_groups.get(cg, {}).get('executor') or default_executor_by_node.get(node_name, f'exec::{node_name}'))
            callback_to_calls[cb_full] = _blocking_primitives([str(x) for x in cb.get('blocking_calls', []) if isinstance(x, str)])
            callback_to_services[cb_full] = [_full(str(x)) for x in cb.get('waits_for_services', []) if isinstance(x, str)]
            if str(cb.get('source', '')).strip().lower() == 'service':
                for srv in sem.get('service_servers', []):
                    service_owner_callbacks[_full(str(srv))].add(cb_full)

    callback_groups_out = sorted(
        [
            {
                'id': str(v.get('id')),
                'node': str(v.get('node', '')),
                'type': str(v.get('type', 'Unknown')),
                'executor': str(v.get('executor', '')),
                'origin': str(v.get('origin', 'explicit')),
            }
            for v in callback_groups.values()
        ],
        key=lambda x: x['id'],
    )
    executors_out = sorted(
        [
            {
                'id': str(v.get('id')),
                'kind': str(v.get('kind', 'UnknownExecutor')),
                'thread_capacity': v.get('thread_capacity', None),
                'origin': str(v.get('origin', 'explicit')),
            }
            for v in executors.values()
        ],
        key=lambda x: x['id'],
    )
    assignments_out = sorted(
        [
            {
                'callback_group': cg['id'],
                'executor': cg.get('executor', ''),
                'origin': 'explicit' if cg.get('origin', 'explicit') == 'explicit' else 'inferred',
            }
            for cg in callback_groups_out
        ],
        key=lambda x: (x['callback_group'], x['executor']),
    )

    callback_resource_edges: List[Dict[str, object]] = []
    # Ingest explicit semantic edges from spec (node-level + graph-level).
    semantic_edges_raw: List[Tuple[str, Dict[str, object]]] = []
    for node_name, sem in spec_nodes.items():
        for se in sem.get('semantic_edges', []):
            if isinstance(se, dict):
                semantic_edges_raw.append((node_name, se))
    for se in spec_globals.get('semantic_edges', []):
        if isinstance(se, dict):
            semantic_edges_raw.append(('', se))

    allowed_relations = {'holds', 'waits_for', 'requests', 'completion_depends_on', 'scheduled_by'}
    semantic_edges_ingested = 0
    for node_ctx, se in semantic_edges_raw:
        src = _normalize_semantic_endpoint(se.get('from', ''), node_ctx=node_ctx)
        dst = _normalize_semantic_endpoint(se.get('to', ''), node_ctx=node_ctx)
        if not src or not dst:
            continue
        rel = str(se.get('relation', 'waits_for'))
        if rel not in allowed_relations:
            rel = 'waits_for'
        et = str(se.get('type', 'potential'))
        if et not in ('blocking', 'potential', 'executor_block'):
            et = 'potential'
        prov = str(se.get('provenance', 'grounded')) or 'grounded'
        confidence = str(se.get('confidence', 'medium')).lower()
        de = bool(se.get('definite_eligible', False))
        if not de and et == 'blocking' and prov == 'grounded' and confidence == 'high':
            # Safe upgrade if edge carries strong grounded blocking evidence.
            de = True
        callback_resource_edges.append(
            {
                'from': src,
                'to': dst,
                'relation': rel,
                'type': et,
                'deadlock_class': str(se.get('deadlock_class', 'callback_blocking')),
                'reason': str(se.get('reason', 'semantic_edge')),
                'definite_eligible': de,
                'provenance': prov,
                'model_uncertainty': bool(se.get('model_uncertainty', False) or (confidence in ('low', 'unknown'))),
            }
        )
        semantic_edges_ingested += 1
        if _is_callback_id(src):
            callback_nodes.add(src)
        if _is_callback_id(dst):
            callback_nodes.add(dst)
    resource_nodes: Set[str] = set()
    for cb_full in sorted(callback_nodes):
        node_name = _full(cb_full.split(':', 1)[0]) if ':' in cb_full else ''
        cg = callback_to_group.get(cb_full, default_group_by_node.get(node_name, f'cg::{node_name}::default'))
        ex = callback_to_exec.get(cb_full, str(callback_groups.get(cg, {}).get('executor') or default_executor_by_node.get(node_name, f'exec::{node_name}')))
        cg_res = f'resource:callback_group:{cg}'
        ex_res = f'resource:executor_slot:{ex}'
        resource_nodes.add(cg_res)
        resource_nodes.add(ex_res)
        cg_meta = callback_groups.get(cg, {})
        ex_meta = executors.get(ex, {})
        cg_unknown = str(cg_meta.get('type', 'Unknown')) == 'Unknown' or str(cg_meta.get('origin', 'implicit_default')) != 'explicit'
        ex_unknown = str(ex_meta.get('kind', 'UnknownExecutor')) == 'UnknownExecutor'
        model_uncertainty = bool(cg_unknown or ex_unknown)
        callback_resource_edges.append(
            {
                'from': cb_full,
                'to': cg_res,
                'relation': 'requests',
                'type': 'potential',
                'deadlock_class': 'callback_blocking',
                'reason': 'requests_callback_group',
                'definite_eligible': False,
                'provenance': 'grounded',
                'model_uncertainty': model_uncertainty,
            }
        )
        callback_resource_edges.append(
            {
                'from': cb_full,
                'to': ex_res,
                'relation': 'scheduled_by',
                'type': 'potential',
                'deadlock_class': 'callback_blocking',
                'reason': 'scheduled_by_executor',
                'definite_eligible': False,
                'provenance': 'grounded',
                'model_uncertainty': model_uncertainty,
            }
        )
        calls = callback_to_calls.get(cb_full, set())
        if calls:
            callback_resource_edges.append(
                {
                    'from': cg_res,
                    'to': cb_full,
                    'relation': 'holds',
                    'type': 'potential',
                    'deadlock_class': 'callback_blocking',
                    'reason': 'holds_callback_group_while_blocked',
                    'definite_eligible': False,
                    'provenance': 'grounded',
                    'model_uncertainty': model_uncertainty,
                }
            )
            callback_resource_edges.append(
                {
                    'from': ex_res,
                    'to': cb_full,
                    'relation': 'holds',
                    'type': 'potential',
                    'deadlock_class': 'callback_blocking',
                    'reason': 'holds_executor_slot_while_blocked',
                    'definite_eligible': False,
                    'provenance': 'grounded',
                    'model_uncertainty': model_uncertainty,
                }
            )
        if ('wait_for_service' in calls) or ('future_get' in calls) or ('spin_until_future_complete' in calls):
            for srv in callback_to_services.get(cb_full, []):
                fut = f'future:service_response:{srv}:{cb_full}'
                resource_nodes.add(fut)
                if 'future_get' in calls:
                    et = 'blocking'
                    de = True
                    conf = 'grounded'
                else:
                    et = 'potential'
                    de = False
                    conf = 'grounded'
                callback_resource_edges.append(
                    {
                        'from': cb_full,
                        'to': fut,
                        'relation': 'waits_for',
                        'type': et,
                        'deadlock_class': 'service_action',
                        'reason': 'waits_for_service_future',
                        'definite_eligible': de,
                        'provenance': conf,
                        'model_uncertainty': model_uncertainty,
                    }
                )
                owners = sorted(service_owner_callbacks.get(srv, set()))
                if not owners:
                    owners = [f'{owner}:service_server' for owner in sorted(service_owner.get(srv, set()))]
                for owner_cb in owners:
                    callback_resource_edges.append(
                        {
                            'from': fut,
                            'to': owner_cb,
                            'relation': 'completion_depends_on',
                            'type': 'potential',
                            'deadlock_class': 'service_action',
                            'reason': 'future_completion_depends_on_service_callback',
                            'definite_eligible': False,
                            'provenance': 'grounded',
                            'model_uncertainty': model_uncertainty,
                        }
                    )

    callback_resource_edges.extend(callback_wait_edges)
    callback_resource_unique = {}
    for e in callback_resource_edges:
        key = (
            e.get('from'),
            e.get('to'),
            e.get('relation'),
            e.get('type'),
            e.get('deadlock_class'),
            e.get('reason'),
            e.get('definite_eligible'),
            e.get('provenance'),
            e.get('model_uncertainty', False),
        )
        if key not in callback_resource_unique:
            callback_resource_unique[key] = e
    callback_resource_edges = list(callback_resource_unique.values())
    callback_resource_nodes: Set[str] = set()
    for e in callback_resource_edges:
        callback_resource_nodes.add(str(e.get('from', '')))
        callback_resource_nodes.add(str(e.get('to', '')))

    node_assessment = assess_cycles(nodes, [
        {
            'from': e.source,
            'to': e.target,
            'type': e.edge_type,
            'deadlock_class': e.deadlock_class,
            'definite_eligible': e.definite_eligible,
        }
        for e in wait_edges
    ], single_thread=single_thread)
    callback_resource_assessment = assess_cycles(callback_resource_nodes, callback_resource_edges, single_thread=callback_single_thread)

    deadlock_definite = callback_resource_assessment['definite_deadlocks']
    deadlock_potential = callback_resource_assessment['potential_deadlocks']
    cycle_candidates = callback_resource_assessment['cycle_candidates']

    outgoing_by_src: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    incoming_by_dst: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for e in callback_resource_edges:
        outgoing_by_src[str(e.get('from', ''))].append(e)
        incoming_by_dst[str(e.get('to', ''))].append(e)

    structural_patterns: List[Dict[str, object]] = []
    seen_pattern_keys: Set[Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = set()
    for cb, out_edges in outgoing_by_src.items():
        if ':' not in cb or not cb.startswith('/'):
            continue
        wait_edges_cb = [e for e in out_edges if str(e.get('relation', '')) == 'waits_for']
        resource_edges_cb = [e for e in out_edges if str(e.get('relation', '')) in {'requests', 'scheduled_by'}]
        hold_edges_cb = [e for e in incoming_by_dst.get(cb, []) if str(e.get('relation', '')) == 'holds']
        if not wait_edges_cb or not (resource_edges_cb or hold_edges_cb):
            continue
        wait_targets = sorted({str(e.get('to', '')) for e in wait_edges_cb if str(e.get('to', ''))})
        resources = sorted(
            {str(e.get('to', '')) for e in resource_edges_cb if str(e.get('to', '')).startswith('resource:')} |
            {str(e.get('from', '')) for e in hold_edges_cb if str(e.get('from', '')).startswith('resource:')}
        )
        deadlock_classes = sorted({str(e.get('deadlock_class', 'callback_blocking')) for e in wait_edges_cb + resource_edges_cb + hold_edges_cb})
        relations = sorted({str(e.get('relation', '')) for e in wait_edges_cb + resource_edges_cb + hold_edges_cb})
        key = (cb, tuple(wait_targets), tuple(resources))
        if key in seen_pattern_keys:
            continue
        seen_pattern_keys.add(key)
        grounded_blocking = any(
            str(e.get('type', '')) == 'blocking' and str(e.get('provenance', 'grounded')) == 'grounded'
            for e in wait_edges_cb
        )
        structural_patterns.append(
            {
                'nodes': [cb] + wait_targets[:2] + resources[:2],
                'deadlock_classes': deadlock_classes,
                'certainty': 'potential',
                'rule': 'blocking_callback_holds_resources_while_waiting',
                'path_certainty': 'medium' if grounded_blocking else 'low',
                'resource_feasibility': 'partial' if resources else 'unknown',
                'runtime_confirmability': 'medium' if grounded_blocking else 'low',
                'relations': relations,
            }
        )

    def _pattern_signature(entry: Dict[str, object]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        norm_nodes: List[str] = []
        for x in entry.get('nodes', []):
            ns = str(x)
            if ns.startswith('future:'):
                norm_nodes.append('future:service_response')
            elif ns.startswith('resource:callback_group:'):
                norm_nodes.append('resource:callback_group')
            elif ns.startswith('resource:executor_slot:'):
                norm_nodes.append('resource:executor_slot')
            elif ns.startswith('/') and ':' in ns and not ns.endswith(':service_server'):
                norm_nodes.append(f'{ns.split(":", 1)[0]}:callback')
            else:
                norm_nodes.append(ns)
        return (tuple(sorted(set(norm_nodes))), tuple(sorted(entry.get('deadlock_classes', []))))

    def _deadlock_rank(entry: Dict[str, object]) -> Tuple[int, int, int]:
        path = {'low': 0, 'medium': 1, 'high': 2}.get(str(entry.get('path_certainty', 'low')), 0)
        feas = {'unknown': 0, 'partial': 1, 'proven': 2}.get(str(entry.get('resource_feasibility', 'unknown')), 0)
        run = {'low': 0, 'medium': 1, 'high': 2}.get(str(entry.get('runtime_confirmability', 'low')), 0)
        return (path, feas, run)

    def _callback_cluster_signature(entry: Dict[str, object]) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
        callbacks: Set[str] = set()
        services: Set[str] = set()
        resources: Set[str] = set()
        for x in entry.get('nodes', []):
            ns = str(x)
            if ns.startswith('future:'):
                services.add('future')
            elif ns.startswith('resource:callback_group:'):
                resources.add('callback_group')
            elif ns.startswith('resource:executor_slot:'):
                resources.add('executor_slot')
            elif ns.startswith('/') and ':' in ns:
                callbacks.add(ns.split(':', 1)[0])
            elif ns.startswith('/'):
                callbacks.add(ns)
        return (tuple(sorted(callbacks)), tuple(sorted(services)), tuple(sorted(resources)))

    existing_potential = {_pattern_signature(x) for x in deadlock_potential}
    for pattern in structural_patterns:
        sig = _pattern_signature(pattern)
        if sig not in existing_potential:
            deadlock_potential.append(pattern)
            existing_potential.add(sig)
    clustered_potential: Dict[Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], Dict[str, object]] = {}
    for entry in deadlock_potential:
        sig = _callback_cluster_signature(entry)
        prev = clustered_potential.get(sig)
        if prev is None or _deadlock_rank(entry) > _deadlock_rank(prev):
            clustered_potential[sig] = entry
    deadlock_potential = sorted(
        clustered_potential.values(),
        key=_deadlock_rank,
        reverse=True,
    )
    overall_assessment = (
        'definite_deadlock'
        if deadlock_definite
        else ('potential_deadlock' if deadlock_potential else 'no_deadlock_cycle')
    )

    edge_type_counts: Dict[str, int] = defaultdict(int)
    for e in wait_edges:
        edge_type_counts[e.edge_type] += 1

    class_counts: Dict[str, int] = defaultdict(int)
    for e in wait_edges:
        class_counts[e.deadlock_class] += 1

    class_assessment = {}
    for cls in ('communication', 'service_action', 'callback_blocking'):
        definite_cls = [c for c in deadlock_definite if cls in c.get('deadlock_classes', [])]
        potential_cls = [c for c in deadlock_potential if cls in c.get('deadlock_classes', [])]
        class_assessment[cls] = {
            'wait_edge_count': class_counts.get(cls, 0),
            'trigger_edge_count': (
                len(communication_trigger_edges) if cls == 'communication' else 0
            ),
            'definite_cycle_count': len(definite_cls),
            'potential_cycle_count': len(potential_cls),
        }

    compact_wait_edges = [
        {
            'from': e.source,
            'to': e.target,
            'relation': e.relation,
            'type': e.edge_type,
            'deadlock_class': e.deadlock_class,
            'reason': e.reason,
        }
        for e in sorted(wait_edges, key=lambda x: (x.source, x.target, x.edge_type, x.reason))
    ]
    max_default_wait_edges = 24
    wait_edges_truncated = len(compact_wait_edges) > max_default_wait_edges
    compact_wait_edges = compact_wait_edges[:max_default_wait_edges]
    compact_callback_resource_graph = [
        {
            'from': str(e.get('from', '')),
            'to': str(e.get('to', '')),
            'relation': str(e.get('relation', 'waits_for')),
            'type': str(e.get('type', 'potential')),
            'deadlock_class': str(e.get('deadlock_class', 'callback_blocking')),
            'reason': str(e.get('reason', '')),
        }
        for e in sorted(
            callback_resource_edges,
            key=lambda x: (x.get('from', ''), x.get('to', ''), x.get('reason', '')),
        )
    ]
    cycle_node_set: Set[str] = set()
    for c in deadlock_definite + deadlock_potential:
        for n in c.get('nodes', []):
            cycle_node_set.add(str(n))
    key_callback_resource_edges = [
        e for e in compact_callback_resource_graph
        if (e['from'] in cycle_node_set or e['to'] in cycle_node_set)
    ]
    if not key_callback_resource_edges:
        key_callback_resource_edges = compact_callback_resource_graph[:12]
    key_callback_resource_edge_total = len(key_callback_resource_edges)
    max_default_resource_edges = 18
    callback_resource_truncated = len(key_callback_resource_edges) > max_default_resource_edges
    key_callback_resource_edges = key_callback_resource_edges[:max_default_resource_edges]

    def summarize_nodes(nodes_in: List[object]) -> Dict[str, object]:
        node_names = [str(x) for x in nodes_in]
        callback_count = sum(1 for x in node_names if x.startswith('/') and ':' in x and not x.endswith(':service_server'))
        future_count = sum(1 for x in node_names if x.startswith('future:'))
        resource_count = sum(1 for x in node_names if x.startswith('resource:'))
        plain_node_count = sum(1 for x in node_names if x.startswith('/') and ':' not in x)
        return {
            'node_count': len(node_names),
            'sample_nodes': node_names[:4],
            'node_kinds': {
                'callbacks': callback_count,
                'futures': future_count,
                'resources': resource_count,
                'plain_nodes': plain_node_count,
            },
        }

    def compact_cycle(c: Dict[str, object]) -> Dict[str, object]:
        nodes_summary = summarize_nodes([str(x) for x in c.get('nodes', [])])
        return {
            **nodes_summary,
            'deadlock_classes': c.get('deadlock_classes', []),
            'certainty': c.get('certainty', 'potential'),
            'rule': c.get('rule', ''),
            'path_certainty': c.get('path_certainty', 'low'),
            'resource_feasibility': c.get('resource_feasibility', 'unknown'),
            'runtime_confirmability': c.get('runtime_confirmability', 'low'),
            'relations': c.get('relations', []),
        }

    out = {
        'graph_summary': {
            'node_count': len(nodes),
            'wait_edge_count': len(wait_edges),
            'callback_resource_edge_count': len(callback_resource_edges),
            'key_callback_resource_edge_count': len(key_callback_resource_edges),
            'key_callback_resource_edge_total': key_callback_resource_edge_total,
            'wait_edges_truncated': wait_edges_truncated,
            'callback_resource_edges_truncated': callback_resource_truncated,
            'communication_trigger_edge_count': len(communication_trigger_edges),
            'cycle_candidate_count': len(cycle_candidates),
        },
        'wait_edges': compact_wait_edges,
        'model_context': {
            'executor_count': len(executors_out),
            'callback_group_count': len(callback_groups_out),
            'assignment_count': len(assignments_out),
            'explicit_executor_count': len([x for x in executors_out if x.get('origin') == 'explicit']),
            'explicit_callback_group_count': len([x for x in callback_groups_out if x.get('origin') == 'explicit']),
        },
        'callback_resource_graph': key_callback_resource_edges,
        'edge_type_counts': dict(sorted(edge_type_counts.items())),
        'edge_class_counts': dict(sorted(class_counts.items())),
        'class_assessment': class_assessment,
        'cycle_candidates': [
            {
                'kind': str(c.get('kind', 'scc')),
                'size': len(c.get('nodes', [])),
                'sample_nodes': [str(x) for x in c.get('nodes', [])[:4]],
            }
            for c in cycle_candidates[:5]
        ],
        'node_level_assessment': {
            'summary': {
                'definite_count': len(node_assessment.get('definite_deadlocks', [])),
                'potential_count': len(node_assessment.get('potential_deadlocks', [])),
            },
        },
        'deadlock_assessment': {
            'definite_deadlocks': [compact_cycle(c) for c in deadlock_definite[:8]],
            'potential_deadlocks': [compact_cycle(c) for c in deadlock_potential[:8]],
            'overall': overall_assessment,
            'summary': {
                'definite_count': len(deadlock_definite),
                'potential_count': len(deadlock_potential),
                'reported_definite_count': min(len(deadlock_definite), 8),
                'reported_potential_count': min(len(deadlock_potential), 8),
            },
            'primary_graph': 'callback_resource_graph',
            'rules': [
                'definite only if every cycle hop has grounded blocking evidence',
                'inferred/low-confidence edges are excluded from definite',
                'wait_for_service and spin_until_future_complete are conservative (potential by default)',
                'otherwise cycle is potential',
            ],
        },
        'config': {
            'use_llm_relations': use_llm_relations,
            'mode': 'evidence_plus_rules' if not use_llm_relations else 'evidence_plus_rules_with_llm_relations',
            'semantic_edges_ingested': semantic_edges_ingested,
        },
        'assumptions': [
            'Potential/executor edges are conservative (may include false positives).',
            'Blocking inference is keyword-based static heuristic, not path-sensitive proof.',
            'This graph models wait dependencies, not data-flow edges.',
            'All semantics are static: source code scan + optional LLM static spec; runtime trace is not used for wait-for.',
        ],
    }
    if full_output:
        out['debug'] = {
            'callback_wait_graph': callback_wait_graph,
            'communication_trigger_graph': communication_trigger_edges,
            'model_context_full': {
                'executors': executors_out,
                'callback_groups': callback_groups_out,
                'assignments': assignments_out,
            },
            'full_callback_resource_graph': callback_resource_edges,
            'full_wait_edges': [
                {
                    'from': e.source,
                    'to': e.target,
                    'type': e.edge_type,
                    'deadlock_class': e.deadlock_class,
                    'reason': e.reason,
                    'evidence': e.evidence,
                    'provenance': e.provenance,
                    'confidence': e.confidence,
                    'definite_eligible': e.definite_eligible,
                    'level': e.level,
                    'relation': e.relation,
                }
                for e in sorted(wait_edges, key=lambda x: (x.source, x.target, x.edge_type, x.reason))
            ],
            'deadlock_assessment_full': {
                'definite_deadlocks': deadlock_definite,
                'potential_deadlocks': deadlock_potential,
            },
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build static Wait-For Graph from static communication graph + code keyword analysis.'
    )
    parser.add_argument('--graph', default='/home/yinyihao/ros2/graph/graph_static.json', help='Path to static graph JSON')
    parser.add_argument('--workspace', default='/home/yinyihao/ros2', help='Workspace root path')
    parser.add_argument('--source-root', default=None, help='Optional source root for code evidence. Defaults to <workspace>/src')
    parser.add_argument('--output', default='/home/yinyihao/ros2/graph/wait_for_graph_static.json', help='Output file path')
    parser.add_argument('--spec', default='/home/yinyihao/ros2/graph/llm_project_spec.json', help='Optional static semantic spec JSON path')
    parser.add_argument(
        '--use-llm-relations',
        action='store_true',
        help='Use LLM-inferred callback relations (callback_wait_edges/waits_for_callbacks). Default off: evidence-only mode.',
    )
    parser.add_argument('--full-output', action='store_true', help='Include verbose debug sections in output JSON.')
    parser.add_argument('--compact', action='store_true', help='Write compact single-line JSON instead of pretty JSON.')
    args = parser.parse_args()

    graph_path = Path(args.graph).resolve()
    ws = Path(args.workspace).resolve()
    src_root = Path(args.source_root).resolve() if args.source_root else (ws / 'src')

    if not graph_path.exists():
        raise SystemExit(f'graph file not found: {graph_path}')
    if not src_root.exists():
        raise SystemExit(f'src root not found: {src_root}')

    graph = load_static_graph(graph_path)
    nodes = set(graph.get('nodes', []))
    src_nodes = discover_source_nodes(src_root, nodes)
    spec_path = Path(args.spec).resolve() if args.spec else None
    spec_semantics = load_spec_semantics(spec_path)
    out = build_wait_for_graph(
        graph,
        src_nodes,
        spec_semantics,
        use_llm_relations=args.use_llm_relations,
        full_output=args.full_output,
    )

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.compact:
        output_path.write_text(json.dumps(out, ensure_ascii=False) + '\n', encoding='utf-8')
    else:
        output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(str(output_path))


if __name__ == '__main__':
    main()
