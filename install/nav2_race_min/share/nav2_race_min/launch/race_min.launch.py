from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode


def generate_launch_description():
    execute_seconds = LaunchConfiguration("execute_seconds")
    deactivate_wait_seconds = LaunchConfiguration("deactivate_wait_seconds")
    race_bug_mode = LaunchConfiguration("race_bug_mode")
    cancel_leak_probability = LaunchConfiguration("cancel_leak_probability")

    return LaunchDescription(
        [
            DeclareLaunchArgument("execute_seconds", default_value="3.0"),
            DeclareLaunchArgument("deactivate_wait_seconds", default_value="1.0"),
            DeclareLaunchArgument("race_bug_mode", default_value="true"),
            DeclareLaunchArgument("cancel_leak_probability", default_value="0.03"),
            LifecycleNode(
                package="nav2_race_min",
                executable="mini_navigator_server",
                name="mini_navigator",
                namespace="",
                output="screen",
                parameters=[
                    {
                        "execute_seconds": execute_seconds,
                        "deactivate_wait_seconds": deactivate_wait_seconds,
                        "race_bug_mode": race_bug_mode,
                        "cancel_leak_probability": cancel_leak_probability,
                    }
                ],
            )
        ]
    )
