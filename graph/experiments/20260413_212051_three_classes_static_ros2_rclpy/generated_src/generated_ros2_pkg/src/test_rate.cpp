#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestRateNode : public rclcpp::Node
{
public:
  TestRateNode()
  : rclcpp::Node("test_rate")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/chatter", 10);



    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&TestRateNode::on_pub_timer_0, this));
  }

private:
  void on_pub_timer_0()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_0_->publish(msg);
  }

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestRateNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
