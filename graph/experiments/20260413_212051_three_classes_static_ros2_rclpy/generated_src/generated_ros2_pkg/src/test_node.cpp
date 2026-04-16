#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestNodeNode : public rclcpp::Node
{
public:
  TestNodeNode()
  : rclcpp::Node("test_node")
  {


    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/foo/{bad_sub}", std::bind(&TestNodeNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_1_ = this->create_service<std_srvs::srv::Trigger>("/get/parameters", std::bind(&TestNodeNode::on_srv_server_1, this, std::placeholders::_1, std::placeholders::_2));
    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/foo/{bad_sub}");
    srv_client_1_ = this->create_client<std_srvs::srv::Trigger>("/get/parameters");
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&TestNodeNode::on_srv_client_timer_0, this));
    timer_srv_client_1_ = this->create_wall_timer(1000ms, std::bind(&TestNodeNode::on_srv_client_timer_1, this));
  }

private:
  void on_srv_server_0(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)
  {
    (void)req;
    resp->success = true;
    resp->message = "ok";
  }

  void on_srv_server_1(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)
  {
    (void)req;
    resp->success = true;
    resp->message = "ok";
  }

  void on_srv_client_timer_0()
  {
    if (!srv_client_0_->service_is_ready()) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_0_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_1()
  {
    if (!srv_client_1_->service_is_ready()) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_1_->async_send_request(req);
    (void)fut;
  }

  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_0_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_1_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_1_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_1_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestNodeNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
