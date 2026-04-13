from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="generated_ros2_pkg", executable="minimal_publisher", namespace="/generated", name="minimal_publisher"),
        Node(package="generated_ros2_pkg", executable="minimal_subscriber", namespace="/generated", name="minimal_subscriber"),
        Node(package="generated_ros2_pkg", executable="service_client", namespace="/generated", name="service_client"),
        Node(package="generated_ros2_pkg", executable="service_server", namespace="/generated", name="service_server"),
        Node(package="generated_ros2_pkg", executable="timer_node", namespace="/generated", name="timer_node"),
    ])
