#include <chrono>
#include <memory>

#include "nav_msgs/msg/occupancy_grid.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"

using namespace std::chrono_literals;

class MapServerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  MapServerMini()
  : rclcpp_lifecycle::LifecycleNode("map_server"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    map_pub_ = this->create_publisher<nav_msgs::msg::OccupancyGrid>("map", 10);
    timer_ = this->create_wall_timer(500ms, std::bind(&MapServerMini::tick, this));
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &)
  {
    active_ = true;
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &)
  {
    active_ = false;
    return CallbackReturn::SUCCESS;
  }

private:
  void tick()
  {
    if (!active_) {
      return;
    }
    nav_msgs::msg::OccupancyGrid map;
    map.header.stamp = this->now();
    map.header.frame_id = "map";
    map.info.resolution = 0.1f;
    map.info.width = 10;
    map.info.height = 10;
    map.data.assign(100, 0);
    map_pub_->publish(map);
  }

  bool active_;
  rclcpp::Publisher<nav_msgs::msg::OccupancyGrid>::SharedPtr map_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<MapServerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
