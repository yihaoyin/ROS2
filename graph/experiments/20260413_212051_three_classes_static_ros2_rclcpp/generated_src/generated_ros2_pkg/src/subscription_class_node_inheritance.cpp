#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class SubscriptionClassNodeInheritanceNode : public rclcpp::Node
{
public:
  SubscriptionClassNodeInheritanceNode()
  : rclcpp::Node("subscription_class_node_inheritance")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/test_topic", 10);
    sub_0_ = this->create_subscription<std_msgs::msg::String>("/topic", 10, std::bind(&SubscriptionClassNodeInheritanceNode::on_sub_0, this, std::placeholders::_1));


    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&SubscriptionClassNodeInheritanceNode::on_pub_timer_0, this));
  }

private:
  void on_pub_timer_0()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_0_->publish(msg);
  }

  void on_sub_0(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<SubscriptionClassNodeInheritanceNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
