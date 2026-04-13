cmake_minimum_required(VERSION 3.8)
project(generated_ros2_pkg)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(std_srvs REQUIRED)

add_executable(minimal_publisher src/minimal_publisher.cpp)
ament_target_dependencies(minimal_publisher rclcpp std_msgs std_srvs)

add_executable(minimal_subscriber src/minimal_subscriber.cpp)
ament_target_dependencies(minimal_subscriber rclcpp std_msgs std_srvs)

add_executable(service_client src/service_client.cpp)
ament_target_dependencies(service_client rclcpp std_msgs std_srvs)

add_executable(service_server src/service_server.cpp)
ament_target_dependencies(service_server rclcpp std_msgs std_srvs)

add_executable(timer_node src/timer_node.cpp)
ament_target_dependencies(timer_node rclcpp std_msgs std_srvs)

install(TARGETS
  minimal_publisher
  minimal_subscriber
  service_client
  service_server
  timer_node
  DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})
ament_package()
