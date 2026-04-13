from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode, Node


def generate_launch_description():
    race_bug_mode = LaunchConfiguration("race_bug_mode")
    cancel_leak_probability = LaunchConfiguration("cancel_leak_probability")
    deactivate_wait_seconds = LaunchConfiguration("deactivate_wait_seconds")
    execute_seconds = LaunchConfiguration("execute_seconds")

    return LaunchDescription(
        [
            DeclareLaunchArgument("race_bug_mode", default_value="true"),
            DeclareLaunchArgument("cancel_leak_probability", default_value="0.03"),
            DeclareLaunchArgument("deactivate_wait_seconds", default_value="0.05"),
            DeclareLaunchArgument("execute_seconds", default_value="5.0"),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="planner_server_mini",
                name="planner_server",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="controller_server_mini",
                name="controller_server",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="smoother_server_mini",
                name="smoother_server",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="behavior_server_mini",
                name="behavior_server",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="map_server_mini",
                name="map_server",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="global_costmap_mini",
                name="global_costmap",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="local_costmap_mini",
                name="local_costmap",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="waypoint_follower_mini",
                name="waypoint_follower",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="velocity_smoother_mini",
                name="velocity_smoother",
                namespace="",
                output="screen",
            ),
            LifecycleNode(
                package="nav2_race_cpp",
                executable="bt_navigator_mini",
                name="bt_navigator",
                namespace="",
                output="screen",
                parameters=[
                    {
                        "race_bug_mode": race_bug_mode,
                        "cancel_leak_probability": cancel_leak_probability,
                        "deactivate_wait_seconds": deactivate_wait_seconds,
                        "execute_seconds": execute_seconds,
                    }
                ],
            ),
            Node(
                package="nav2_race_cpp",
                executable="lifecycle_manager_navigation_mini",
                name="lifecycle_manager_navigation",
                output="screen",
                parameters=[
                    {
                        "managed_nodes": [
                            "planner_server",
                            "controller_server",
                            "smoother_server",
                            "behavior_server",
                            "map_server",
                            "global_costmap",
                            "local_costmap",
                            "waypoint_follower",
                            "velocity_smoother",
                            "bt_navigator",
                        ],
                        "autostart": True,
                    }
                ],
            ),
            Node(
                package="nav2_race_cpp",
                executable="collision_monitor_mini",
                name="collision_monitor",
                output="screen",
            ),
        ]
    )
