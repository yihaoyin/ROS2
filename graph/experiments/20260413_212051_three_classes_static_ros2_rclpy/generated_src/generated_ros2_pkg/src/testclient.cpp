#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestclientNode : public rclcpp::Node
{
public:
  TestclientNode()
  : rclcpp::Node("testclient")
  {


    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/get/parameters", std::bind(&TestclientNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_1_ = this->create_service<std_srvs::srv::Trigger>("/service", std::bind(&TestclientNode::on_srv_server_1, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_2_ = this->create_service<std_srvs::srv::Trigger>("/test_wfs_exists", std::bind(&TestclientNode::on_srv_server_2, this, std::placeholders::_1, std::placeholders::_2));
    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/get/parameters");
    srv_client_1_ = this->create_client<std_srvs::srv::Trigger>("/service");
    srv_client_2_ = this->create_client<std_srvs::srv::Trigger>("/test_direct_destroy");
    srv_client_3_ = this->create_client<std_srvs::srv::Trigger>("/test_server");
    srv_client_4_ = this->create_client<std_srvs::srv::Trigger>("/test_service");
    srv_client_5_ = this->create_client<std_srvs::srv::Trigger>("/test_wfs_exists");
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_0, this));
    timer_srv_client_1_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_1, this));
    timer_srv_client_2_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_2, this));
    timer_srv_client_3_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_3, this));
    timer_srv_client_4_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_4, this));
    timer_srv_client_5_ = this->create_wall_timer(1000ms, std::bind(&TestclientNode::on_srv_client_timer_5, this));
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

  void on_srv_server_2(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)
  {
    (void)req;
    resp->success = true;
    resp->message = "ok";
  }

  void on_srv_client_timer_0()
  {
    if (!srv_client_0_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_0_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_1()
  {
    if (!srv_client_1_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_1_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_2()
  {
    if (!srv_client_2_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_2_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_3()
  {
    if (!srv_client_3_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_3_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_4()
  {
    if (!srv_client_4_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_4_->async_send_request(req);
    (void)fut;
  }

  void on_srv_client_timer_5()
  {
    if (!srv_client_5_->wait_for_service(200ms)) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = srv_client_5_->async_send_request(req);
    (void)fut;
  }

  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_0_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_1_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_2_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_1_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_1_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_2_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_2_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_3_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_3_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_4_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_4_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_5_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_5_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestclientNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
