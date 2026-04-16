#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestNodeNoSimNode : public rclcpp::Node
{
public:
  TestNodeNoSimNode()
  : rclcpp::Node("test_node_no_sim")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/chatter", 10);
    pub_1_ = this->create_publisher<std_msgs::msg::String>("/chatter/{bad_sub}", 10);
    pub_2_ = this->create_publisher<std_msgs::msg::String>("/raw_subscription_test", 10);
    pub_3_ = this->create_publisher<std_msgs::msg::String>("/take_test", 10);
    sub_0_ = this->create_subscription<std_msgs::msg::String>("/chatter", 10, std::bind(&TestNodeNoSimNode::on_sub_0, this, std::placeholders::_1));
    sub_1_ = this->create_subscription<std_msgs::msg::String>("/foo/{bad_sub}", 10, std::bind(&TestNodeNoSimNode::on_sub_1, this, std::placeholders::_1));
    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/foo/{bad_sub}", std::bind(&TestNodeNoSimNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_1_ = this->create_service<std_srvs::srv::Trigger>("/get/parameters", std::bind(&TestNodeNoSimNode::on_srv_server_1, this, std::placeholders::_1, std::placeholders::_2));
    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/foo/{bad_sub}");
    srv_client_1_ = this->create_client<std_srvs::srv::Trigger>("/get/parameters");
    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&TestNodeNoSimNode::on_pub_timer_0, this));
    timer_pub_1_ = this->create_wall_timer(500ms, std::bind(&TestNodeNoSimNode::on_pub_timer_1, this));
    timer_pub_2_ = this->create_wall_timer(500ms, std::bind(&TestNodeNoSimNode::on_pub_timer_2, this));
    timer_pub_3_ = this->create_wall_timer(500ms, std::bind(&TestNodeNoSimNode::on_pub_timer_3, this));
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&TestNodeNoSimNode::on_srv_client_timer_0, this));
    timer_srv_client_1_ = this->create_wall_timer(1000ms, std::bind(&TestNodeNoSimNode::on_srv_client_timer_1, this));
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

  void on_pub_timer_3()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_3_->publish(msg);
  }

  void on_sub_0(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_1(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

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

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_1_;
  rclcpp::TimerBase::SharedPtr timer_pub_1_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_2_;
  rclcpp::TimerBase::SharedPtr timer_pub_2_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_3_;
  rclcpp::TimerBase::SharedPtr timer_pub_3_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_0_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_1_;
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
    auto node = std::make_shared<TestNodeNoSimNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
