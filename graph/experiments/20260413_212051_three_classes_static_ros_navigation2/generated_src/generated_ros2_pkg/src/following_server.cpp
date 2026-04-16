#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class FollowingServerNode : public rclcpp::Node
{
public:
  FollowingServerNode()
  : rclcpp::Node("following_server")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/filtered_dynamic_pose", 10);

    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/follow_object", std::bind(&FollowingServerNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));

    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&FollowingServerNode::on_pub_timer_0, this));
  }

private:
  void on_pub_timer_0()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_0_->publish(msg);
  }

  void on_srv_server_0(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)
  {
    (void)req;
    resp->success = true;
    resp->message = "ok";
  }

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<FollowingServerNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
