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
from typing import Dict, List, Optional

PROMPT_TEMPLATE = textwrap.dedent(
    """
    You are an extraction engine. Read a ROS2 GitHub repository and output ONLY JSON.

    Input:
    - repo_url: {repo_url}
    - ref: {ref}

    Goal:
    Extract a MAXIMAL VERIFIABLE SUBGRAPH for deadlock verification.
    Focus on wait-for / concurrency semantics, not only data-flow.

        Output JSON schema (strict):
    {{
      "schema_version": "1.0",
      "meta": {{"repo_url":"...","ref":"...","project_name":"...","language":["cpp","python"]}},
      "graph_seed": {{
        "nodes": ["/node"],
        "topics": ["/topic"],
                "edges": [{{"from":"/node","to":"/topic","kind":"pub"}},{{"from":"/topic","to":"/node","kind":"sub"}}],
                "node_semantics": [
                    {{
                        "name": "/node",
                        "executor": "single_threaded|multi_threaded|unknown",
                        "callback_sources": ["subscription","service","timer","action"],
                        "service_clients": ["/service"],
                        "service_servers": ["/service"],
                        "blocking_calls": ["wait_for_service","sleep","future_get","spin_until_future_complete"],
                        "callbacks": [
                            {{
                                "id": "cb_nav_tick",
                                "source": "timer|subscription|service|action",
                                "waits_for_callbacks": ["/other_node:cb_x"],
                                "waits_for_services": ["/service"],
                                "blocking_calls": ["wait_for_service","future_get"]
                            }}
                        ],
                        "callback_wait_edges": [{{"from":"/node:cb_a","to":"/node_or_service_owner:cb_b","reason":"blocking_in_callback"}}]
                    }}
                ]
      }}
    }}

    Constraints:
    1) Use absolute ROS names starting with '/'.
    2) Return JSON only. No markdown.
    3) Keep only verification-relevant nodes. Drop sensor-heavy and visualization-only nodes unless required by dependency.
    4) Prefer control-plane nodes (action/service/lifecycle/controller/planner/navigator).
    5) graph_seed must be self-consistent and complete enough for code generation.
    6) Prioritize callback/executor/blocking semantics that can form wait-for dependencies.
    7) Prefer callback-level dependencies over node-level summaries. Include explicit callback IDs and waits_for_callbacks whenever possible.
    8) If a service callback itself can block or wait, include reverse callback dependency hints to reveal possible A->B->A cycles.
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
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {detail}")

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
        except Exception:
            tried.append(m)
    raise RuntimeError(f"All models failed: {tried}")


def validate_spec(spec: Dict) -> List[str]:
    errs: List[str] = []
    for k in ["schema_version", "meta", "graph_seed"]:
        if k not in spec:
            errs.append(f"missing {k}")

    gs = spec.get("graph_seed", {})
    gs.setdefault("nodes", [])
    gs.setdefault("topics", [])
    gs.setdefault("edges", [])
    gs.setdefault("node_semantics", [])
    gen = spec.get("generation", {})
    gen.setdefault("cpp_nodes", gen.get("python_nodes", []))

    # normalize
    gs["nodes"] = [canonical_node_id(x) for x in gs["nodes"] if isinstance(x, str)]
    gs["topics"] = [normalize_abs(x) for x in gs["topics"] if isinstance(x, str)]

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
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x))
            ]
            cb_blocking = [str(x) for x in cb.get("blocking_calls", [])]
            callbacks.append({
                "id": cb_id,
                "source": str(cb.get("source", "unknown")),
                "waits_for_callbacks": waits_for_callbacks,
                "waits_for_services": waits_for_services,
                "blocking_calls": cb_blocking,
            })

        semantics_by_name[nn] = {
            "service_servers": [
                normalize_abs(x)
                for x in s.get("service_servers", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x))
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
        }

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
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x))
            ],
            "service_clients": [
                normalize_abs(x)
                for x in n.get("service_clients", [])
                if isinstance(x, str) and not is_reserved_service_name(normalize_abs(x))
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
                if t in RESERVED_TOPICS:
                    continue
                topics.add(t)
                edges.append({"from": node_name, "to": t, "kind": "pub"})
            for t in n_subs:
                if t in RESERVED_TOPICS:
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
                if t in RESERVED_TOPICS:
                    continue
                topics.add(t)
                edges.append({"from": node_name, "to": t, "kind": "pub"})
            for t in n.get("subscribes", []):
                if t in RESERVED_TOPICS:
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
        }

    gen["cpp_nodes"] = [cpp_by_name[n] for n in sorted(cpp_by_name.keys())]

    if len(gs["nodes"]) == 0:
        errs.append("graph_seed.nodes empty")
    if len(gs["edges"]) == 0:
        errs.append("graph_seed.edges empty")

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

        for i, t in enumerate(node["publishes"]):
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

        for i, t in enumerate(node["subscribes"]):
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

        for i, s in enumerate(node["service_servers"]):
            srv_server_init.append(
                f'    srv_server_{i}_ = this->create_service<std_srvs::srv::Trigger>("{s}", std::bind(&{cls}::on_srv_server_{i}, this, std::placeholders::_1, std::placeholders::_2));'
            )
            callbacks.append(
                f'''  void on_srv_server_{i}(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)\n  {{\n    (void)req;\n    resp->success = true;\n    resp->message = "ok";\n  }}'''
            )
            members.append(f'  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_{i}_;')

        for i, s in enumerate(node["service_clients"]):
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
        f"python3 tools/ros2_wait_for_graph_static.py --graph graph/graph_static.json --workspace {shlex.quote(str(ws))} --output graph/wait_for_graph_static.json --pretty",
        cwd=ws,
        use_ros_env=True,
        timeout_sec=120,
    )
    # Merge and analyze callback-level semantics.
    run(
        f"python3 tools/ros2_wait_for_semantic_merge.py --workspace {shlex.quote(str(ws))} --spec graph/llm_project_spec.json --wait-for graph/wait_for_graph_static.json --pretty",
        cwd=ws,
        use_ros_env=False,
        timeout_sec=120,
    )
    log_step("wait_for:done")

    launch_cmd = f"ros2 launch {shlex.quote(package)} generated_system.launch.py"
    launch_env = os.environ.copy()
    launch_env["ROS_DOMAIN_ID"] = str(ros_domain_id)
    proc = subprocess.Popen(
        [
            "bash",
            "-lc",
            f"source /opt/ros/humble/setup.bash; source install/setup.bash; {launch_cmd}",
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
    p.add_argument("--fallback-model", action="append", default=["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B"])
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
    p.add_argument("--print-prompt", action="store_true")
    return p.parse_args()


def main() -> None:
    try:
        args = parse_args()
        ws = Path(args.workspace).resolve()

        if args.print_prompt:
            print(PROMPT_TEMPLATE.format(repo_url=args.repo_url, ref=args.ref))
            return

        api_key = args.api_key or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise SystemExit("Missing API key. Use --api-key or LLM_API_KEY env.")

        prompt = PROMPT_TEMPLATE.format(repo_url=args.repo_url, ref=args.ref)
        spec, used_model = call_model_with_fallback(args.api_base_url, api_key, args.model, args.fallback_model, prompt)
        print(f"LLM model used: {used_model}")

        errs = validate_spec(spec)
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
            print(f"LLM retry model used: {used_model}")
            errs = validate_spec(spec)

        if errs:
            print("SPEC INVALID")
            for e in errs:
                print("-", e)
            raise SystemExit(2)

        out_spec = Path(args.out_spec)
        llm_spec_out = {
            "schema_version": spec.get("schema_version"),
            "meta": spec.get("meta", {}),
            "graph_seed": spec.get("graph_seed", {}),
        }
        save_json(out_spec, llm_spec_out)

        pkg_dir = generate_cpp_pkg(spec, ws, args.package, args.force)
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
        )

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
