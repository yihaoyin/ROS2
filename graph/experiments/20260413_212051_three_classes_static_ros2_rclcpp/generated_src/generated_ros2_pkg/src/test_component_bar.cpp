#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestComponentBarNode : public rclcpp::Node
{
public:
  TestComponentBarNode()
  : rclcpp::Node("test_component_bar")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/chatter", 10);
    pub_1_ = this->create_publisher<std_msgs::msg::String>("/content_filter_topic", 10);
    pub_2_ = this->create_publisher<std_msgs::msg::String>("/custom_allocator_test", 10);
    pub_3_ = this->create_publisher<std_msgs::msg::String>("/loaned_message_test_topic", 10);
    pub_4_ = this->create_publisher<std_msgs::msg::String>("/node1_topic", 10);
    pub_5_ = this->create_publisher<std_msgs::msg::String>("/test_drop", 10);
    pub_6_ = this->create_publisher<std_msgs::msg::String>("/test_topic", 10);
    pub_7_ = this->create_publisher<std_msgs::msg::String>("/topic", 10);
    pub_8_ = this->create_publisher<std_msgs::msg::String>("/topic_name", 10);
    pub_9_ = this->create_publisher<std_msgs::msg::String>("/wait_for_last_message_topic", 10);
    pub_10_ = this->create_publisher<std_msgs::msg::String>("/wait_for_message_topic", 10);
    sub_0_ = this->create_subscription<std_msgs::msg::String>("/service/_service_event", 10, std::bind(&TestComponentBarNode::on_sub_0, this, std::placeholders::_1));
    sub_1_ = this->create_subscription<std_msgs::msg::String>("/test_drop", 10, std::bind(&TestComponentBarNode::on_sub_1, this, std::placeholders::_1));
    sub_2_ = this->create_subscription<std_msgs::msg::String>("/test_topic", 10, std::bind(&TestComponentBarNode::on_sub_2, this, std::placeholders::_1));
    sub_3_ = this->create_subscription<std_msgs::msg::String>("/topic", 10, std::bind(&TestComponentBarNode::on_sub_3, this, std::placeholders::_1));
    sub_4_ = this->create_subscription<std_msgs::msg::String>("/topic_name", 10, std::bind(&TestComponentBarNode::on_sub_4, this, std::placeholders::_1));
    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/not_a_service", std::bind(&TestComponentBarNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_1_ = this->create_service<std_srvs::srv::Trigger>("/service", std::bind(&TestComponentBarNode::on_srv_server_1, this, std::placeholders::_1, std::placeholders::_2));
    srv_server_2_ = this->create_service<std_srvs::srv::Trigger>("/test_service", std::bind(&TestComponentBarNode::on_srv_server_2, this, std::placeholders::_1, std::placeholders::_2));
    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/not_an_existing_service");
    srv_client_1_ = this->create_client<std_srvs::srv::Trigger>("/service");
    srv_client_2_ = this->create_client<std_srvs::srv::Trigger>("/test_qos_depth");
    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_0, this));
    timer_pub_1_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_1, this));
    timer_pub_2_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_2, this));
    timer_pub_3_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_3, this));
    timer_pub_4_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_4, this));
    timer_pub_5_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_5, this));
    timer_pub_6_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_6, this));
    timer_pub_7_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_7, this));
    timer_pub_8_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_8, this));
    timer_pub_9_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_9, this));
    timer_pub_10_ = this->create_wall_timer(500ms, std::bind(&TestComponentBarNode::on_pub_timer_10, this));
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&TestComponentBarNode::on_srv_client_timer_0, this));
    timer_srv_client_1_ = this->create_wall_timer(1000ms, std::bind(&TestComponentBarNode::on_srv_client_timer_1, this));
    timer_srv_client_2_ = this->create_wall_timer(1000ms, std::bind(&TestComponentBarNode::on_srv_client_timer_2, this));
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

  void on_pub_timer_4()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_4_->publish(msg);
  }

  void on_pub_timer_5()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_5_->publish(msg);
  }

  void on_pub_timer_6()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_6_->publish(msg);
  }

  void on_pub_timer_7()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_7_->publish(msg);
  }

  void on_pub_timer_8()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_8_->publish(msg);
  }

  void on_pub_timer_9()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_9_->publish(msg);
  }

  void on_pub_timer_10()
  {
    std_msgs::msg::String msg;
    msg.data = "tick";
    pub_10_->publish(msg);
  }

  void on_sub_0(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_1(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_2(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_3(const std_msgs::msg::String::SharedPtr msg)
  {
    (void)msg;
  }

  void on_sub_4(const std_msgs::msg::String::SharedPtr msg)
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

  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_0_;
  rclcpp::TimerBase::SharedPtr timer_pub_0_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_1_;
  rclcpp::TimerBase::SharedPtr timer_pub_1_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_2_;
  rclcpp::TimerBase::SharedPtr timer_pub_2_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_3_;
  rclcpp::TimerBase::SharedPtr timer_pub_3_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_4_;
  rclcpp::TimerBase::SharedPtr timer_pub_4_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_5_;
  rclcpp::TimerBase::SharedPtr timer_pub_5_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_6_;
  rclcpp::TimerBase::SharedPtr timer_pub_6_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_7_;
  rclcpp::TimerBase::SharedPtr timer_pub_7_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_8_;
  rclcpp::TimerBase::SharedPtr timer_pub_8_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_9_;
  rclcpp::TimerBase::SharedPtr timer_pub_9_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_10_;
  rclcpp::TimerBase::SharedPtr timer_pub_10_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_0_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_1_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_2_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_3_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_4_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_0_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_1_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_2_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_1_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_1_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_2_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_2_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestComponentBarNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
