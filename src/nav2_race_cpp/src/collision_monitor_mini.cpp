#include <memory>

#include "geometry_msgs/msg/twist.hpp"
#include "rclcpp/rclcpp.hpp"

class CollisionMonitorMini : public rclcpp::Node
{
public:
  CollisionMonitorMini()
  : rclcpp::Node("collision_monitor")
  {
    cmd_sub_ = this->create_subscription<geometry_msgs::msg::Twist>(
      "cmd_vel_smoothed", 10,
      [this](const geometry_msgs::msg::Twist::SharedPtr msg) {
        auto out = *msg;
        if (out.linear.x > 0.2) {
          out.linear.x = 0.2;
        }
        cmd_pub_->publish(out);
      });
    cmd_pub_ = this->create_publisher<geometry_msgs::msg::Twist>("cmd_vel", 10);
  }

private:
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr cmd_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_pub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<CollisionMonitorMini>());
  rclcpp::shutdown();
  return 0;
}
