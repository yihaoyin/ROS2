#!/usr/bin/env bash
set -euo pipefail

WS="/home/yinyihao/ros2"
OUT_DIR="$WS/graph"
DYN_OUT="$OUT_DIR/graph_dynamic.json"
DYN_RAW_OUT="$OUT_DIR/graph_dynamic_raw.json"
DYN_TRACE_OUT="$OUT_DIR/graph_dynamic_trace.json"
STA_OUT="$OUT_DIR/graph_static.json"

mkdir -p "$OUT_DIR"

cd "$WS"
set +u
source /opt/ros/humble/setup.bash
source install/setup.bash
set -u

# Runtime graph + timeline metrics
python3 tools/ros2_graph_dynamic_trace.py --duration 8 --sample-interval 0.2 --topic-prefix / > "$DYN_TRACE_OUT"

# Keep a raw dynamic graph copy (from dynamic trace output)
python3 - <<'PY'
import json
from pathlib import Path
ws = Path('/home/yinyihao/ros2')
trace = json.loads((ws / 'graph/graph_dynamic_trace.json').read_text())
raw = {
	'nodes': trace.get('nodes', []),
	'topics': trace.get('topics', []),
	'edges': trace.get('edges', []),
}
(ws / 'graph/graph_dynamic_raw.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2) + '\n')
PY

python3 tools/ros2_graph_static_dump.py --workspace "$WS" --package nav2_race_cpp > "$STA_OUT"

# Build a comparable dynamic graph in the same scope as static extraction
# (application-declared nodes/topics only).
python3 - <<'PY'
import json
from pathlib import Path

ws = Path('/home/yinyihao/ros2')
dyn_raw = json.loads((ws / 'graph/graph_dynamic_raw.json').read_text())
sta = json.loads((ws / 'graph/graph_static.json').read_text())

allowed_nodes = set(sta.get('nodes', []))
allowed_topics = {t['name'] for t in sta.get('topics', [])}

dyn_nodes = sorted([n for n in dyn_raw.get('nodes', []) if n in allowed_nodes])
dyn_topics = [t for t in dyn_raw.get('topics', []) if t.get('name') in allowed_topics]

dyn_edges = []
for e in dyn_raw.get('edges', []):
	src = e.get('from')
	dst = e.get('to')
	kind = e.get('kind')
	if kind == 'pub' and src in allowed_nodes and dst in allowed_topics:
		dyn_edges.append(e)
	elif kind == 'sub' and src in allowed_topics and dst in allowed_nodes:
		dyn_edges.append(e)

dyn_edges = sorted(dyn_edges, key=lambda x: (x['from'], x['to'], x['kind']))

out = {
	'nodes': dyn_nodes,
	'topics': dyn_topics,
	'edges': dyn_edges,
}
(ws / 'graph/graph_dynamic.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
PY

echo "dynamic graph: $DYN_OUT"
echo "dynamic raw  : $DYN_RAW_OUT"
echo "dynamic trace: $DYN_TRACE_OUT"
echo "static  graph: $STA_OUT"
