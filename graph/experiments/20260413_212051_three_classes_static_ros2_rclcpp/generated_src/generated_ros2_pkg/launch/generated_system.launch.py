from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="generated_ros2_pkg", executable="clock_publisher", namespace="/generated", name="clock_publisher"),
        Node(package="generated_ros2_pkg", executable="clock_thread_testing_node", namespace="/generated", name="clock_thread_testing_node"),
        Node(package="generated_ros2_pkg", executable="my_node", namespace="/generated", name="my_node"),
        Node(package="generated_ros2_pkg", executable="sim_clock_publisher_node", namespace="/generated", name="sim_clock_publisher_node"),
        Node(package="generated_ros2_pkg", executable="subscription_class_node_inheritance", namespace="/generated", name="subscription_class_node_inheritance"),
        Node(package="generated_ros2_pkg", executable="test_component_bar", namespace="/generated", name="test_component_bar"),
        Node(package="generated_ros2_pkg", executable="test_component_foo", namespace="/generated", name="test_component_foo"),
        Node(package="generated_ros2_pkg", executable="timer_node", namespace="/generated", name="timer_node"),
    ])
