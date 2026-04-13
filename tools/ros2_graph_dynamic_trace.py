#!/usr/bin/env python3

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rosidl_runtime_py.utilities import get_message


def _join_namespace_name(namespace: str, name: str) -> str:
    namespace = (namespace or "/").strip()
    name = (name or "").strip()

    if not namespace.startswith("/"):
        namespace = "/" + namespace

    if namespace == "/":
        return "/" + name.lstrip("/")

    return (namespace.rstrip("/") + "/" + name.lstrip("/")).replace("//", "/")


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values_sorted = sorted(values)
    k = (len(values_sorted) - 1) * p
    f = int(k)
    c = min(f + 1, len(values_sorted) - 1)
    if f == c:
        return values_sorted[f]
    return values_sorted[f] + (values_sorted[c] - values_sorted[f]) * (k - f)


def _stats_ms(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    return {
        "mean": statistics.fmean(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


@dataclass
class TopicMetrics:
    topic: str
    type_name: str
    recv_count: int = 0
    proc_count: int = 0
    recv_timestamps_ns: List[int] = field(default_factory=list)
    inter_arrival_ms: List[float] = field(default_factory=list)
    callback_ms: List[float] = field(default_factory=list)
    latency_ms: List[float] = field(default_factory=list)
    backlog_max: int = 0
    timeline: List[Dict[str, Any]] = field(default_factory=list)

    def on_recv(self, t_ns: int) -> None:
        self.recv_count += 1
        if self.recv_timestamps_ns:
            dt = (t_ns - self.recv_timestamps_ns[-1]) / 1e6
            self.inter_arrival_ms.append(dt)
        self.recv_timestamps_ns.append(t_ns)

    def on_proc_done(self) -> None:
        self.proc_count += 1
        backlog = self.recv_count - self.proc_count
        if backlog > self.backlog_max:
            self.backlog_max = backlog


class DynamicTraceCollector(Node):
    def __init__(
        self,
        duration_sec: float,
        sample_interval_sec: float,
        monitor_queue_depth: int,
        processing_delay_ms: float,
        topic_prefix: Optional[str],
    ) -> None:
        super().__init__("dynamic_trace_collector")
        self.duration_sec = duration_sec
        self.sample_interval_sec = sample_interval_sec
        self.monitor_queue_depth = monitor_queue_depth
        self.processing_delay_ms = processing_delay_ms
        self.topic_prefix = topic_prefix

        self.start_wall_ns = time.time_ns()
        self.timer_trigger_ns: List[int] = []

        self.metrics: Dict[str, TopicMetrics] = {}
        self._subs = []
        self._subscribed_topics: Set[str] = set()

        self.sample_timer = self.create_timer(sample_interval_sec, self._on_sample_timer)

    def _allow_topic(self, topic_name: str) -> bool:
        if topic_name.startswith("/_"):
            return False
        if self.topic_prefix and not topic_name.startswith(self.topic_prefix):
            return False
        return True

    def setup_subscriptions(self) -> None:
        topic_pairs = self.get_topic_names_and_types()
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=self.monitor_queue_depth,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )

        for topic_name, types in topic_pairs:
            if not self._allow_topic(topic_name):
                continue
            if not types:
                continue
            if topic_name in self._subscribed_topics:
                continue
            # Use the first advertised type to mirror ros2 graph dump behavior.
            type_name = types[0]
            self.metrics[topic_name] = TopicMetrics(topic=topic_name, type_name=type_name)

            try:
                msg_cls = get_message(type_name)
            except Exception:
                # Keep the topic in graph scope, but skip runtime callback metrics
                # when Python message type support is unavailable.
                continue

            sub = self.create_subscription(
                msg_cls,
                topic_name,
                self._make_callback(topic_name),
                qos,
            )
            self._subs.append(sub)
            self._subscribed_topics.add(topic_name)

    def _extract_latency_ms(self, msg: Any, now_ns: int) -> Optional[float]:
        # Best effort: only works for message types carrying header.stamp.
        header = getattr(msg, "header", None)
        if header is None:
            return None
        stamp = getattr(header, "stamp", None)
        if stamp is None:
            return None
        sec = getattr(stamp, "sec", None)
        nanosec = getattr(stamp, "nanosec", None)
        if sec is None or nanosec is None:
            return None
        if sec == 0 and nanosec == 0:
            return None
        sent_ns = int(sec) * 1_000_000_000 + int(nanosec)
        return (now_ns - sent_ns) / 1e6

    def _make_callback(self, topic_name: str):
        def _cb(msg: Any) -> None:
            t_recv_ns = time.time_ns()
            m = self.metrics[topic_name]
            m.on_recv(t_recv_ns)

            latency = self._extract_latency_ms(msg, t_recv_ns)
            if latency is not None:
                m.latency_ms.append(latency)

            t0 = time.perf_counter_ns()
            if self.processing_delay_ms > 0.0:
                time.sleep(self.processing_delay_ms / 1000.0)
            cb_ms = (time.perf_counter_ns() - t0) / 1e6
            m.callback_ms.append(cb_ms)
            m.on_proc_done()

        return _cb

    def _on_sample_timer(self) -> None:
        # Continuously discover new topics during collection.
        self.setup_subscriptions()

        t_ns = time.time_ns()
        self.timer_trigger_ns.append(t_ns)

        for m in self.metrics.values():
            m.timeline.append(
                {
                    "t_ns": t_ns,
                    "recv_count": m.recv_count,
                    "proc_count": m.proc_count,
                    "backlog": m.recv_count - m.proc_count,
                }
            )

    def graph_snapshot(self) -> Dict[str, Any]:
        nodes: Set[str] = set()
        topics_map: Dict[str, List[str]] = {}
        edges: List[Dict[str, str]] = []

        for name, namespace in self.get_node_names_and_namespaces():
            full = _join_namespace_name(namespace, name)
            if full != _join_namespace_name(self.get_namespace(), self.get_name()):
                nodes.add(full)

        for topic_name, types in self.get_topic_names_and_types():
            if not self._allow_topic(topic_name):
                continue
            topics_map[topic_name] = list(types)

            for info in self.get_publishers_info_by_topic(topic_name):
                src = _join_namespace_name(info.node_namespace, info.node_name)
                if src != _join_namespace_name(self.get_namespace(), self.get_name()):
                    nodes.add(src)
                edges.append({"from": src, "to": topic_name, "kind": "pub"})

            for info in self.get_subscriptions_info_by_topic(topic_name):
                dst = _join_namespace_name(info.node_namespace, info.node_name)
                if dst != _join_namespace_name(self.get_namespace(), self.get_name()):
                    nodes.add(dst)
                edges.append({"from": topic_name, "to": dst, "kind": "sub"})

        topics = [
            {"name": name, "types": sorted(types)} for name, types in sorted(topics_map.items())
        ]
        edges_sorted = sorted(edges, key=lambda e: (e["from"], e["to"], e["kind"]))

        return {
            "nodes": sorted(nodes),
            "topics": topics,
            "edges": edges_sorted,
        }

    def summarize(self) -> Dict[str, Any]:
        end_wall_ns = time.time_ns()

        timer_intervals_ms: List[float] = []
        for i in range(1, len(self.timer_trigger_ns)):
            timer_intervals_ms.append((self.timer_trigger_ns[i] - self.timer_trigger_ns[i - 1]) / 1e6)

        topic_summaries = []
        for tname in sorted(self.metrics.keys()):
            m = self.metrics[tname]
            elapsed_sec = max((end_wall_ns - self.start_wall_ns) / 1e9, 1e-9)
            recv_rate_hz = m.recv_count / elapsed_sec
            proc_rate_hz = m.proc_count / elapsed_sec

            # Rough overflow-risk estimate for monitored queue.
            # If processing capacity < arrival rate, backlog can grow and exceed depth.
            overflow_risk = False
            if recv_rate_hz > 0 and proc_rate_hz > 0 and recv_rate_hz > proc_rate_hz:
                overflow_risk = True

            topic_summaries.append(
                {
                    "topic": tname,
                    "type": m.type_name,
                    "recv_count": m.recv_count,
                    "proc_count": m.proc_count,
                    "recv_rate_hz": recv_rate_hz,
                    "proc_rate_hz": proc_rate_hz,
                    "inter_arrival_ms": _stats_ms(m.inter_arrival_ms),
                    "callback_processing_ms": _stats_ms(m.callback_ms),
                    "latency_ms": _stats_ms(m.latency_ms),
                    "monitor_queue_depth": self.monitor_queue_depth,
                    "backlog_max": m.backlog_max,
                    "overflow_risk": overflow_risk,
                    "timeline": m.timeline,
                    "publisher_endpoints": [
                        {
                            "node": _join_namespace_name(info.node_namespace, info.node_name),
                            "qos_depth": info.qos_profile.depth,
                            "reliability": str(info.qos_profile.reliability),
                        }
                        for info in self.get_publishers_info_by_topic(tname)
                    ],
                    "subscription_endpoints": [
                        {
                            "node": _join_namespace_name(info.node_namespace, info.node_name),
                            "qos_depth": info.qos_profile.depth,
                            "reliability": str(info.qos_profile.reliability),
                        }
                        for info in self.get_subscriptions_info_by_topic(tname)
                    ],
                }
            )

        return {
            "runtime": {
                "start_time_ns": self.start_wall_ns,
                "end_time_ns": end_wall_ns,
                "duration_sec": (end_wall_ns - self.start_wall_ns) / 1e9,
            },
            "timer": {
                "sample_interval_sec": self.sample_interval_sec,
                "trigger_count": len(self.timer_trigger_ns),
                "trigger_interval_ms": _stats_ms(timer_intervals_ms),
                "trigger_timestamps_ns": self.timer_trigger_ns,
            },
            "topics_runtime": topic_summaries,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dynamic ROS2 graph + runtime timeline extractor. "
            "Outputs static-like graph fields (nodes/topics/edges) plus timing and queue-risk metrics."
        )
    )
    parser.add_argument("--duration", type=float, default=8.0, help="Collection duration in seconds")
    parser.add_argument("--sample-interval", type=float, default=0.2, help="Timeline sampling interval in seconds")
    parser.add_argument("--monitor-queue-depth", type=int, default=50, help="Queue depth used by monitor subscriptions")
    parser.add_argument("--processing-delay-ms", type=float, default=0.0, help="Artificial monitor callback processing delay")
    parser.add_argument("--topic-prefix", type=str, default="/", help="Only monitor topics with this prefix")
    parser.add_argument("--compact", action="store_true", help="Output compact JSON")
    args = parser.parse_args()

    rclpy.init()
    node = DynamicTraceCollector(
        duration_sec=args.duration,
        sample_interval_sec=args.sample_interval,
        monitor_queue_depth=args.monitor_queue_depth,
        processing_delay_ms=args.processing_delay_ms,
        topic_prefix=args.topic_prefix,
    )

    # Discovery warmup
    for _ in range(5):
        rclpy.spin_once(node, timeout_sec=0.1)
        time.sleep(0.05)

    node.setup_subscriptions()

    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    t_end = time.monotonic() + args.duration
    try:
        while time.monotonic() < t_end:
            executor.spin_once(timeout_sec=0.1)
    finally:
        graph = node.graph_snapshot()
        runtime = node.summarize()
        out = {
            **graph,
            "dynamic_runtime": runtime,
        }

        if args.compact:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))

        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
