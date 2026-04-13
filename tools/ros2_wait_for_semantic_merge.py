#!/usr/bin/env python3

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def _norm_executor(v: str) -> str:
    s = str(v or "unknown").strip().lower()
    if s in ("single", "singlethreaded", "single_threaded"):
        return "single_threaded"
    if s in ("multi", "multithreaded", "multi_threaded"):
        return "multi_threaded"
    return "unknown"


def _cycle_canonical(cycle: List[str]) -> Tuple[str, ...]:
    if not cycle:
        return tuple()
    rots = [tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))]
    return min(rots)


def _find_cycles(nodes: Set[str], adj: Dict[str, Set[str]], max_cycles: int = 2000) -> List[List[str]]:
    ordered = sorted(nodes)
    rank = {n: i for i, n in enumerate(ordered)}
    out: List[List[str]] = []
    seen = set()

    for start in ordered:
        stack = [start]
        vis = {start}

        def dfs(u: str) -> None:
            if len(out) >= max_cycles:
                return
            for v in sorted(adj.get(u, set())):
                if v == start and len(stack) >= 2:
                    cyc = stack.copy()
                    key = _cycle_canonical(cyc)
                    if key not in seen:
                        seen.add(key)
                        out.append(cyc)
                    continue
                if v in vis:
                    continue
                if rank.get(v, 10**9) < rank[start]:
                    continue
                vis.add(v)
                stack.append(v)
                dfs(v)
                stack.pop()
                vis.remove(v)

        dfs(start)
        if len(out) >= max_cycles:
            break

    return out


def _infer_edge_type(reason: str) -> str:
    r = str(reason or "").lower()
    if "service" in r or "blocking" in r:
        return "blocking"
    return "potential"


def merge_semantics(workspace: Path, spec_rel: str, wf_rel: str) -> Dict:
    spec_path = workspace / spec_rel
    wf_path = workspace / wf_rel
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    wf = json.loads(wf_path.read_text(encoding="utf-8"))

    nodes = set(wf.get("nodes", []))
    sem = spec.get("graph_seed", {}).get("node_semantics", [])

    service_owner: Dict[str, str] = {}
    executor_semantics: List[Dict] = []
    executor_by_node: Dict[str, str] = {}

    cb_edges: List[Dict] = []
    cb_seen = set()
    cb_merge: Dict[Tuple[str, str, str], Dict] = {}
    cb_waiters_by_service: Dict[str, List[str]] = defaultdict(list)
    service_callbacks_by_node: Dict[str, List[str]] = defaultdict(list)

    for s in sem:
        if not isinstance(s, dict):
            continue
        node = s.get("name")
        if not isinstance(node, str) or not node:
            continue
        if nodes and node not in nodes:
            continue

        ex = _norm_executor(s.get("executor", "unknown"))
        executor_by_node[node] = ex
        executor_semantics.append(
            {
                "node": node,
                "executor": ex,
                "callback_sources": [str(x) for x in s.get("callback_sources", []) if isinstance(x, str)],
            }
        )

        for srv in s.get("service_servers", []):
            if isinstance(srv, str) and srv:
                service_owner.setdefault(srv, node)

    def add_cb_edge(src: str, dst: str, edge_type: str, reason: str, source: str, service: str = None) -> None:
        if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
            return
        reason_raw = str(reason)
        reason_norm = reason_raw
        if edge_type == "blocking" and "service" in reason_raw.lower():
            reason_norm = "service_call"

        key_seen = (src, dst, reason_norm)
        if key_seen in cb_seen:
            return
        cb_seen.add(key_seen)

        key = (src, dst, edge_type)
        merged = cb_merge.get(key)
        if merged is None:
            entry = {
                "from": src,
                "to": dst,
                "type": edge_type,
                "reason": reason_norm,
                "source": source,
            }
            if reason_norm != reason_raw:
                entry["reason_detail"] = reason_raw
            if service:
                entry["service"] = service
            cb_merge[key] = entry
            return

        # Merge multiple same from/to/type reasons into one edge to reduce duplicate noise.
        if merged.get("reason") != reason_norm:
            if "reasons" not in merged:
                merged["reasons"] = [merged.get("reason")]
            if reason_norm not in merged["reasons"]:
                merged["reasons"].append(reason_norm)
            # Prefer service_call as canonical reason for blocking service waits.
            if edge_type == "blocking" and (merged.get("reason") != "service_call"):
                if reason_norm == "service_call":
                    merged["reason"] = "service_call"

        if merged.get("source") != source:
            if "sources" not in merged:
                merged["sources"] = [merged.get("source")]
            if source not in merged["sources"]:
                merged["sources"].append(source)

        if service and "service" not in merged:
            merged["service"] = service

    # Build callback-level edges from explicit callback_wait_edges and callback declarations.
    for s in sem:
        if not isinstance(s, dict):
            continue
        node = s.get("name")
        if not isinstance(node, str) or not node:
            continue
        if nodes and node not in nodes:
            continue

        for e in s.get("callback_wait_edges", []):
            if not isinstance(e, dict):
                continue
            ef = str(e.get("from", node))
            et = e.get("to")
            if not isinstance(et, str) or not et:
                continue
            src_node = ef.split(":", 1)[0]
            dst_node = et.split(":", 1)[0]
            if nodes and (src_node not in nodes or dst_node not in nodes):
                continue
            reason = str(e.get("reason", "llm_callback_wait"))
            add_cb_edge(ef, et, _infer_edge_type(reason), reason, "llm_callback_wait_edges")

        for cb in s.get("callbacks", []):
            if not isinstance(cb, dict):
                continue
            cb_id = str(cb.get("id", "")).strip()
            if not cb_id:
                continue
            src_cb = f"{node}:{cb_id}"
            cb_source = str(cb.get("source", "unknown")).strip().lower()
            cb_blocking = [str(x) for x in cb.get("blocking_calls", []) if isinstance(x, str)]

            if cb_source == "service" and cb_blocking:
                service_callbacks_by_node[node].append(src_cb)

            for tcb in cb.get("waits_for_callbacks", []):
                if not isinstance(tcb, str) or not tcb:
                    continue
                dst_node = tcb.split(":", 1)[0]
                if nodes and (node not in nodes or dst_node not in nodes):
                    continue
                add_cb_edge(src_cb, tcb, "potential", "callback_dependency", "llm_callbacks")

            for srv in cb.get("waits_for_services", []):
                if not isinstance(srv, str) or not srv:
                    continue
                owner = service_owner.get(srv)
                if not owner:
                    continue
                if nodes and (node not in nodes or owner not in nodes):
                    continue
                dst_cb = f"{owner}:service_server"
                add_cb_edge(src_cb, dst_cb, "blocking", "service_call", "llm_callbacks", service=srv)
                cb_waiters_by_service[srv].append(src_cb)

    # Add reverse callback dependency heuristic:
    # if service callback itself is blocking, add service_cb -> client_cb.
    reverse_added = 0
    for srv, owner in service_owner.items():
        owner_cbs = service_callbacks_by_node.get(owner, [])
        waiters = cb_waiters_by_service.get(srv, [])
        for scb in owner_cbs:
            for wcb in waiters:
                before = len(cb_merge)
                add_cb_edge(scb, wcb, "blocking", "service_callback_blocking_reverse", "heuristic", service=srv)
                if len(cb_merge) > before:
                    reverse_added += 1

    # Collapse same callback pair by priority: blocking > potential.
    pair_best: Dict[Tuple[str, str], Dict] = {}
    for e in cb_merge.values():
        k = (str(e.get("from", "")), str(e.get("to", "")))
        prev = pair_best.get(k)
        if prev is None:
            pair_best[k] = e
            continue
        prev_t = str(prev.get("type", "potential"))
        cur_t = str(e.get("type", "potential"))
        if prev_t == "blocking":
            continue
        if cur_t == "blocking":
            pair_best[k] = e

    cb_edges = sorted(pair_best.values(), key=lambda x: (x.get("from", ""), x.get("to", ""), x.get("type", ""), x.get("reason", "")))

    # Project callback edges to node-level visualization only (not for deadlock analysis).
    node_wait_vis = []
    nw_seen = set()
    for e in cb_edges:
        sf = e.get("from", "")
        st = e.get("to", "")
        sn = sf.split(":", 1)[0] if isinstance(sf, str) else ""
        tn = st.split(":", 1)[0] if isinstance(st, str) else ""
        if not sn or not tn or sn == tn:
            continue
        key = (sn, tn, e.get("type", "projection"))
        if key in nw_seen:
            continue
        nw_seen.add(key)
        node_wait_vis.append(
            {
                "from": sn,
                "to": tn,
                "type": str(e.get("type", "projection")),
                "reason": str(e.get("reason", "callback_projection")),
                "evidence": {"source": "callback_wait_graph_projection"},
            }
        )

    # Callback-graph deadlock detection only.
    cb_nodes = set()
    adj: Dict[str, Set[str]] = defaultdict(set)
    for e in cb_edges:
        a = e.get("from")
        b = e.get("to")
        if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
            continue
        cb_nodes.add(a)
        cb_nodes.add(b)
        adj[a].add(b)

    cycles = _find_cycles(cb_nodes, adj)

    definite = []
    potential = []
    for cyc in cycles:
        hop_pairs = []
        for i in range(len(cyc)):
            a = cyc[i]
            b = cyc[(i + 1) % len(cyc)]
            hop_pairs.append((a, b))

        involved_nodes = sorted({x.split(":", 1)[0] for x in cyc})
        all_single = len(involved_nodes) > 0 and all(executor_by_node.get(n, "unknown") == "single_threaded" for n in involved_nodes)
        certainty = "definite" if all_single else "potential"
        rule = "callback_cycle_all_single_threaded_nodes" if all_single else "callback_cycle_requires_runtime_confirmation"

        item = {
            "callbacks": cyc,
            "nodes": involved_nodes,
            "hops": [{"from": a, "to": b} for a, b in hop_pairs],
            "certainty": certainty,
            "rule": rule,
        }
        if certainty == "definite":
            definite.append(item)
        else:
            potential.append(item)

    wf["callback_wait_graph"] = cb_edges
    wf["executor_semantics"] = sorted(executor_semantics, key=lambda x: (x.get("node", ""), x.get("executor", "")))
    wf["wait_edges"] = sorted(node_wait_vis, key=lambda x: (x.get("from", ""), x.get("to", ""), x.get("type", ""), x.get("reason", "")))
    edge_type_counts = defaultdict(int)
    for e in wf["wait_edges"]:
        edge_type_counts[str(e.get("type", "projection"))] += 1
    wf["edge_type_counts"] = dict(sorted(edge_type_counts.items()))

    legacy = wf.get("deadlock_assessment", {})
    wf["deadlock_assessment_legacy"] = legacy
    wf["deadlock_assessment"] = {
        "definite_deadlocks": definite,
        "potential_deadlocks": potential,
        "summary": {
            "definite_count": len(definite),
            "potential_count": len(potential),
            "cycle_count": len(cycles),
            "analysis_basis": "callback_wait_graph_only",
        },
        "rules": [
            "deadlock analysis uses callback_wait_graph only",
            "node-level wait_edges are projection for visualization only",
            "definite if a callback cycle exists and all involved nodes are single_threaded",
            "otherwise potential and requires runtime confirmation",
        ],
        "notes": [
            f"llm_semantics_merged=callback_edges:{len(cb_edges)},executors:{len(executor_semantics)},reverse_edges:{reverse_added}",
        ],
    }

    return wf


def main() -> None:
    p = argparse.ArgumentParser(description="Merge LLM callback/executor semantics into wait-for graph")
    p.add_argument("--workspace", default="/home/yinyihao/ros2")
    p.add_argument("--spec", default="graph/llm_project_spec.json")
    p.add_argument("--wait-for", default="graph/wait_for_graph_static.json")
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args()

    ws = Path(args.workspace).resolve()
    out = merge_semantics(ws, args.spec, args.wait_for)

    out_path = ws / args.wait_for
    if args.pretty:
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        out_path.write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")

    print(str(out_path))


if __name__ == "__main__":
    main()
