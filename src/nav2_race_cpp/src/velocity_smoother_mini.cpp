#include <memory>

#include "geometry_msgs/msg/twist.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"

class VelocitySmootherMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  VelocitySmootherMini()
  : rclcpp_lifecycle::LifecycleNode("velocity_smoother"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    cmd_sub_ = this->create_subscription<geometry_msgs::msg::Twist>(
      "cmd_vel_raw", 10,
      [this](const geometry_msgs::msg::Twist::SharedPtr msg) {
        if (!active_) {
          return;
        }
        auto out = *msg;
        out.linear.x *= 0.9;
        out.angular.z *= 0.9;
        cmd_pub_->publish(out);
      });
    cmd_pub_ = this->create_publisher<geometry_msgs::msg::Twist>("cmd_vel_smoothed", 10);
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
  bool active_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr cmd_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_pub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<VelocitySmootherMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
