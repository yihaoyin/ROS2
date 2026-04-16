#!/usr/bin/env python3

import argparse
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROMPT_TEMPLATE = textwrap.dedent(
    """
    You are a ROS2 static evidence extractor. Output JSON only.
    Input repo_url={repo_url}, ref={ref}, extracted_code_evidence_json={evidence_json}

    Output schema (strict):
    {{
      "schema_version": "1.0",
      "meta": {{"repo_url":"...","ref":"...","project_name":"...","language":["cpp","python"]}},
      "graph_seed": {{
        "nodes": ["/node"],
        "topics": ["/topic"],
        "edges": [{{"from":"/node","to":"/topic","kind":"pub"}},{{"from":"/topic","to":"/node","kind":"sub"}}],
        "node_semantics": [{{"name":"/node","executor":"single_threaded|multi_threaded|unknown","callback_sources":[],"service_clients":[],"service_servers":[],"blocking_calls":[],"callbacks":[{{"id":"cb_x","source":"timer|subscription|service|action","callback_group":"cg::/node::default","waits_for_callbacks":[],"waits_for_services":[],"blocking_calls":[]}}],"callback_wait_edges":[],"semantic_edges":[]}}],
        "semantic_edges": [{{"from":"/node:cb_x","to":"future:service_response:/service:/node:cb_x","relation":"waits_for","type":"blocking|potential|executor_block","deadlock_class":"communication|service_action|callback_blocking","reason":"...","provenance":"grounded|inferred","confidence":"high|medium|low","definite_eligible":true}}],
        "executors": [{{"id":"exec_1","kind":"SingleThreadedExecutor","thread_capacity":1}}],
        "callback_groups": [{{"id":"cg::/node::default","node":"/node","type":"MutuallyExclusive|Reentrant|Unknown","executor":"exec_1","origin":"explicit|implicit_default|inferred"}}],
        "assignments": [{{"callback_group":"cg::/node::default","executor":"exec_1","origin":"explicit|inferred"}}]
      }}
    }}

    Constraints:
    1) Use absolute ROS names starting with "/".
    2) Preserve maximal verifiable entities (do not shrink subgraph).
    3) Evidence only: do not invent cycles or reverse edges.
    4) If uncertain, leave waits_for/callback_wait_edges empty.
    5) Allowed relation: holds, waits_for, requests, completion_depends_on, scheduled_by.
    6) Every item must be traceable to extracted_code_evidence_json.
    """
).strip()

CPP_NODE_TEMPLATE = r'''#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class {class_name} : public rclcpp::Node
{{
public:
  {class_name}()
  : rclcpp::Node("{node_name}")
  {{
{pub_init}
{sub_init}
{srv_server_init}
{srv_client_init}
{timer_init}
  }}

private:
{callbacks}

{members}
}};

int main(int argc, char ** argv)
{{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<{class_name}>();
{spin_block}
  rclcpp::shutdown();
  return 0;
}}
'''

LAUNCH_TEMPLATE = '''from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
{node_entries}
    ])
'''

RESERVED_TOPICS = {
    "/rosout",
    "/parameter_events",
}

RESERVED_SERVICE_BASENAMES = {
    "describe_parameters",
    "get_parameter_types",
    "get_parameters",
    "list_parameters",
    "set_parameters",
    "set_parameters_atomically",
}

GENERATED_NAMESPACE = "/generated"


INVALID_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_~/{}\/]")
INVALID_TOKEN_START_RE = re.compile(r"/[0-9]")


def run(
    cmd: str,
    cwd: Path = None,
    use_ros_env: bool = False,
    timeout_sec: Optional[int] = None,
) -> None:
    if use_ros_env:
        shell_cmd = (
            "set -eo pipefail; "
            "set +u; "
            "source /opt/ros/humble/setup.bash; "
            "source install/setup.bash; "
            + cmd
        )
    else:
        shell_cmd = "set -eo pipefail; " + cmd
    try:
        subprocess.run(
            ["bash", "-lc", shell_cmd],
            cwd=str(cwd) if cwd else None,
            check=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timeout after {timeout_sec}s: {cmd}") from e


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_abs(name: str) -> str:
    if not isinstance(name, str):
        return name
    if not name.startswith("/"):
        return "/" + name
    return name


def snake(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip("/"))
    return (s or "node").lower()


def ros_node_name(name: str) -> str:
    # ROS node name cannot contain '/'.
    s = snake(name)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "node"
    if s.startswith("__"):
        s = "n_" + s.lstrip("_")
    if not re.match(r"^[A-Za-z]", s):
        s = "n_" + s
    return s


def canonical_node_id(name: str) -> str:
    n = normalize_abs(name)
    if not isinstance(n, str) or not n:
        return n
    return "/" + ros_node_name(n)


def is_reserved_service_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    base = name.strip().rstrip("/").split("/")[-1]
    return base in RESERVED_SERVICE_BASENAMES


def is_valid_ros_graph_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    s = name.strip()
    if not s or not s.startswith("/"):
        return False
    if "?" in s:
        return False
    if "/~/" in s:
        return False
    if INVALID_NAME_CHARS_RE.search(s):
        return False
    if INVALID_TOKEN_START_RE.search(s):
        return False
    return True


def camel(name: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name.strip("/")) if p]
    return ("".join(p[:1].upper() + p[1:] for p in parts) or "Generated") + "Node"


def call_openai_compatible(api_base_url: str, api_key: str, model: str, prompt: str) -> Dict:
    url = api_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": "Output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                payload = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"HTTP {e.code}: {detail}")
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"model request failed for {model}: {last_err}")

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("No choices in model response")
    content = choices[0].get("message", {}).get("content", "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content).strip()
    return json.loads(content)


def call_model_with_fallback(api_base_url: str, api_key: str, model: str, fallback_models: List[str], prompt: str) -> (Dict, str):
    tried = []
    for m in [model] + [x for x in fallback_models if x != model]:
        try:
            return call_openai_compatible(api_base_url, api_key, m, prompt), m
        except Exception as e:
            tried.append(f"{m}: {e}")
    raise RuntimeError(f"All models failed: {tried}")


def repair_spec_shape(spec: Dict, repo_url: str, ref: str) -> Dict:
    if not isinstance(spec, dict):
        return spec
    if "schema_version" not in spec:
        spec["schema_version"] = "1.0"
    meta = spec.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("repo_url", repo_url)
    meta.setdefault("ref", ref)
    meta.setdefault("project_name", Path(repo_url.rstrip("/")).name or "unknown_project")
    meta.setdefault("language", ["cpp", "python"])
    spec["meta"] = meta
    gs = spec.get("graph_seed")
    if not isinstance(gs, dict):
        gs = {}
    gs.setdefault("nodes", [])
    gs.setdefault("topics", [])
    gs.setdefault("edges", [])
    gs.setdefault("node_semantics", [])
    gs.setdefault("semantic_edges", [])
    gs.setdefault("executors", [])
    gs.setdefault("callback_groups", [])
    gs.setdefault("assignments", [])
    spec["graph_seed"] = gs
    return spec


def _repo_id(repo_url: str, ref: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", repo_url.strip())
    rr = re.sub(r"[^a-zA-Z0-9._-]+", "_", ref.strip())
    return f"{base}__{rr}"


def ensure_repo_checkout(repo_url: str, ref: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = cache_dir / _repo_id(repo_url, ref)
    if repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            run(
                f"git -c http.version=HTTP/1.1 fetch --depth 1 origin {shlex.quote(ref)}",
                cwd=repo_dir,
                timeout_sec=240,
            )
            run("git reset --hard FETCH_HEAD", cwd=repo_dir, timeout_sec=120)
            return repo_dir
        except Exception:
            shutil.rmtree(repo_dir, ignore_errors=True)
    try:
        run(
            f"git clone --depth 1 --branch {shlex.quote(ref)} {shlex.quote(repo_url)} {shlex.quote(str(repo_dir))}",
            timeout_sec=420,
        )
    except Exception:
        run(
            f"git -c http.version=HTTP/1.1 clone --depth 1 --branch {shlex.quote(ref)} {shlex.quote(repo_url)} {shlex.quote(str(repo_dir))}",
            timeout_sec=420,
        )
    return repo_dir


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def extract_ros_evidence(repo_dir: Path, max_files: int = 5000, max_snippets: int = 1200) -> Dict:
    patterns = {
        "node_decl": re.compile(r'(?:(?:rclcpp::Node|Node|create_node)\s*\(\s*["\']([^"\']+)["\'])|(?:super\(\)\.__init__\(\s*["\']([^"\']+)["\'])'),
        "pub_topic": re.compile(r'create_publisher(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\']([^"\']+)["\']'),
        "sub_topic": re.compile(r'create_subscription(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\']([^"\']+)["\']'),
        "srv_server": re.compile(r'create_service(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\']([^"\']+)["\']'),
        "srv_client": re.compile(r'create_client(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\']([^"\']+)["\']'),
        "param_client": re.compile(r'(?:SyncParametersClient|AsyncParametersClient)'),
        "action_server": re.compile(r'create_server(?:<[^>]+>)?\s*\(\s*["\']([^"\']+)["\']'),
        "action_client": re.compile(r'create_client(?:<[^>]+>)?\s*\(\s*["\']([^"\']+)["\']'),
        "timer_cb": re.compile(r'create_wall_timer\s*\([^,]+,\s*std::bind\(\s*&[A-Za-z0-9_:]+::([A-Za-z0-9_]+)'),
        "executor_single": re.compile(r"SingleThreadedExecutor"),
        "executor_multi": re.compile(r"MultiThreadedExecutor"),
        "wait_for_service": re.compile(r"wait_for_service\s*\("),
        "future_get": re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:future|promise|result)[A-Za-z0-9_]*\s*\.\s*get\s*\("),
        "spin_until_future_complete": re.compile(r"spin_until_future_complete\s*\("),
        "py_timer_site": re.compile(r'create_timer\s*\(\s*[^,]+,\s*([A-Za-z_][A-Za-z0-9_\.]*)'),
        "cpp_timer_site": re.compile(r'create_wall_timer\s*\(\s*[^,]+,\s*(?:std::bind\([^:]+::([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))'),
        "cpp_sub_site": re.compile(r'create_subscription(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\'][^"\']+["\']\s*,\s*[^,]+,\s*([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE),
        "cpp_srv_site": re.compile(r'create_service(?:<[^>]+>)?\s*\(\s*(?:[^,]+,\s*)?["\'][^"\']+["\']\s*,\s*([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE),
        "py_sub_site": re.compile(r'create_subscription\s*\(\s*[^,]+,\s*["\'][^"\']+["\']\s*,\s*([^,\)]+)'),
        "py_srv_site": re.compile(r'create_service\s*\(\s*[^,]+,\s*["\'][^"\']+["\']\s*,\s*([^,\)]+)'),
        "py_client_site": re.compile(r'create_client\s*\(\s*[^,]+,\s*["\']([^"\']+)["\']'),
    }

    allow_ext = {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".py"}
    files: List[Path] = []
    for p in repo_dir.rglob("*"):
        if len(files) >= max_files:
            break
        if not p.is_file():
            continue
        if ".git" in p.parts:
            continue
        if p.suffix.lower() in allow_ext:
            files.append(p)

    nodes = set()
    topics_pub = set()
    topics_sub = set()
    services_server = set()
    services_client = set()
    actions_server = set()
    actions_client = set()
    timer_callbacks = set()
    blocking_calls = set()
    executor_hints = set()
    relations = set()
    callback_sites: List[Dict] = []
    blocking_sites: List[Dict] = []
    snippets: List[Dict] = []
    node_files: List[Dict] = []
    service_client_files: List[Dict] = []
    service_server_files: List[Dict] = []

    def abs_ros(n: str) -> str:
        s = str(n or "").strip()
        if not s:
            return s
        return s if s.startswith("/") else f"/{s}"

    for fp in files:
        txt = _read_file_safe(fp)
        if not txt:
            continue
        rel = str(fp.relative_to(repo_dir))
        lines = txt.splitlines()
        file_nodes = set()
        file_pubs = set()
        file_subs = set()
        seen_callback_sites = set()
        cap = False
        for i, line in enumerate(lines, start=1):
            # entity extraction
            mnode = patterns["node_decl"].search(line)
            if mnode:
                a = mnode.group(1) or mnode.group(2)
                if a:
                    nid = canonical_node_id(abs_ros(a))
                    nodes.add(nid)
                    file_nodes.add(nid)
                    cap = True
            for k, dst in (
                ("pub_topic", topics_pub),
                ("sub_topic", topics_sub),
                ("srv_server", services_server),
                ("srv_client", services_client),
                ("action_server", actions_server),
                ("action_client", actions_client),
            ):
                for m in patterns[k].finditer(line):
                    nm = abs_ros(m.group(1))
                    if not is_valid_ros_graph_name(nm):
                        continue
                    dst.add(nm)
                    if k == "pub_topic":
                        file_pubs.add(nm)
                    elif k == "sub_topic":
                        file_subs.add(nm)
                    elif k == "srv_client":
                        service_client_files.append({"file": rel, "service": nm, "line": i})
                    elif k == "srv_server":
                        service_server_files.append({"file": rel, "service": nm, "line": i})
                    cap = True
            if patterns["param_client"].search(line):
                services_client.add("/parameter_service")
                service_client_files.append({"file": rel, "service": "/parameter_service", "line": i})
                cap = True
            for m in patterns["timer_cb"].finditer(line):
                timer_callbacks.add(m.group(1))
                cap = True
            if patterns["wait_for_service"].search(line):
                blocking_calls.add("wait_for_service")
                blocking_sites.append({"file": rel, "line": i, "primitive": "wait_for_service", "code": line.strip()[:220]})
                cap = True
            if patterns["spin_until_future_complete"].search(line):
                blocking_calls.add("spin_until_future_complete")
                blocking_sites.append({"file": rel, "line": i, "primitive": "spin_until_future_complete", "code": line.strip()[:220]})
                cap = True
            if patterns["future_get"].search(line):
                blocking_calls.add("future_get")
                blocking_sites.append({"file": rel, "line": i, "primitive": "future_get", "code": line.strip()[:220]})
                cap = True
            if patterns["executor_single"].search(line):
                executor_hints.add("single_threaded")
                cap = True
            if patterns["executor_multi"].search(line):
                executor_hints.add("multi_threaded")
                cap = True

            m = patterns["py_timer_site"].search(line)
            if m:
                key = (rel, i, "timer", m.group(1).strip())
                if key not in seen_callback_sites:
                    seen_callback_sites.add(key)
                    callback_sites.append({"file": rel, "line": i, "kind": "timer", "callback_expr": m.group(1).strip(), "code": line.strip()[:220]})
            m = patterns["cpp_timer_site"].search(line)
            if m:
                cb_expr = (m.group(1) or m.group(2) or "").strip()
                if cb_expr:
                    key = (rel, i, "timer", cb_expr)
                    if key not in seen_callback_sites:
                        seen_callback_sites.add(key)
                        callback_sites.append({"file": rel, "line": i, "kind": "timer", "callback_expr": cb_expr, "code": line.strip()[:220]})
            m = patterns["py_sub_site"].search(line)
            if m:
                key = (rel, i, "subscription", m.group(1).strip())
                if key not in seen_callback_sites:
                    seen_callback_sites.add(key)
                    callback_sites.append({"file": rel, "line": i, "kind": "subscription", "callback_expr": m.group(1).strip(), "code": line.strip()[:220]})
            m = patterns["py_srv_site"].search(line)
            if m:
                key = (rel, i, "service", m.group(1).strip())
                if key not in seen_callback_sites:
                    seen_callback_sites.add(key)
                    callback_sites.append({"file": rel, "line": i, "kind": "service", "callback_expr": m.group(1).strip(), "code": line.strip()[:220]})
            m = patterns["py_client_site"].search(line)
            if m:
                svc = abs_ros(m.group(1))
                if is_valid_ros_graph_name(svc):
                    callback_sites.append({"file": rel, "line": i, "kind": "service_client", "service": svc, "code": line.strip()[:220]})

        for kind, pattern in (("subscription", patterns["cpp_sub_site"]), ("service", patterns["cpp_srv_site"])):
            for m in pattern.finditer(txt):
                line_no = txt.count("\n", 0, m.start()) + 1
                cb_expr = (m.group(1) or "").strip()
                key = (rel, line_no, kind, cb_expr)
                if not cb_expr or key in seen_callback_sites:
                    continue
                seen_callback_sites.add(key)
                code = lines[line_no - 1].strip()[:220] if 0 < line_no <= len(lines) else ""
                callback_sites.append({"file": rel, "line": line_no, "kind": kind, "callback_expr": cb_expr, "code": code})

            if cap and len(snippets) < max_snippets:
                snippets.append(
                    {
                        "file": rel,
                        "line": i,
                        "code": line.strip()[:240],
                    }
                )
                cap = False
        for n in file_nodes:
            node_files.append({"node": n, "file": rel})
            for t in file_pubs:
                relations.add((n, t, "pub", rel))
            for t in file_subs:
                relations.add((t, n, "sub", rel))

    evidence = {
        "repo_root": str(repo_dir),
        "counts": {
            "files_scanned": len(files),
            "nodes": len(nodes),
            "topics_pub": len(topics_pub),
            "topics_sub": len(topics_sub),
            "service_servers": len(services_server),
            "service_clients": len(services_client),
            "action_servers": len(actions_server),
            "action_clients": len(actions_client),
            "timer_callbacks": len(timer_callbacks),
            "blocking_calls": len(blocking_calls),
            "executor_hints": len(executor_hints),
            "snippets": len(snippets),
            "relations": len(relations),
            "callback_sites": len(callback_sites),
            "blocking_sites": len(blocking_sites),
        },
        "entities": {
            "nodes": sorted(nodes),
            "topics_pub": sorted(topics_pub),
            "topics_sub": sorted(topics_sub),
            "service_servers": sorted(services_server),
            "service_clients": sorted(services_client),
            "action_servers": sorted(actions_server),
            "action_clients": sorted(actions_client),
            "timer_callbacks": sorted(timer_callbacks),
            "blocking_calls": sorted(blocking_calls),
            "executor_hints": sorted(executor_hints),
        },
        "relations": [
            {"from": s, "to": t, "kind": k, "file": f}
            for (s, t, k, f) in sorted(relations)
        ],
        "callback_sites": callback_sites,
        "blocking_sites": blocking_sites,
        "node_files": node_files,
        "service_client_files": service_client_files,
        "service_server_files": service_server_files,
        "snippets": snippets,
    }
    return evidence


def compact_evidence_for_prompt(evidence: Dict, max_items: int = 300, max_snippets: int = 300) -> Dict:
    ents = evidence.get("entities", {})
    out_ents = {}
    for k, v in ents.items():
        if isinstance(v, list):
            out_ents[k] = v[:max_items]
        else:
            out_ents[k] = v
    return {
        "repo_root": evidence.get("repo_root"),
        "counts": evidence.get("counts", {}),
        "entities": out_ents,
        "callback_sites": (evidence.get("callback_sites", []) or [])[:max_items],
        "blocking_sites": (evidence.get("blocking_sites", []) or [])[:max_items],
        "node_files": (evidence.get("node_files", []) or [])[:max_items],
        "service_client_files": (evidence.get("service_client_files", []) or [])[:max_items],
        "service_server_files": (evidence.get("service_server_files", []) or [])[:max_items],
        "snippets": (evidence.get("snippets", []) or [])[:max_snippets],
        "truncation": {
            "entities_max_items": max_items,
            "snippets_max_items": max_snippets,
        },
    }


def synthesize_semantics_from_evidence(gs: Dict, evidence: Dict) -> None:
    file_to_nodes: Dict[str, set] = {}
    for item in evidence.get("node_files", []) or []:
        if not isinstance(item, dict):
            continue
        node = canonical_node_id(item.get("node", ""))
        file = str(item.get("file", "")).strip()
        if node and file:
            file_to_nodes.setdefault(file, set()).add(node)
    for rel in evidence.get("relations", []) or []:
        if not isinstance(rel, dict):
            continue
        file = str(rel.get("file", "")).strip()
        kind = str(rel.get("kind", "")).strip()
        if not file or kind not in {"pub", "sub"}:
            continue
        node = canonical_node_id(rel.get("from", "")) if kind == "pub" else canonical_node_id(rel.get("to", ""))
        if node:
            file_to_nodes.setdefault(file, set()).add(node)

    blocking_by_file: Dict[str, set] = {}
    for item in evidence.get("blocking_sites", []) or []:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        primitive = str(item.get("primitive", "")).strip()
        if file and primitive:
            blocking_by_file.setdefault(file, set()).add(primitive)

    callbacks_by_file: Dict[str, List[Dict]] = {}
    service_clients_by_file: Dict[str, set] = {}
    service_servers_by_file: Dict[str, set] = {}
    for item in evidence.get("callback_sites", []) or []:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        kind = str(item.get("kind", "")).strip()
        if not file or not kind:
            continue
        callbacks_by_file.setdefault(file, []).append(item)
        if kind == "service_client":
            service = normalize_abs(item.get("service", ""))
            if service and is_valid_ros_graph_name(service) and not is_reserved_service_name(service):
                service_clients_by_file.setdefault(file, set()).add(service)
    for item in evidence.get("service_client_files", []) or []:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        service = normalize_abs(item.get("service", ""))
        if file and service and is_valid_ros_graph_name(service) and not is_reserved_service_name(service):
            service_clients_by_file.setdefault(file, set()).add(service)
    for item in evidence.get("service_server_files", []) or []:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        service = normalize_abs(item.get("service", ""))
        if file and service and is_valid_ros_graph_name(service) and not is_reserved_service_name(service):
            service_servers_by_file.setdefault(file, set()).add(service)

    sem_by_node = {}
    for item in gs.get("node_semantics", []):
        if not isinstance(item, dict):
            continue
        node = canonical_node_id(item.get("name", ""))
        if node:
            sem_by_node[node] = item

    hints = {str(x) for x in (evidence.get("entities", {}) or {}).get("executor_hints", []) if isinstance(x, str)}
    inferred_executor = "single_threaded" if "single_threaded" in hints else ("multi_threaded" if "multi_threaded" in hints else "unknown")

    def ensure_sem(node: str) -> Dict:
        sem = sem_by_node.get(node)
        if sem is None:
            sem = {
                "name": node,
                "executor": inferred_executor,
                "callback_sources": [],
                "service_clients": [],
                "service_servers": [],
                "blocking_calls": [],
                "callbacks": [],
                "callback_wait_edges": [],
                "semantic_edges": [],
            }
            sem_by_node[node] = sem
        for key, default in (
            ("callback_sources", []),
            ("service_clients", []),
            ("service_servers", []),
            ("blocking_calls", []),
            ("callbacks", []),
            ("callback_wait_edges", []),
            ("semantic_edges", []),
        ):
            sem.setdefault(key, default.copy() if isinstance(default, list) else default)
        if str(sem.get("executor", "unknown")) == "unknown" and inferred_executor != "unknown":
            sem["executor"] = inferred_executor
        return sem

    for file, nodes in file_to_nodes.items():
        blocking_calls = sorted(blocking_by_file.get(file, set()))
        service_clients = sorted(service_clients_by_file.get(file, set()))
        callback_sites = [x for x in callbacks_by_file.get(file, []) if str(x.get("kind", "")) in {"timer", "subscription", "service", "action"}]
        for node in sorted(nodes):
            sem = ensure_sem(node)
            sem["blocking_calls"] = sorted(set(sem.get("blocking_calls", [])) | set(blocking_calls))
            sem["service_clients"] = sorted(set(sem.get("service_clients", [])) | set(service_clients))
            sem["service_servers"] = sorted(set(sem.get("service_servers", [])) | set(service_servers_by_file.get(file, set())))
            existing_ids = {str(cb.get("id", "")).strip() for cb in sem.get("callbacks", []) if isinstance(cb, dict)}
            for cb in callback_sites:
                kind = str(cb.get("kind", "unknown")).strip() or "unknown"
                line = int(cb.get("line", 0) or 0)
                cb_id = f"cb_{kind}_{line}" if line > 0 else f"cb_{kind}_auto"
                if cb_id in existing_ids:
                    continue
                existing_ids.add(cb_id)
                sem["callback_sources"] = sorted(set(sem.get("callback_sources", [])) | {kind})
                sem["callbacks"].append(
                    {
                        "id": cb_id,
                        "source": kind,
                        "callback_group": f"cg::{node}::default",
                        "waits_for_callbacks": [],
                        "waits_for_services": service_clients if blocking_calls else [],
                        "blocking_calls": blocking_calls,
                    }
                )
            if not sem.get("callbacks") and blocking_calls and service_clients:
                sem["callbacks"].append(
                    {
                        "id": "cb_blocking_auto",
                        "source": "unknown",
                        "callback_group": f"cg::{node}::default",
                        "waits_for_callbacks": [],
                        "waits_for_services": service_clients,
                        "blocking_calls": blocking_calls,
                    }
                )

    for sem in sem_by_node.values():
        node_blocking = sorted(set(str(x) for x in sem.get("blocking_calls", []) if str(x)))
        node_services = sorted(
            set(
                normalize_abs(x)
                for x in sem.get("service_clients", [])
                if isinstance(x, str) and normalize_abs(x) and not is_reserved_service_name(normalize_abs(x))
            )
        )
        callbacks = [cb for cb in sem.get("callbacks", []) if isinstance(cb, dict)]
        if not callbacks or not node_blocking or not node_services:
            continue
        if any(cb.get("blocking_calls") or cb.get("waits_for_services") for cb in callbacks):
            continue
        priority = {"service": 0, "timer": 1, "action": 2, "unknown": 3, "subscription": 4}
        target_cb = sorted(
            callbacks,
            key=lambda cb: (
                priority.get(str(cb.get("source", "unknown")), 9),
                str(cb.get("id", "")),
            ),
        )[0]
        cb_blocking = sorted(set(str(x) for x in target_cb.get("blocking_calls", []) if str(x)) | set(node_blocking))
        cb_services = sorted(
            set(normalize_abs(x) for x in target_cb.get("waits_for_services", []) if isinstance(x, str) and normalize_abs(x)) |
            set(node_services)
        )
        target_cb["blocking_calls"] = cb_blocking
        target_cb["waits_for_services"] = cb_services

    gs["node_semantics"] = [sem_by_node[n] for n in sorted(sem_by_node)]

    executors = {str(x.get("id")): x for x in gs.get("executors", []) if isinstance(x, dict) and x.get("id")}
    callback_groups = {str(x.get("id")): x for x in gs.get("callback_groups", []) if isinstance(x, dict) and x.get("id")}
    assignment_pairs = {(str(x.get("callback_group")), str(x.get("executor"))) for x in gs.get("assignments", []) if isinstance(x, dict)}
    for node, sem in sem_by_node.items():
        if not sem.get("callbacks"):
            continue
        ex_id = f"exec::{node}"
        cg_id = f"cg::{node}::default"
        executors.setdefault(
            ex_id,
            {
                "id": ex_id,
                "kind": "SingleThreadedExecutor" if sem.get("executor") == "single_threaded" else "UnknownExecutor",
                "thread_capacity": 1,
                "origin": "inferred",
            },
        )
        callback_groups.setdefault(
            cg_id,
            {
                "id": cg_id,
                "node": node,
                "type": "MutuallyExclusive" if sem.get("executor") == "single_threaded" else "Unknown",
                "executor": ex_id,
                "origin": "inferred",
            },
        )
        assignment_pairs.add((cg_id, ex_id))
    gs["executors"] = sorted(executors.values(), key=lambda x: x["id"])
    gs["callback_groups"] = sorted(callback_groups.values(), key=lambda x: x["id"])
    gs["assignments"] = [
        {"callback_group": cg, "executor": ex, "origin": "inferred"}
        for (cg, ex) in sorted(assignment_pairs)
    ]


def validate_spec(spec: Dict, evidence: Optional[Dict] = None) -> List[str]:
    errs: List[str] = []
    for k in ["schema_version", "meta", "graph_seed"]:
        if k not in spec:
            errs.append(f"missing {k}")

    gs = spec.get("graph_seed", {})
    gs.setdefault("nodes", [])
    gs.setdefault("topics", [])
    gs.setdefault("edges", [])
    gs.setdefault("node_semantics", [])
    gs.setdefault("semantic_edges", [])
    gs.setdefault("executors", [])
    gs.setdefault("callback_groups", [])
    gs.setdefault("assignments", [])
    gen = spec.get("generation", {})
    gen.setdefault("cpp_nodes", gen.get("python_nodes", []))

    if evidence:
        ents = evidence.get("entities", {})
        rels = evidence.get("relations", [])
        ev_nodes = [canonical_node_id(x) for x in ents.get("nodes", []) if isinstance(x, str)]
        ev_topics = [normalize_abs(x) for x in (ents.get("topics_pub", []) + ents.get("topics_sub", [])) if isinstance(x, str) and is_valid_ros_graph_name(normalize_abs(x))]
        gs["nodes"] = sorted(set(gs.get("nodes", [])) | set(ev_nodes))
        gs["topics"] = sorted(set(gs.get("topics", [])) | set(ev_topics))
        merged_edges = list(gs.get("edges", []))
        seen = {(e.get("from"), e.get("to"), e.get("kind")) for e in merged_edges if isinstance(e, dict)}
        for e in rels:
            if not isinstance(e, dict):
                continue
            edge = (e.get("from"), e.get("to"), e.get("kind"))
            if edge in seen:
                continue
            if edge[2] not in ("pub", "sub"):
                continue
            merged_edges.append({"from": edge[0], "to": edge[1], "kind": edge[2]})
            seen.add(edge)
        gs["edges"] = merged_edges
        synthesize_semantics_from_evidence(gs, evidence)

    # normalize
    gs["nodes"] = [canonical_node_id(x) for x in gs["nodes"] if isinstance(x, str)]
    gs["topics"] = [normalize_abs(x) for x in gs["topics"] if isinstance(x, str) and is_valid_ros_graph_name(normalize_abs(x))]

    seed_nodes = set(gs["nodes"])
    norm_edges = []
    seen_edges = set()
    derived_nodes = set()
    derived_topics = set()
    for e in gs["edges"]:
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        f = normalize_abs(e.get("from", ""))
        t = normalize_abs(e.get("to", ""))
        if k not in ("pub", "sub") or not f or not t:
            continue
        if not is_valid_ros_graph_name(t if k == "pub" else f):
            continue

        # Enforce bipartite semantics by edge kind.
        node_name = canonical_node_id(f if k == "pub" else t)
        topic_name = t if k == "pub" else f
        if topic_name in RESERVED_TOPICS:
            continue

        ef = node_name if k == "pub" else topic_name
        et = topic_name if k == "pub" else node_name
        # If seed provides explicit node set, only keep edges whose node endpoint
        # belongs to seed nodes. This prevents topic-like names from being promoted
        # to generated executable nodes.
        if seed_nodes and node_name not in seed_nodes:
            continue
        edge = (ef, et, k)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        norm_edges.append({"from": ef, "to": et, "kind": k})
        derived_nodes.add(node_name)
        derived_topics.add(topic_name)

    gs["edges"] = sorted(norm_edges, key=lambda x: (x["from"], x["to"], x["kind"]))
    # Use seed node set as canonical node universe when provided.
    gs["nodes"] = sorted(seed_nodes if seed_nodes else derived_nodes)
    gs["topics"] = sorted(derived_topics)

    def _norm_executor(v: str) -> str:
        s = str(v or "unknown").strip().lower()
        if s in ("single", "singlethreaded", "single_threaded"):
            return "single_threaded"
        if s in ("multi", "multithreaded", "multi_threaded"):
            return "multi_threaded"
        return "unknown"

    def _norm_endpoint(v: str, node_ctx: str = "") -> str:
        s = str(v or "").strip()
        if not s:
            return ""
        if s.startswith("resource:") or s.startswith("future:"):
            return s
        if ":" in s and s.startswith("/"):
            head, tail = s.split(":", 1)
            return f"{canonical_node_id(head)}:{tail}"
        if ":" in s and (not s.startswith("/")) and node_ctx:
            # Local callback id like "cb_timer" -> "/node:cb_timer"
            if "/" not in s.split(":", 1)[0]:
                return f"{node_ctx}:{s}"
        if s.startswith("/"):
            return canonical_node_id(s)
        return normalize_abs(s)

    def _norm_relation(v: str) -> str:
        s = str(v or "").strip()
        allowed = {"holds", "waits_for", "requests", "completion_depends_on", "scheduled_by"}
        return s if s in allowed else "waits_for"

    def _norm_edge_type(v: str) -> str:
        s = str(v or "").strip()
        return s if s in ("blocking", "potential", "executor_block") else "potential"

    # Parse concurrency semantics from graph_seed first.
    semantics_by_name = {}
    for s in gs.get("node_semantics", []):
        if not isinstance(s, dict):
            continue
        nn = canonical_node_id(s.get("name", ""))
        if not nn:
            continue
        callbacks = []
        for cb in s.get("callbacks", []):
            if not isinstance(cb, dict):
                continue
            cb_id = str(cb.get("id", "")).strip()
            if not cb_id:
                continue
            waits_for_callbacks = [
                normalize_abs(x)
                for x in cb.get("waits_for_callbacks", [])
                if isinstance(x, str) and normalize_abs(x)
            ]
            waits_for_services = [
                normalize_abs(x)
                for x in cb.get("waits_for_services", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x)) and is_valid_ros_graph_name(normalize_abs(x))
            ]
            cb_blocking = [str(x) for x in cb.get("blocking_calls", [])]
            callbacks.append({
                "id": cb_id,
                "source": str(cb.get("source", "unknown")),
                "callback_group": str(cb.get("callback_group", "")).strip(),
                "waits_for_callbacks": waits_for_callbacks,
                "waits_for_services": waits_for_services,
                "blocking_calls": cb_blocking,
            })

        sem_edges = []
        for se in s.get("semantic_edges", []):
            if not isinstance(se, dict):
                continue
            src = _norm_endpoint(se.get("from", ""), nn)
            dst = _norm_endpoint(se.get("to", ""), nn)
            if not src or not dst:
                continue
            sem_edges.append(
                {
                    "from": src,
                    "to": dst,
                    "relation": _norm_relation(se.get("relation", "waits_for")),
                    "type": _norm_edge_type(se.get("type", "potential")),
                    "deadlock_class": str(se.get("deadlock_class", "callback_blocking")),
                    "reason": str(se.get("reason", "")),
                    "provenance": str(se.get("provenance", "grounded")),
                    "confidence": str(se.get("confidence", "medium")),
                    "definite_eligible": bool(se.get("definite_eligible", False)),
                    "model_uncertainty": bool(se.get("model_uncertainty", False)),
                }
            )

        semantics_by_name[nn] = {
            "service_servers": [
                normalize_abs(x)
                for x in s.get("service_servers", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x)) and is_valid_ros_graph_name(normalize_abs(x))
            ],
            "service_clients": [
                normalize_abs(x)
                for x in s.get("service_clients", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x))
            ],
            "blocking_calls": [str(x) for x in s.get("blocking_calls", [])],
            "executor": _norm_executor(s.get("executor", "unknown")),
            "callback_sources": [str(x) for x in s.get("callback_sources", [])],
            "callbacks": callbacks,
            "callback_wait_edges": [x for x in s.get("callback_wait_edges", []) if isinstance(x, dict)],
            "semantic_edges": sem_edges,
        }

    # Graph-level semantic edges
    normalized_semantic_edges = []
    for se in gs.get("semantic_edges", []):
        if not isinstance(se, dict):
            continue
        src = _norm_endpoint(se.get("from", ""))
        dst = _norm_endpoint(se.get("to", ""))
        if not src or not dst:
            continue
        normalized_semantic_edges.append(
            {
                "from": src,
                "to": dst,
                "relation": _norm_relation(se.get("relation", "waits_for")),
                "type": _norm_edge_type(se.get("type", "potential")),
                "deadlock_class": str(se.get("deadlock_class", "callback_blocking")),
                "reason": str(se.get("reason", "")),
                "provenance": str(se.get("provenance", "grounded")),
                "confidence": str(se.get("confidence", "medium")),
                "definite_eligible": bool(se.get("definite_eligible", False)),
                "model_uncertainty": bool(se.get("model_uncertainty", False)),
            }
        )
    gs["semantic_edges"] = normalized_semantic_edges

    # Normalize model context blocks.
    norm_executors = []
    for ex in gs.get("executors", []):
        if not isinstance(ex, dict):
            continue
        ex_id = str(ex.get("id", "")).strip()
        if not ex_id:
            continue
        cap = ex.get("thread_capacity", 1)
        try:
            cap = int(cap)
        except Exception:
            cap = 1
        norm_executors.append(
            {
                "id": ex_id,
                "kind": str(ex.get("kind", "UnknownExecutor")).strip() or "UnknownExecutor",
                "thread_capacity": max(1, cap),
                "origin": str(ex.get("origin", "explicit")).strip() or "explicit",
            }
        )
    gs["executors"] = sorted(norm_executors, key=lambda x: x["id"])

    norm_callback_groups = []
    for cg in gs.get("callback_groups", []):
        if not isinstance(cg, dict):
            continue
        cg_id = str(cg.get("id", "")).strip()
        node_name = canonical_node_id(cg.get("node", ""))
        if not cg_id or not node_name:
            continue
        norm_callback_groups.append(
            {
                "id": cg_id,
                "node": node_name,
                "type": str(cg.get("type", "Unknown")).strip() or "Unknown",
                "executor": str(cg.get("executor", "")).strip(),
                "origin": str(cg.get("origin", "explicit")).strip() or "explicit",
            }
        )
    gs["callback_groups"] = sorted(norm_callback_groups, key=lambda x: x["id"])

    norm_assignments = []
    for a in gs.get("assignments", []):
        if not isinstance(a, dict):
            continue
        cg = str(a.get("callback_group", "")).strip()
        ex = str(a.get("executor", "")).strip()
        if not cg or not ex:
            continue
        norm_assignments.append(
            {
                "callback_group": cg,
                "executor": ex,
                "origin": str(a.get("origin", "explicit")).strip() or "explicit",
            }
        )
    gs["assignments"] = sorted(norm_assignments, key=lambda x: (x["callback_group"], x["executor"]))

    # Write normalized node_semantics back to graph_seed.
    gs["node_semantics"] = [
        {
            "name": nn,
            "executor": sem.get("executor", "unknown"),
            "callback_sources": sem.get("callback_sources", []),
            "service_clients": sem.get("service_clients", []),
            "service_servers": sem.get("service_servers", []),
            "blocking_calls": sem.get("blocking_calls", []),
            "callbacks": sem.get("callbacks", []),
            "callback_wait_edges": sem.get("callback_wait_edges", []),
            "semantic_edges": sem.get("semantic_edges", []),
        }
        for nn, sem in sorted(semantics_by_name.items())
    ]

    # Parse optional generation section for backward compatibility.
    llm_gen_by_name = {}
    for n in gen.get("cpp_nodes", []):
        if isinstance(n, str):
            n = {"name": n}
        if not isinstance(n, dict):
            continue
        nn = canonical_node_id(n.get("name", ""))
        if not nn:
            continue
        llm_gen_by_name[nn] = {
            "publishes": [
                normalize_abs(x)
                for x in n.get("publishes", [])
                if isinstance(x, str) and normalize_abs(x) not in RESERVED_TOPICS
            ],
            "subscribes": [
                normalize_abs(x)
                for x in n.get("subscribes", [])
                if isinstance(x, str) and normalize_abs(x) not in RESERVED_TOPICS
            ],
            "service_servers": [
                normalize_abs(x)
                for x in n.get("service_servers", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x)) and is_valid_ros_graph_name(normalize_abs(x))
            ],
            "service_clients": [
                normalize_abs(x)
                for x in n.get("service_clients", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x)) and is_valid_ros_graph_name(normalize_abs(x))
            ],
            "blocking_calls": [str(x) for x in n.get("blocking_calls", [])],
        }

    # Merge semantics as preferred source for wait-related metadata.
    for nn, sem in semantics_by_name.items():
        if nn not in llm_gen_by_name:
            llm_gen_by_name[nn] = {
                "publishes": [],
                "subscribes": [],
                "service_servers": [],
                "service_clients": [],
                "blocking_calls": [],
                "executor": "unknown",
                "callback_sources": [],
                "callbacks": [],
                "callback_wait_edges": [],
                "semantic_edges": [],
            }
        llm_gen_by_name[nn]["service_servers"] = sorted(
            set(llm_gen_by_name[nn].get("service_servers", [])) | set(sem.get("service_servers", []))
        )
        llm_gen_by_name[nn]["service_clients"] = sorted(
            set(llm_gen_by_name[nn].get("service_clients", [])) | set(sem.get("service_clients", []))
        )
        if sem.get("blocking_calls"):
            llm_gen_by_name[nn]["blocking_calls"] = sem.get("blocking_calls", [])
        llm_gen_by_name[nn]["executor"] = sem.get("executor", "unknown")
        llm_gen_by_name[nn]["callback_sources"] = sem.get("callback_sources", [])
        llm_gen_by_name[nn]["callbacks"] = sem.get("callbacks", [])
        llm_gen_by_name[nn]["callback_wait_edges"] = sem.get("callback_wait_edges", [])
        llm_gen_by_name[nn]["semantic_edges"] = sem.get("semantic_edges", [])

    # If edges missing, synthesize from optional generation channels.
    if len(gs["edges"]) == 0:
        topics = set(gs["topics"])
        nodes = set(gs["nodes"])
        edges = []
        for node_name, meta in llm_gen_by_name.items():
            n_pubs = meta.get("publishes", [])
            n_subs = meta.get("subscribes", [])
            nodes.add(node_name)
            for t in n_pubs:
                if t in RESERVED_TOPICS or not is_valid_ros_graph_name(t):
                    continue
                topics.add(t)
                edges.append({"from": node_name, "to": t, "kind": "pub"})
            for t in n_subs:
                if t in RESERVED_TOPICS or not is_valid_ros_graph_name(t):
                    continue
                topics.add(t)
                edges.append({"from": t, "to": node_name, "kind": "sub"})

        # Legacy fallback: if old LLM generation contains publish/subscribe fields.
        for n in gen.get("cpp_nodes", []):
            if not isinstance(n, dict):
                continue
            node_name = normalize_abs(n.get("name", ""))
            node_name = canonical_node_id(node_name)
            if not node_name:
                continue
            nodes.add(node_name)
            for t in n.get("publishes", []):
                if t in RESERVED_TOPICS or not is_valid_ros_graph_name(t):
                    continue
                topics.add(t)
                edges.append({"from": node_name, "to": t, "kind": "pub"})
            for t in n.get("subscribes", []):
                if t in RESERVED_TOPICS or not is_valid_ros_graph_name(t):
                    continue
                topics.add(t)
                edges.append({"from": t, "to": node_name, "kind": "sub"})
        gs["nodes"] = sorted(nodes)
        gs["topics"] = sorted(topics)
        gs["edges"] = edges

    # Build generation.cpp_nodes directly from seed graph (single source of truth).
    cpp_by_name = {}

    pub_map = {n: set() for n in gs["nodes"]}
    sub_map = {n: set() for n in gs["nodes"]}
    for e in gs["edges"]:
        k = e.get("kind")
        f = e.get("from")
        t = e.get("to")
        if k == "pub" and f in pub_map:
            pub_map[f].add(t)
        elif k == "sub" and t in sub_map:
            sub_map[t].add(f)

    for n in gs["nodes"]:
        llm_meta = llm_gen_by_name.get(n, {})
        sem = semantics_by_name.get(n, {})
        cpp_by_name[n] = {
            "name": n,
            "publishes": sorted(pub_map.get(n, set())),
            "subscribes": sorted(sub_map.get(n, set())),
            "service_servers": sorted(set(llm_meta.get("service_servers", []))),
            "service_clients": sorted(set(llm_meta.get("service_clients", []))),
            "blocking_calls": llm_meta.get("blocking_calls", ["wait_for_service", "sleep"]),
            "executor": llm_meta.get("executor", "unknown"),
            "callback_sources": llm_meta.get("callback_sources", []),
            "callbacks": llm_meta.get("callbacks", []),
            "callback_wait_edges": llm_meta.get("callback_wait_edges", []),
            "semantic_edges": llm_meta.get("semantic_edges", []),
        }

    gen["cpp_nodes"] = [cpp_by_name[n] for n in sorted(cpp_by_name.keys())]

    if len(gs["nodes"]) == 0:
        errs.append("graph_seed.nodes empty")
    # edges can legitimately be empty for repos where this pass extracts
    # mostly node-level evidence (e.g., constructor names without explicit topic wiring).
    # keep it valid as long as node universe is preserved.

    spec["graph_seed"] = gs
    gen["target_package"] = gen.get("target_package") or "generated_ros2_pkg"
    spec["generation"] = gen
    return errs


def generate_cpp_pkg(spec: Dict, ws: Path, package: str, force: bool) -> Path:
    pkg_dir = ws / "src" / package
    src_dir = pkg_dir / "src"
    launch_dir = pkg_dir / "launch"

    if pkg_dir.exists() and force:
        shutil.rmtree(pkg_dir)
    elif pkg_dir.exists() and not force:
        raise RuntimeError(f"package exists: {pkg_dir} (use --force)")

    src_dir.mkdir(parents=True, exist_ok=True)
    launch_dir.mkdir(parents=True, exist_ok=True)

    nodes = spec["generation"]["cpp_nodes"]

    exe_names = []
    launch_entries = []

    for node in nodes:
        n = node["name"]
        cls = camel(n)
        stem = snake(n)
        exe = stem
        exe_names.append(exe)

        pub_init = []
        sub_init = []
        srv_server_init = []
        srv_client_init = []
        timer_init = []
        callbacks = []
        members = []
        has_wait = "wait_for_service" in set(node["blocking_calls"])
        has_sleep = "sleep" in set(node["blocking_calls"])
        exec_mode = str(node.get("executor", "unknown")).lower()
        if exec_mode == "multi_threaded":
            spin_block = textwrap.indent(
                """
rclcpp::executors::MultiThreadedExecutor exec;
exec.add_node(node);
exec.spin();
""".strip(),
                "  ",
            )
        else:
            spin_block = textwrap.indent(
                """
rclcpp::executors::SingleThreadedExecutor exec;
exec.add_node(node);
exec.spin();
""".strip(),
                "  ",
            )

        valid_pubs = [t for t in node["publishes"] if is_valid_ros_graph_name(t)]
        valid_subs = [t for t in node["subscribes"] if is_valid_ros_graph_name(t)]
        valid_service_servers = [s for s in node["service_servers"] if is_valid_ros_graph_name(s)]
        valid_service_clients = [s for s in node["service_clients"] if is_valid_ros_graph_name(s)]

        for i, t in enumerate(valid_pubs):
            pub_init.append(f'    pub_{i}_ = this->create_publisher<std_msgs::msg::String>("{t}", 10);')
            cb_lines = [
                f"  void on_pub_timer_{i}()",
                "  {",
                "    std_msgs::msg::String msg;",
                '    msg.data = "tick";',
                f"    pub_{i}_->publish(msg);",
            ]
            if has_sleep:
                cb_lines.append("    rclcpp::sleep_for(5ms);")
            cb_lines.append("  }")
            callbacks.append("\n".join(cb_lines))
            timer_init.append(f'    timer_pub_{i}_ = this->create_wall_timer(500ms, std::bind(&{cls}::on_pub_timer_{i}, this));')
            members.append(f'  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_{i}_;')
            members.append(f'  rclcpp::TimerBase::SharedPtr timer_pub_{i}_;')

        for i, t in enumerate(valid_subs):
            sub_init.append(
                f'    sub_{i}_ = this->create_subscription<std_msgs::msg::String>("{t}", 10, std::bind(&{cls}::on_sub_{i}, this, std::placeholders::_1));'
            )
            cb_lines = [
                f"  void on_sub_{i}(const std_msgs::msg::String::SharedPtr msg)",
                "  {",
                "    (void)msg;",
            ]
            if has_sleep:
                cb_lines.append("    rclcpp::sleep_for(5ms);")
            cb_lines.append("  }")
            callbacks.append("\n".join(cb_lines))
            members.append(f'  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_{i}_;')

        for i, s in enumerate(valid_service_servers):
            srv_server_init.append(
                f'    srv_server_{i}_ = this->create_service<std_srvs::srv::Trigger>("{s}", std::bind(&{cls}::on_srv_server_{i}, this, std::placeholders::_1, std::placeholders::_2));'
            )
            callbacks.append(
                f'''  void on_srv_server_{i}(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)\n  {{\n    (void)req;\n    resp->success = true;\n    resp->message = "ok";\n  }}'''
            )
            members.append(f'  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_{i}_;')

        for i, s in enumerate(valid_service_clients):
            srv_client_init.append(
                f'    srv_client_{i}_ = this->create_client<std_srvs::srv::Trigger>("{s}");'
            )
            cb = [f'  void on_srv_client_timer_{i}()', '  {']
            if has_wait:
                cb.append(f'    if (!srv_client_{i}_->wait_for_service(200ms)) return;')
            else:
                cb.append(f'    if (!srv_client_{i}_->service_is_ready()) return;')
            cb.append('    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();')
            cb.append(f'    auto fut = srv_client_{i}_->async_send_request(req);')
            if has_sleep:
                cb.append('    rclcpp::sleep_for(20ms);')
            cb.append('    (void)fut;')
            cb.append('  }')
            callbacks.append("\n".join(cb))
            timer_init.append(f'    timer_srv_client_{i}_ = this->create_wall_timer(1000ms, std::bind(&{cls}::on_srv_client_timer_{i}, this));')
            members.append(f'  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_{i}_;')
            members.append(f'  rclcpp::TimerBase::SharedPtr timer_srv_client_{i}_;')

        code = CPP_NODE_TEMPLATE.format(
            class_name=cls,
            node_name=ros_node_name(n),
            pub_init="\n".join(pub_init) if pub_init else "",
            sub_init="\n".join(sub_init) if sub_init else "",
            srv_server_init="\n".join(srv_server_init) if srv_server_init else "",
            srv_client_init="\n".join(srv_client_init) if srv_client_init else "",
            timer_init="\n".join(timer_init) if timer_init else "",
            callbacks="\n\n".join(callbacks) if callbacks else "  void idle() {}",
            members="\n".join(members) if members else "  int unused_ = 0;",
            spin_block=spin_block,
        )
        (src_dir / f"{stem}.cpp").write_text(code, encoding="utf-8")
        launch_entries.append(
            f'''        Node(package="{package}", executable="{exe}", namespace="{GENERATED_NAMESPACE}", name="{ros_node_name(n)}"),'''
        )

    cmake = [
        "cmake_minimum_required(VERSION 3.8)",
        f"project({package})",
        "",
        "find_package(ament_cmake REQUIRED)",
        "find_package(rclcpp REQUIRED)",
        "find_package(std_msgs REQUIRED)",
        "find_package(std_srvs REQUIRED)",
        "",
    ]
    for exe in exe_names:
        cmake.append(f"add_executable({exe} src/{exe}.cpp)")
        cmake.append(f"ament_target_dependencies({exe} rclcpp std_msgs std_srvs)")
        cmake.append("")
    cmake.append("install(TARGETS")
    for exe in exe_names:
        cmake.append(f"  {exe}")
    cmake.append("  DESTINATION lib/${PROJECT_NAME})")
    cmake.append("")
    cmake.append("install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})")
    cmake.append("ament_package()")
    (pkg_dir / "CMakeLists.txt").write_text("\n".join(cmake) + "\n", encoding="utf-8")

    package_xml = f'''<?xml version="1.0"?>
<package format="3">
  <name>{package}</name>
  <version>0.1.0</version>
  <description>Generated ROS2 C++ package from LLM spec</description>
  <maintainer email="dev@example.com">dev</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <depend>rclcpp</depend>
  <depend>std_msgs</depend>
  <depend>std_srvs</depend>
  <depend>launch_ros</depend>
  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
'''
    (pkg_dir / "package.xml").write_text(package_xml, encoding="utf-8")

    launch_py = LAUNCH_TEMPLATE.format(node_entries="\n".join(launch_entries))
    (launch_dir / "generated_system.launch.py").write_text(launch_py, encoding="utf-8")

    return pkg_dir


def build_and_extract(
    ws: Path,
    package: str,
    duration: int,
    ros_domain_id: int = None,
    build_workers: int = 1,
    build_jobs: int = 1,
    build_timeout_sec: int = 900,
    startup_timeout_sec: int = 20,
    dynamic_timeout_sec: int = 120,
    spec_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
) -> None:
    step_log = ws / "graph" / "pipeline_steps.log"

    def log_step(msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        step_log.parent.mkdir(parents=True, exist_ok=True)
        with step_log.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")

    log_step("build:start")
    workers = max(1, int(build_workers))
    jobs = max(1, int(build_jobs))
    run(
        f"export CMAKE_BUILD_PARALLEL_LEVEL={jobs}; "
        f"export MAKEFLAGS=-j{jobs}; "
        f"colcon build --executor sequential --parallel-workers {workers} --packages-select {shlex.quote(package)}",
        cwd=ws,
        use_ros_env=True,
        timeout_sec=build_timeout_sec,
    )
    log_step("build:done")

    # Isolate this run from other ROS2 systems in the same machine/session.
    if ros_domain_id is None:
        ros_domain_id = random.randint(100, 230)

    spec_path = spec_path or (ws / "graph" / "llm_project_spec.json")
    source_root = source_root or (ws / "src")

    log_step("static_graph:start")
    run(
        f"python3 tools/ros2_graph_static_dump.py --workspace {shlex.quote(str(ws))} --package {shlex.quote(package)} > graph/graph_static.json",
        cwd=ws,
        use_ros_env=True,
        timeout_sec=120,
    )
    log_step("static_graph:done")
    log_step("wait_for:start")
    run(
        f"python3 tools/ros2_wait_for_graph_static.py --graph graph/graph_static.json --spec {shlex.quote(str(spec_path))} --workspace {shlex.quote(str(ws))} --source-root {shlex.quote(str(source_root))} --output graph/wait_for_graph_static.json",
        cwd=ws,
        use_ros_env=True,
        timeout_sec=120,
    )
    log_step("wait_for:done")

    launch_cmd = f"ros2 launch {shlex.quote(package)} generated_system.launch.py"
    launch_env = os.environ.copy()
    launch_env["ROS_DOMAIN_ID"] = str(ros_domain_id)
    ros_log_dir = ws / "graph" / "ros_logs"
    ros_log_dir.mkdir(parents=True, exist_ok=True)
    launch_env["ROS_LOG_DIR"] = str(ros_log_dir)
    proc = subprocess.Popen(
        [
            "bash",
            "-lc",
            f"export ROS_LOG_DIR={shlex.quote(str(ros_log_dir))}; source /opt/ros/humble/setup.bash; source install/setup.bash; {launch_cmd}",
        ],
        cwd=str(ws),
        env=launch_env,
    )
    try:
        log_step("launch:start")
        t0 = time.monotonic()
        while time.monotonic() - t0 < startup_timeout_sec:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if proc.poll() is not None:
            raise RuntimeError("Generated launch exited early. Aborting dynamic extraction.")
        log_step("launch:ready")

        log_step("dynamic_trace:start")
        run(
            f"export ROS_DOMAIN_ID={int(ros_domain_id)}; python3 tools/ros2_graph_dynamic_trace.py --duration {int(duration)} --sample-interval 0.2 --topic-prefix / > graph/graph_dynamic_trace.json",
            cwd=ws,
            use_ros_env=True,
            timeout_sec=dynamic_timeout_sec,
        )
        log_step("dynamic_trace:done")

        # Keep dynamic graph independent: only deduplicate/clean by dynamic self-consistency.
        # Static graph is used only for alignment metrics, never for mutating dynamic edges.
        log_step("dynamic_postprocess:start")
        run(
            "python3 - <<'PY'\n"
            "import json\n"
            "from pathlib import Path\n"
            f"ws=Path('{str(ws)}')\n"
            "sta=json.loads((ws/'graph/graph_static.json').read_text())\n"
            "trace_path=ws/'graph/graph_dynamic_trace.json'\n"
            "dyn=json.loads(trace_path.read_text())\n"
            "static_edges={(e.get('from'),e.get('to'),e.get('kind')) for e in sta.get('edges',[])}\n"
            f"ns='{GENERATED_NAMESPACE}'\n"
            "def denorm_node(n):\n"
            "  if not isinstance(n,str):\n"
            "    return None\n"
            "  p=ns.rstrip('/') + '/'\n"
            "  if n == ns:\n"
            "    return '/'\n"
            "  if n.startswith(p):\n"
            "    return '/' + n[len(p):].lstrip('/')\n"
            "  return None\n"
            "nodes=sorted({denorm_node(n) for n in dyn.get('nodes',[]) if denorm_node(n)})\n"
            "topic_names=[]\n"
            "seen_topics=set()\n"
            "for t in dyn.get('topics',[]):\n"
            "  name=t.get('name') if isinstance(t,dict) else None\n"
            "  if isinstance(name,str) and name.startswith('/') and name not in seen_topics and name not in {'/rosout','/parameter_events'}:\n"
            "    seen_topics.add(name)\n"
            "    topic_names.append(name)\n"
            "node_set=set(nodes)\n"
            "topic_set=set(topic_names)\n"
            "raw_edges=dyn.get('edges',[])\n"
            "edges_set=set()\n"
            "for e in raw_edges:\n"
            "  if not isinstance(e,dict):\n"
            "    continue\n"
            "  k=e.get('kind'); s=e.get('from'); t=e.get('to')\n"
            "  if k=='pub':\n"
            "    sn=denorm_node(s)\n"
            "    if sn in node_set and t in topic_set:\n"
            "      edges_set.add((sn,t,k))\n"
            "  elif k=='sub':\n"
            "    tn=denorm_node(t)\n"
            "    if s in topic_set and tn in node_set:\n"
            "      edges_set.add((s,tn,k))\n"
            "edges=[{'from':s,'to':t,'kind':k} for (s,t,k) in sorted(edges_set)]\n"
            "extra=[e for e in edges if (e['from'],e['to'],e['kind']) not in static_edges]\n"
            "dyn['nodes']=nodes\n"
            "dyn['topics']=[{'name':n} for n in topic_names]\n"
            "dyn['edges']=edges\n"
            "dyn['static_alignment']={\n"
            "  'static_edge_count': len(static_edges),\n"
            "  'dynamic_raw_edge_count': len(raw_edges),\n"
            "  'dynamic_dedup_edge_count': len(edges),\n"
            "  'dynamic_subset_of_static': len(extra) == 0,\n"
            "  'dynamic_extra_edge_count': len(extra),\n"
            "  'dynamic_extra_edges_sample': extra[:20],\n"
            "  'expectation': 'dynamic extraction is independent; subset relation is a validation target, not a filter rule'\n"
            "}\n"
            "trace_path.write_text(json.dumps(dyn,ensure_ascii=False,indent=2)+'\\n')\n"
            "PY",
            cwd=ws,
            use_ros_env=True,
            timeout_sec=120,
        )
        log_step("dynamic_postprocess:done")

        # Remove legacy artifacts to keep a single dynamic source of truth.
        run(
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            f"ws=Path('{str(ws / 'graph')}')\n"
            "for f in ['graph_dynamic.json','graph_dynamic_raw.json']:\n"
            "  p=ws/f\n"
            "  if p.exists():\n"
            "    p.unlink()\n"
            "PY",
            cwd=ws,
            use_ros_env=False,
            timeout_sec=30,
        )
    finally:
        log_step("launch:stop")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()



def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM GitHub->JSON->C++ package->static/dynamic->wait-for deadlock pipeline")
    p.add_argument("--repo-url", required=True)
    p.add_argument("--ref", default="main")
    p.add_argument("--api-base-url", required=True)
    p.add_argument("--api-key", default=None)
    p.add_argument("--model", default="deepseek-ai/DeepSeek-V3.2")
    p.add_argument(
        "--fallback-model",
        action="append",
        default=[
            "Qwen/Qwen3-235B-A22B",
            "Qwen/Qwen3-235B-A22B-Thinking-2507",
            "mistralai/Mistral-Large-Instruct-2407",
        ],
    )
    p.add_argument("--workspace", default="/home/yinyihao/ros2")
    p.add_argument("--package", default="generated_ros2_pkg")
    p.add_argument("--force", action="store_true")
    p.add_argument("--duration", type=int, default=6)
    p.add_argument("--build-workers", type=int, default=1, help="colcon parallel workers (set low to avoid OOM)")
    p.add_argument("--build-jobs", type=int, default=1, help="CMake/Make parallel jobs (set low to avoid OOM)")
    p.add_argument("--build-timeout", type=int, default=900, help="Timeout seconds for colcon build")
    p.add_argument("--startup-timeout", type=int, default=20, help="Timeout seconds waiting for launch startup")
    p.add_argument("--dynamic-timeout", type=int, default=120, help="Timeout seconds for dynamic trace step")
    p.add_argument("--ros-domain-id", type=int, default=None, help="Optional ROS_DOMAIN_ID isolation for launch+dynamic trace")
    p.add_argument("--out-spec", default="/home/yinyihao/ros2/graph/llm_project_spec.json")
    p.add_argument("--repo-cache-dir", default="/tmp/ros2_repo_cache")
    p.add_argument("--repo-local-path", default=None, help="Use an existing local repository path instead of cloning/fetching")
    p.add_argument("--evidence-max-files", type=int, default=5000)
    p.add_argument("--evidence-max-snippets", type=int, default=1200)
    p.add_argument("--print-prompt", action="store_true")
    return p.parse_args()


def main() -> None:
    try:
        args = parse_args()
        ws = Path(args.workspace).resolve()

        if args.print_prompt:
            print(PROMPT_TEMPLATE.format(repo_url=args.repo_url, ref=args.ref, evidence_json='{"demo":"use runtime extraction"}'))
            return

        api_key = args.api_key or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise SystemExit("Missing API key. Use --api-key or LLM_API_KEY env.")

        if args.repo_local_path:
            repo_dir = Path(args.repo_local_path).resolve()
            if not repo_dir.exists():
                raise SystemExit(f"repo-local-path not found: {repo_dir}")
        else:
            repo_dir = ensure_repo_checkout(args.repo_url, args.ref, Path(args.repo_cache_dir))
        evidence = extract_ros_evidence(
            repo_dir,
            max_files=max(1, int(args.evidence_max_files)),
            max_snippets=max(1, int(args.evidence_max_snippets)),
        )
        compact_evidence = compact_evidence_for_prompt(evidence, max_items=300, max_snippets=300)
        evidence_json = json.dumps(compact_evidence, ensure_ascii=False)
        prompt = PROMPT_TEMPLATE.format(repo_url=args.repo_url, ref=args.ref, evidence_json=evidence_json)
        spec, used_model = call_model_with_fallback(args.api_base_url, api_key, args.model, args.fallback_model, prompt)
        spec = repair_spec_shape(spec, args.repo_url, args.ref)
        print(f"LLM model used: {used_model}")
        print(
            "EVIDENCE COUNTS:",
            json.dumps(evidence.get("counts", {}), ensure_ascii=False),
        )

        errs = validate_spec(spec, evidence)
        if errs and errs == ["graph_seed.edges empty"]:
            retry_prompt = (
                prompt
                + "\n\nHard requirement for this retry:\n"
                + "- graph_seed.edges MUST be non-empty.\n"
                + "- Include at least one valid pub edge and one valid sub edge.\n"
                + "- Do not return empty graph_seed.\n"
            )
            spec, used_model = call_model_with_fallback(
                args.api_base_url,
                api_key,
                args.model,
                args.fallback_model,
                retry_prompt,
            )
            spec = repair_spec_shape(spec, args.repo_url, args.ref)
            print(f"LLM retry model used: {used_model}")
            errs = validate_spec(spec, evidence)

        if errs:
            print("SPEC INVALID")
            for e in errs:
                print("-", e)
            raise SystemExit(2)

        out_spec = Path(args.out_spec)
        experiment_dir = out_spec.resolve().parent
        experiment_dir.mkdir(parents=True, exist_ok=True)
        llm_spec_out = {
            "schema_version": spec.get("schema_version"),
            "meta": spec.get("meta", {}),
            "graph_seed": spec.get("graph_seed", {}),
        }
        save_json(out_spec, llm_spec_out)
        # Keep pipeline internal spec path in sync: build_and_extract reads graph/llm_project_spec.json.
        save_json(ws / "graph" / "llm_project_spec.json", llm_spec_out)

        pkg_dir = generate_cpp_pkg(spec, ws, args.package, args.force)
        snapshot_root = experiment_dir / "generated_src"
        if snapshot_root.exists():
            shutil.rmtree(snapshot_root)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        pkg_snapshot = snapshot_root / args.package
        pkg_snapshot.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pkg_dir / "src", pkg_snapshot / "src")
        if (pkg_dir / "launch").exists():
            shutil.copytree(pkg_dir / "launch", pkg_snapshot / "launch")
        build_and_extract(
            ws,
            args.package,
            args.duration,
            args.ros_domain_id,
            args.build_workers,
            args.build_jobs,
            args.build_timeout,
            args.startup_timeout,
            args.dynamic_timeout,
            spec_path=out_spec,
            source_root=snapshot_root,
        )
        for name in ("graph_static.json", "graph_dynamic_trace.json", "wait_for_graph_static.json", "pipeline_steps.log"):
            src = ws / "graph" / name
            if src.exists():
                shutil.copy2(src, experiment_dir / name)

        print("DONE")
        print(f"spec: {out_spec}")
        print(f"generated cpp package: {pkg_dir}")
        print("artifacts: graph/graph_static.json, graph/graph_dynamic_trace.json, graph/wait_for_graph_static.json")
        print("note: dynamic truth file is graph/graph_dynamic_trace.json (includes static_alignment check)")
    except subprocess.CalledProcessError as e:
        print("PIPELINE FAILED: subprocess command failed")
        print(f"returncode: {e.returncode}")
        if isinstance(e.cmd, list):
            print("cmd:", " ".join(str(x) for x in e.cmd))
        else:
            print("cmd:", str(e.cmd))
        raise SystemExit(3)
    except Exception as e:
        print(f"PIPELINE FAILED: {type(e).__name__}: {e}")
        raise SystemExit(4)


if __name__ == "__main__":
    main()
