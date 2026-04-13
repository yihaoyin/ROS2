# nav2_race_min

A minimal, standalone race reproducer that keeps only the problematic interaction class:
Lifecycle transition (deactivate/activate) overlapping with Action goal cancel.

## Build

```bash
cd /home/yinyihao/ros2
source /opt/ros/humble/setup.bash
colcon build --packages-select nav2_race_min
source install/setup.bash
```

## Run

Terminal 1:

```bash
cd /home/yinyihao/ros2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch nav2_race_min race_min.launch.py
```

Aggressive (more likely to fail deactivation under overlap):

```bash
ros2 launch nav2_race_min race_min.launch.py execute_seconds:=5.0 deactivate_wait_seconds:=0.03 race_bug_mode:=true cancel_leak_probability:=0.05
```

Terminal 2:

```bash
cd /home/yinyihao/ros2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run nav2_race_min mini_race_stress --loops 300 --cancel-delay 0.03 --jitter 0.02
```

## Notes

- `race_bug_mode=true` intentionally widens race timing windows.
- `cancel_leak_probability` intentionally emulates occasional in-flight goal cleanup loss to stabilize reproduction.
- This package is designed for fast local iteration and instrumentation, not navigation behavior accuracy.
