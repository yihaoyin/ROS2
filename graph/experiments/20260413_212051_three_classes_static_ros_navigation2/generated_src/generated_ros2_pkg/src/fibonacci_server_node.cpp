#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class FibonacciServerNodeNode : public rclcpp::Node
{
public:
  FibonacciServerNodeNode()
  : rclcpp::Node("fibonacci_server_node")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/activate_server", 10);
    pub_1_ = this->create_publisher<std_msgs::msg::String>("/deactivate_server", 10);
    pub_2_ = this->create_publisher<std_msgs::msg::String>("/omit_preemption", 10);


    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/fibonacci");
    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&FibonacciServerNodeNode::on_pub_timer_0, this));
    timer_pub_1_ = this->create_wall_timer(500ms, std::bind(&FibonacciServerNodeNode::on_pub_timer_1, this));
    timer_pub_2_ = this->create_wall_timer(500ms, std::bind(&FibonacciServerNodeNode::on_pub_timer_2, this));
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&FibonacciServerNodeNode::on_srv_client_timer_0, this));
  }

private:
  void on_pub_timer_0()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_0_->publish(msg);
  }

  void on_pub_timer_1()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_1_->publish(msg);
  }

  void on_pub_timer_2()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_2_->publish(msg);
  }

  void on_srv_client_timer_0()
  {
    if (!srv_client_0_->service_is_ready()) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_0_->async_send_request(req);
    (void)fut;
  }

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_1_;
  rclcpp::TimerBase::SharedPtr timer_pub_1_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_2_;
  rclcpp::TimerBase::SharedPtr timer_pub_2_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<FibonacciServerNodeNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
