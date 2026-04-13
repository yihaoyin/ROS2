#!/usr/bin/env python3

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


CPP_NODE_CTOR_RE = re.compile(r'\b(?:Node|LifecycleNode)\s*\(\s*"(?P<name>[^"]+)"\s*\)')
PY_NODE_CTOR_RE = re.compile(r'\bsuper\(\)\.__init__\s*\(\s*"(?P<name>[^"]+)"')

CPP_PUB_RE = re.compile(r'create_publisher\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<topic>[^"]+)"')
CPP_SUB_RE = re.compile(r'create_subscription\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<topic>[^"]+)"')
CPP_SERVICE_RE = re.compile(r'create_service\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<name>[^"]+)"')
CPP_CLIENT_RE = re.compile(r'create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\(\s*"(?P<name>[^"]+)"')
CPP_ACTION_SERVER_RE = re.compile(
    r'rclcpp_action::create_server\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?"(?P<name>[^"]+)"',
    re.MULTILINE,
)
CPP_ACTION_CLIENT_RE = re.compile(
    r'rclcpp_action::create_client\s*<\s*(?P<type>[^>]+?)\s*>\s*\([\s\S]*?"(?P<name>[^"]+)"',
    re.MULTILINE,
)

PY_PUB_RE = re.compile(r'create_publisher\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<topic>[^"]+)"')
PY_SUB_RE = re.compile(r'create_subscription\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<topic>[^"]+)"')
PY_SERVICE_RE = re.compile(r'create_service\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_CLIENT_RE = re.compile(r'create_client\s*\(\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_ACTION_SERVER_RE = re.compile(r'ActionServer\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')
PY_ACTION_CLIENT_RE = re.compile(r'ActionClient\s*\(\s*self\s*,\s*(?P<type>[A-Za-z0-9_:\.]+)\s*,\s*"(?P<name>[^"]+)"')


@dataclass
class Endpoint:
    kind: str
    name: str
    type_name: str
    file: str
    line: int


@dataclass
class NodeDecl:
    node_name: str
    file: str
    ctor_line: int
    endpoints: List[Endpoint]


def _full(name: str) -> str:
    if name.startswith("/"):
        return name
    return "/" + name


def _chunk(lines: List[str], i: int, lookahead: int = 3) -> str:
    s = lines[i]
    for k in range(1, lookahead + 1):
        if i + k < len(lines):
            s += " " + lines[i + k]
    return s


def _scan_file(path: Path) -> List[NodeDecl]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    lines = text.splitlines()
    is_py = path.suffix == ".py"
    ctor_re = PY_NODE_CTOR_RE if is_py else CPP_NODE_CTOR_RE

    node_decls: List[NodeDecl] = []
    for i, line in enumerate(lines):
        m = ctor_re.search(line)
        if not m:
            continue
        node_decls.append(NodeDecl(node_name=_full(m.group("name")), file=str(path), ctor_line=i + 1, endpoints=[]))

    if not node_decls:
        return []

    endpoints: List[Endpoint] = []
    for i, line in enumerate(lines):
        c = _chunk(lines, i)
        if is_py:
            patterns = [
                (PY_PUB_RE, "pub", "topic"),
                (PY_SUB_RE, "sub", "topic"),
                (PY_SERVICE_RE, "service_server", "name"),
                (PY_CLIENT_RE, "service_client", "name"),
                (PY_ACTION_SERVER_RE, "action_server", "name"),
                (PY_ACTION_CLIENT_RE, "action_client", "name"),
            ]
        else:
            patterns = [
                (CPP_PUB_RE, "pub", "topic"),
                (CPP_SUB_RE, "sub", "topic"),
                (CPP_SERVICE_RE, "service_server", "name"),
                (CPP_CLIENT_RE, "service_client", "name"),
            ]

        for pat, kind, key in patterns:
            m = pat.search(c)
            if not m:
                continue
            endpoints.append(
                Endpoint(kind=kind, name=_full(m.group(key)), type_name=m.group("type").strip(), file=str(path), line=i + 1)
            )

    if not is_py:
        for m in CPP_ACTION_SERVER_RE.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            endpoints.append(Endpoint("action_server", _full(m.group("name")), m.group("type").strip(), str(path), ln))
        for m in CPP_ACTION_CLIENT_RE.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            endpoints.append(Endpoint("action_client", _full(m.group("name")), m.group("type").strip(), str(path), ln))

    ctor_lines = [n.ctor_line for n in node_decls]
    for ep in endpoints:
        idx = 0
        for j, cl in enumerate(ctor_lines):
            if cl <= ep.line:
                idx = j
            else:
                break
        node_decls[idx].endpoints.append(ep)

    return node_decls


def scan_workspace(src_root: Path) -> Dict[str, NodeDecl]:
    out: Dict[str, NodeDecl] = {}
    for ext in ("*.cpp", "*.hpp", "*.h", "*.py"):
        for p in src_root.rglob(ext):
            for decl in _scan_file(p):
                prev = out.get(decl.node_name)
                if prev is None or len(decl.endpoints) > len(prev.endpoints):
                    out[decl.node_name] = decl
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Locate node/topic/action/service source positions from a dynamic graph JSON.")
    parser.add_argument("--graph", default="/home/yinyihao/ros2/graph/graph_dynamic.json", help="Dynamic graph JSON path")
    parser.add_argument("--workspace", default="/home/yinyihao/ros2", help="Workspace root")
    parser.add_argument("--pretty", action="store_true", help="Pretty print output")
    args = parser.parse_args()

    graph_path = Path(args.graph)
    ws = Path(args.workspace)
    src_root = ws / "src"

    graph = json.loads(graph_path.read_text())
    graph_nodes = set(graph.get("nodes", []))
    graph_topics = {t.get("name") for t in graph.get("topics", [])}

    decls = scan_workspace(src_root)

    resolved = []
    for n in sorted(graph_nodes):
        d = decls.get(n)
        if d is None:
            resolved.append({"node": n, "status": "not_found_in_workspace_sources"})
            continue

        eps = []
        for ep in d.endpoints:
            include = True
            if ep.kind in ("pub", "sub") and ep.name not in graph_topics:
                include = False
            if not include:
                continue
            eps.append(
                {
                    "kind": ep.kind,
                    "name": ep.name,
                    "type": ep.type_name,
                    "source": {"file": ep.file, "line": ep.line},
                }
            )

        resolved.append(
            {
                "node": n,
                "constructor": {"file": d.file, "line": d.ctor_line},
                "endpoints": sorted(eps, key=lambda x: (x["kind"], x["name"], x["source"]["line"])),
            }
        )

    output = {
        "graph": str(graph_path),
        "workspace": str(ws),
        "resolved": resolved,
    }

    if args.pretty:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
