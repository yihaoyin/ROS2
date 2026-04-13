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
    reason: str
    evidence: Dict[str, object]


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


def build_wait_for_graph(graph: Dict, src_nodes: Dict[str, SourceNode]) -> Dict:
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

    service_servers: Dict[str, Set[str]] = defaultdict(set)
    action_servers: Dict[str, Set[str]] = defaultdict(set)
    for ep in endpoints:
        if ep.kind == 'service_server':
            service_servers[ep.name].add(ep.node)
        elif ep.kind == 'action_server':
            action_servers[ep.name].add(ep.node)

    wait_edges: List[WaitEdge] = []

    def add_edge(src: str, dst: str, edge_type: str, reason: str, evidence: Dict[str, object]) -> None:
        if src == dst:
            return
        wait_edges.append(
            WaitEdge(
                source=src,
                target=dst,
                edge_type=edge_type,
                reason=reason,
                evidence=evidence,
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
                    'blocking_callback_with_published_topic_dependency',
                    {
                        'topic': topic,
                        'file': str(src_nodes[src].file) if src in src_nodes else None,
                        'blocking_callback_lines': cb_b_lines.get(src, []),
                    },
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
                    'single_thread_executor_with_blocking_callback',
                    {
                        'topic': topic,
                        'file': str(src_nodes[src].file) if src in src_nodes else None,
                        'blocking_callback_lines': cb_b_lines.get(src, []),
                    },
                )

    # Deduplicate identical edges by (src,dst,type,reason,name/topic)
    unique = {}
    for e in wait_edges:
        key_basis = e.evidence.get('name') or e.evidence.get('topic') or ''
        key = (e.source, e.target, e.edge_type, e.reason, str(key_basis))
        if key not in unique:
            unique[key] = e
    wait_edges = list(unique.values())

    # Build adjacency for cycle detection.
    adj: Dict[str, Set[str]] = defaultdict(set)
    radj: Dict[str, Set[str]] = defaultdict(set)
    edge_types_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for n in nodes:
        adj[n] = set()
        radj[n] = set()
    for e in wait_edges:
        adj[e.source].add(e.target)
        radj[e.target].add(e.source)
        edge_types_by_pair[(e.source, e.target)].add(e.edge_type)

    # Kosaraju SCC
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

    cycle_candidates = []
    for comp in sccs:
        if len(comp) > 1:
            cycle_candidates.append({'nodes': comp, 'kind': 'scc'})
        elif len(comp) == 1:
            u = comp[0]
            if u in adj[u]:
                cycle_candidates.append({'nodes': comp, 'kind': 'self_loop'})

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
            visited = {start}

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
                    if v in visited:
                        continue
                    # Canonical traversal: avoid duplicate cycles from lower-ranked entry points.
                    if rank[v] < rank[start]:
                        continue
                    visited.add(v)
                    stack.append(v)
                    dfs(v)
                    stack.pop()
                    visited.remove(v)

            dfs(start)
            if len(out) >= max_cycles:
                break

        return out

    deadlock_definite = []
    deadlock_potential = []

    for comp in sccs:
        if len(comp) == 1:
            u = comp[0]
            if u not in adj[u]:
                continue
            cycles = [[u]]
        else:
            cycles = enumerate_cycles_in_scc(comp)

        for cyc_nodes in cycles:
            if len(cyc_nodes) == 1:
                pair_seq = [(cyc_nodes[0], cyc_nodes[0])]
            else:
                pair_seq = []
                for i in range(len(cyc_nodes)):
                    a = cyc_nodes[i]
                    b = cyc_nodes[(i + 1) % len(cyc_nodes)]
                    pair_seq.append((a, b))

            hop_type_sets = [edge_types_by_pair.get(p, set()) for p in pair_seq]
            if any(not s for s in hop_type_sets):
                continue

            all_blocking_possible = all('blocking' in s for s in hop_type_sets)
            blocking_executor_only_possible = all((s & {'blocking', 'executor_block'}) for s in hop_type_sets)
            has_executor_somewhere = any('executor_block' in s for s in hop_type_sets)
            has_blocking_somewhere = any('blocking' in s for s in hop_type_sets)

            certainty = 'potential'
            rule = 'contains_potential_or_unknown_wait_edges'

            # Definite rule 1: all edges can be interpreted as blocking.
            if all_blocking_possible:
                certainty = 'definite'
                rule = 'all_edges_blocking'
            # Definite rule 2: cycle can be formed by blocking + executor constraints.
            elif blocking_executor_only_possible and has_executor_somewhere and has_blocking_somewhere:
                certainty = 'definite'
                rule = 'blocking_plus_executor_constraints'

            cycle_entry = {
                'nodes': cyc_nodes,
                'hops': [
                    {
                        'from': a,
                        'to': b,
                        'edge_types': sorted(list(edge_types_by_pair.get((a, b), set()))),
                    }
                    for (a, b) in pair_seq
                ],
                'certainty': certainty,
                'rule': rule,
            }

            if certainty == 'definite':
                deadlock_definite.append(cycle_entry)
            else:
                deadlock_potential.append(cycle_entry)

    # Deduplicate cycle entries by normalized node cycle + certainty.
    def dedup_cycles(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        seen = set()
        out = []
        for item in items:
            nodes_key = tuple(item.get('nodes', []))
            key = (nodes_key, item.get('certainty', ''), item.get('rule', ''))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    deadlock_definite = dedup_cycles(deadlock_definite)
    deadlock_potential = dedup_cycles(deadlock_potential)

    edge_type_counts: Dict[str, int] = defaultdict(int)
    for e in wait_edges:
        edge_type_counts[e.edge_type] += 1

    out = {
        'nodes': sorted(nodes),
        'wait_edges': [
            {
                'from': e.source,
                'to': e.target,
                'type': e.edge_type,
                'reason': e.reason,
                'evidence': e.evidence,
            }
            for e in sorted(wait_edges, key=lambda x: (x.source, x.target, x.edge_type, x.reason))
        ],
        'edge_type_counts': dict(sorted(edge_type_counts.items())),
        'cycle_candidates': cycle_candidates,
        'deadlock_assessment': {
            'definite_deadlocks': deadlock_definite,
            'potential_deadlocks': deadlock_potential,
            'summary': {
                'definite_count': len(deadlock_definite),
                'potential_count': len(deadlock_potential),
            },
            'rules': [
                'definite if all cycle edges are blocking',
                'definite if cycle edges are constrained by blocking + executor_block',
                'otherwise cycle is potential',
            ],
        },
        'assumptions': [
            'Potential/executor edges are conservative (may include false positives).',
            'Blocking inference is keyword-based static heuristic, not path-sensitive proof.',
            'This graph models wait dependencies, not data-flow edges.',
        ],
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build static Wait-For Graph from static communication graph + code keyword analysis.'
    )
    parser.add_argument('--graph', default='/home/yinyihao/ros2/graph/graph_static.json', help='Path to static graph JSON')
    parser.add_argument('--workspace', default='/home/yinyihao/ros2', help='Workspace root path')
    parser.add_argument('--output', default='/home/yinyihao/ros2/graph/wait_for_graph_static.json', help='Output file path')
    parser.add_argument('--pretty', action='store_true', help='Pretty-print output JSON')
    args = parser.parse_args()

    graph_path = Path(args.graph).resolve()
    ws = Path(args.workspace).resolve()
    src_root = ws / 'src'

    if not graph_path.exists():
        raise SystemExit(f'graph file not found: {graph_path}')
    if not src_root.exists():
        raise SystemExit(f'src root not found: {src_root}')

    graph = load_static_graph(graph_path)
    nodes = set(graph.get('nodes', []))
    src_nodes = discover_source_nodes(src_root, nodes)
    out = build_wait_for_graph(graph, src_nodes)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.pretty:
        output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        output_path.write_text(json.dumps(out, ensure_ascii=False) + '\n', encoding='utf-8')

    print(str(output_path))


if __name__ == '__main__':
    main()
