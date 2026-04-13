#!/usr/bin/env python3

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


CPP_NODE_CTOR_RE = re.compile(
    r"\b(?:Node|LifecycleNode)\s*\(\s*\"(?P<name>[^\"]+)\"(?:\s*,[^)]*)?\)"
)
PY_NODE_CTOR_RE = re.compile(r"\bsuper\(\)\.__init__\s*\(\s*\"(?P<name>[^\"]+)\"(?:\s*,[^)]*)?\)")

CPP_PUB_RE = re.compile(
    r"create_publisher\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*\"(?P<topic>[^\"]+)\""
)
CPP_SUB_RE = re.compile(
    r"create_subscription\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*\"(?P<topic>[^\"]+)\""
)
CPP_CLIENT_RE = re.compile(
    r"create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*\"(?P<service>[^\"]+)\""
)
CPP_SERVICE_RE = re.compile(
    r"create_service\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*\"(?P<service>[^\"]+)\""
)
CPP_ACTION_SERVER_RE = re.compile(
    r"rclcpp_action::create_server\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?\"(?P<name>[^\"]+)\"",
    re.MULTILINE,
)
CPP_ACTION_CLIENT_RE = re.compile(
    r"rclcpp_action::create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?\"(?P<name>[^\"]+)\"",
    re.MULTILINE,
)

PY_PUB_RE = re.compile(
    r"create_publisher\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<topic>[^\"]+)\""
)
PY_SUB_RE = re.compile(
    r"create_subscription\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<topic>[^\"]+)\""
)
PY_CLIENT_RE = re.compile(
    r"create_client\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<service>[^\"]+)\""
)
PY_SERVICE_RE = re.compile(
    r"create_service\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<service>[^\"]+)\""
)
PY_ACTION_CLIENT_RE = re.compile(
    r"ActionClient\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<name>[^\"]+)\""
)
PY_ACTION_SERVER_RE = re.compile(
    r"ActionServer\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*\"(?P<name>[^\"]+)\""
)


@dataclass
class Endpoint:
    kind: str  # pub|sub|service_client|service_server|action_client|action_server
    name: str
    type_name: str
    line: int


@dataclass
class NodeInfo:
    node_name: str
    file: str
    ctor_line: int
    endpoints: List[Endpoint]


def _full_node_name(name: str) -> str:
    n = name.strip()
    if not n.startswith("/"):
        n = "/" + n
    return n


def _full_resource_name(name: str) -> str:
    r = name.strip()
    if not r.startswith("/"):
        r = "/" + r
    return r


def _chunk(lines: List[str], i: int, lookahead: int = 3) -> str:
    s = lines[i]
    for k in range(1, lookahead + 1):
        if i + k < len(lines):
            s += " " + lines[i + k]
    return s


def _scan_file(path: Path) -> List[NodeInfo]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    lines = text.splitlines()
    is_py = path.suffix == ".py"

    node_ctor_re = PY_NODE_CTOR_RE if is_py else CPP_NODE_CTOR_RE
    nodes: List[NodeInfo] = []

    for m in node_ctor_re.finditer(text):
        ln = text.count("\n", 0, m.start()) + 1
        nodes.append(
            NodeInfo(
                node_name=_full_node_name(m.group("name")),
                file=str(path),
                ctor_line=ln,
                endpoints=[],
            )
        )

    if not nodes:
        return []

    # Collect endpoints file-wide then attach to nearest previous ctor.
    endpoints_by_line: List[Endpoint] = []
    for i, line in enumerate(lines):
        c = _chunk(lines, i)

        if is_py:
            m_pub = PY_PUB_RE.search(c)
            m_sub = PY_SUB_RE.search(c)
            m_cli = PY_CLIENT_RE.search(c)
            m_srv = PY_SERVICE_RE.search(c)
            m_ac = PY_ACTION_CLIENT_RE.search(c)
            m_as = PY_ACTION_SERVER_RE.search(c)
        else:
            m_pub = CPP_PUB_RE.search(c)
            m_sub = CPP_SUB_RE.search(c)
            m_cli = CPP_CLIENT_RE.search(c)
            m_srv = CPP_SERVICE_RE.search(c)
            m_ac = None
            m_as = None

        if m_pub and "create_publisher" in line:
            endpoints_by_line.append(Endpoint("pub", _full_resource_name(m_pub.group("topic")), m_pub.group("type").strip(), i + 1))
        if m_sub and "create_subscription" in line:
            endpoints_by_line.append(Endpoint("sub", _full_resource_name(m_sub.group("topic")), m_sub.group("type").strip(), i + 1))
        if m_cli and "create_client" in line:
            endpoints_by_line.append(Endpoint("service_client", _full_resource_name(m_cli.group("service")), m_cli.group("type").strip(), i + 1))
        if m_srv and "create_service" in line:
            endpoints_by_line.append(Endpoint("service_server", _full_resource_name(m_srv.group("service")), m_srv.group("type").strip(), i + 1))
        if m_ac and ("create_client" in line or "ActionClient" in line):
            endpoints_by_line.append(Endpoint("action_client", _full_resource_name(m_ac.group("name")), m_ac.group("type").strip(), i + 1))
        if m_as and ("create_server" in line or "ActionServer" in line):
            endpoints_by_line.append(Endpoint("action_server", _full_resource_name(m_as.group("name")), m_as.group("type").strip(), i + 1))

    if not is_py:
        for m in CPP_ACTION_SERVER_RE.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            endpoints_by_line.append(
                Endpoint("action_server", _full_resource_name(m.group("name")), m.group("type").strip(), ln)
            )
        for m in CPP_ACTION_CLIENT_RE.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            endpoints_by_line.append(
                Endpoint("action_client", _full_resource_name(m.group("name")), m.group("type").strip(), ln)
            )

    ctor_lines = [n.ctor_line for n in nodes]
    for ep in endpoints_by_line:
        idx = 0
        for j, cl in enumerate(ctor_lines):
            if cl <= ep.line:
                idx = j
            else:
                break
        nodes[idx].endpoints.append(ep)

    return nodes


def scan_workspace(src_root: Path) -> List[NodeInfo]:
    out: List[NodeInfo] = []
    for ext in ("*.cpp", "*.hpp", "*.h", "*.py"):
        for p in src_root.rglob(ext):
            out.extend(_scan_file(p))
    return out


def to_graph(nodes: List[NodeInfo]) -> Dict:
    node_names: Set[str] = set()
    topic_types: Dict[str, Set[str]] = defaultdict(set)
    edges: List[Dict[str, str]] = []

    for n in nodes:
        node_names.add(n.node_name)
        for ep in n.endpoints:
            if ep.kind == "pub":
                topic_types[ep.name].add(ep.type_name)
                edges.append({"from": n.node_name, "to": ep.name, "kind": "pub"})
            elif ep.kind == "sub":
                topic_types[ep.name].add(ep.type_name)
                edges.append({"from": ep.name, "to": n.node_name, "kind": "sub"})
            elif ep.kind in ("action_client", "action_server"):
                # Approximate action-related topic edges, so it can be compared to dynamic dump.
                status_topic = ep.name + "/_action/status"
                feedback_topic = ep.name + "/_action/feedback"
                if ep.kind == "action_server":
                    topic_types[status_topic].add("action_msgs/msg/GoalStatusArray")
                    topic_types[feedback_topic].add(ep.type_name + "_FeedbackMessage")
                    edges.append({"from": n.node_name, "to": status_topic, "kind": "pub"})
                    edges.append({"from": n.node_name, "to": feedback_topic, "kind": "pub"})
                else:
                    edges.append({"from": status_topic, "to": n.node_name, "kind": "sub"})
                    edges.append({"from": feedback_topic, "to": n.node_name, "kind": "sub"})

    topics = [
        {"name": k, "types": sorted(list(v))} for k, v in sorted(topic_types.items())
    ]
    edges_sorted = sorted(edges, key=lambda e: (e["from"], e["to"], e["kind"]))

    return {
        "nodes": sorted(node_names),
        "topics": topics,
        "edges": edges_sorted,
    }


def filter_by_package(nodes: List[NodeInfo], package: Optional[str]) -> List[NodeInfo]:
    if not package:
        return nodes
    marker = f"/src/{package}/"
    return [n for n in nodes if marker in n.file.replace("\\", "/")]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Static ROS2 graph extractor from source keywords (pub/sub/service/action constructors)."
    )
    parser.add_argument("--workspace", default=str(Path.home() / "ros2"), help="Workspace root")
    parser.add_argument("--package", default=None, help="Optional package name filter under src/")
    parser.add_argument("--compact", action="store_true", help="Output compact JSON (default: pretty, same as dynamic dump style)")
    args = parser.parse_args()

    ws = Path(args.workspace).resolve()
    src_root = ws / "src"
    if not src_root.exists():
        raise SystemExit(f"src root not found: {src_root}")

    nodes = scan_workspace(src_root)
    nodes = filter_by_package(nodes, args.package)
    graph = to_graph(nodes)

    if args.compact:
        print(json.dumps(graph, ensure_ascii=False))
    else:
        print(json.dumps(graph, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
