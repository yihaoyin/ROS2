#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class MinimalSubscriberNode : public rclcpp::Node
{
public:
  MinimalSubscriberNode()
  : rclcpp::Node("minimal_subscriber")
  {

    sub_0_ = this->create_subscription<std_msgs::msg::String>("/topic", 10, std::bind(&MinimalSubscriberNode::on_sub_0, this, std::placeholders::_1));

    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/compute_sum");
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&MinimalSubscriberNode::on_srv_client_timer_0, this));
  }

private:
  void on_sub_0(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_srv_client_timer_0()
  {
    if (!srv_client_0_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_0_->async_send_request(req);
    (void)fut;
  }

  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_0_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<MinimalSubscriberNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
