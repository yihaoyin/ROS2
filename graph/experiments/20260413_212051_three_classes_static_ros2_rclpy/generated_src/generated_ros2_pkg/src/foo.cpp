#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class FooNode : public rclcpp::Node
{
public:
  FooNode()
  : rclcpp::Node("foo")
  {

    sub_0_ = this->create_subscription<std_msgs::msg::String>("/test_node", 10, std::bind(&FooNode::on_sub_0, this, std::placeholders::_1));
    sub_1_ = this->create_subscription<std_msgs::msg::String>("/testcreatewhilespinning", 10, std::bind(&FooNode::on_sub_1, this, std::placeholders::_1));



  }

private:
  void on_sub_0(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_1(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_0_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_1_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<FooNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
