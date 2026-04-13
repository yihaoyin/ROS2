cmake_minimum_required(VERSION 3.8)
project(nav2_race_cpp)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(nav2_msgs REQUIRED)
find_package(lifecycle_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(std_srvs REQUIRED)

add_executable(bt_navigator_mini src/bt_navigator_mini.cpp)
ament_target_dependencies(bt_navigator_mini
  rclcpp rclcpp_lifecycle rclcpp_action nav2_msgs lifecycle_msgs geometry_msgs nav_msgs std_msgs std_srvs)

add_executable(planner_server_mini src/planner_server_mini.cpp)
ament_target_dependencies(planner_server_mini
  rclcpp rclcpp_lifecycle rclcpp_action nav2_msgs lifecycle_msgs geometry_msgs nav_msgs std_msgs std_srvs)

add_executable(controller_server_mini src/controller_server_mini.cpp)
ament_target_dependencies(controller_server_mini
  rclcpp rclcpp_lifecycle rclcpp_action nav2_msgs lifecycle_msgs geometry_msgs nav_msgs std_msgs std_srvs)

add_executable(smoother_server_mini src/smoother_server_mini.cpp)
ament_target_dependencies(smoother_server_mini
  rclcpp rclcpp_lifecycle nav_msgs std_msgs std_srvs)

add_executable(behavior_server_mini src/behavior_server_mini.cpp)
ament_target_dependencies(behavior_server_mini
  rclcpp rclcpp_lifecycle std_msgs std_srvs)

add_executable(map_server_mini src/map_server_mini.cpp)
ament_target_dependencies(map_server_mini
  rclcpp rclcpp_lifecycle nav_msgs)

add_executable(global_costmap_mini src/global_costmap_mini.cpp)
ament_target_dependencies(global_costmap_mini
  rclcpp rclcpp_lifecycle nav_msgs std_msgs)

add_executable(local_costmap_mini src/local_costmap_mini.cpp)
ament_target_dependencies(local_costmap_mini
  rclcpp rclcpp_lifecycle nav_msgs std_msgs)

add_executable(velocity_smoother_mini src/velocity_smoother_mini.cpp)
ament_target_dependencies(velocity_smoother_mini
  rclcpp rclcpp_lifecycle geometry_msgs)

add_executable(waypoint_follower_mini src/waypoint_follower_mini.cpp)
ament_target_dependencies(waypoint_follower_mini
  rclcpp rclcpp_lifecycle rclcpp_action nav2_msgs geometry_msgs)

add_executable(collision_monitor_mini src/collision_monitor_mini.cpp)
ament_target_dependencies(collision_monitor_mini
  rclcpp geometry_msgs)

add_executable(lifecycle_manager_navigation_mini src/lifecycle_manager_navigation_mini.cpp)
ament_target_dependencies(lifecycle_manager_navigation_mini
  rclcpp nav2_msgs lifecycle_msgs std_msgs)

install(TARGETS
  bt_navigator_mini
  planner_server_mini
  controller_server_mini
  smoother_server_mini
  behavior_server_mini
  map_server_mini
  global_costmap_mini
  local_costmap_mini
  velocity_smoother_mini
  waypoint_follower_mini
  collision_monitor_mini
  lifecycle_manager_navigation_mini
  DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME})

ament_package()
