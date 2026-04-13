#include <chrono>
#include <memory>

#include "nav_msgs/msg/occupancy_grid.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"

using namespace std::chrono_literals;

class GlobalCostmapMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  GlobalCostmapMini()
  : rclcpp_lifecycle::LifecycleNode("global_costmap"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    map_sub_ = this->create_subscription<nav_msgs::msg::OccupancyGrid>(
      "map", 10,
      [this](const nav_msgs::msg::OccupancyGrid::SharedPtr msg) {
        latest_map_ = *msg;
      });
    costmap_pub_ = this->create_publisher<nav_msgs::msg::OccupancyGrid>("global_costmap/costmap", 10);
    timer_ = this->create_wall_timer(300ms, std::bind(&GlobalCostmapMini::tick, this));
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
    nav_msgs::msg::OccupancyGrid out = latest_map_;
    out.header.stamp = this->now();
    if (out.header.frame_id.empty()) {
      out.header.frame_id = "map";
    }
    if (out.data.empty()) {
      out.info.resolution = 0.1f;
      out.info.width = 10;
      out.info.height = 10;
      out.data.assign(100, 0);
    }
    costmap_pub_->publish(out);
  }

  bool active_;
  nav_msgs::msg::OccupancyGrid latest_map_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr map_sub_;
  rclcpp::Publisher<nav_msgs::msg::OccupancyGrid>::SharedPtr costmap_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<GlobalCostmapMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
