# nav2_race_cpp

A reduced but graph-rich Nav2-like stack in C++ for race research with ROS graph extraction and static source localization.

## Why this package

- Keeps key Nav2 interaction shape: `lifecycle_manager_navigation` + `bt_navigator` + `planner_server` + `controller_server`.
- Keeps key interfaces and names used by your tooling:
  - Service: `/lifecycle_manager_navigation/manage_nodes` (`nav2_msgs/srv/ManageLifecycleNodes`)
  - Action: `/navigate_to_pose` (`nav2_msgs/action/NavigateToPose`)
  - Lifecycle state service: `/bt_navigator/get_state`
- Provides richer graph than the tiny Python-only demo, and source is C++ so `ros2_graph_locate_nodes.py` can resolve definitions.

## Build

```bash
cd /home/yinyihao/ros2
source /opt/ros/humble/setup.bash
colcon build --packages-select nav2_race_cpp
source install/setup.bash
```

## Run stack

```bash
ros2 launch nav2_race_cpp nav2_race_cpp.launch.py execute_seconds:=5.0 deactivate_wait_seconds:=0.05 race_bug_mode:=true cancel_leak_probability:=0.03
```

## Run stress

Use your existing stress tool:

```bash
python3 /home/yinyihao/ros2/tools/nav2_race_repro.py --loops 200 --cancel-delay 0.01 --jitter 0.01
```

## Graph + locate

```bash
python3 /home/yinyihao/ros2/tools/ros2_graph_dump.py > /tmp/graph.json
python3 /home/yinyihao/ros2/tools/ros2_graph_locate_nodes.py --workspace /home/yinyihao/ros2 --pretty > /tmp/locate.json
```
