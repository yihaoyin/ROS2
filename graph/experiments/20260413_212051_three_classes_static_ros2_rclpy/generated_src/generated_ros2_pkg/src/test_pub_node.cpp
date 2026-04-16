#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestPubNodeNode : public rclcpp::Node
{
public:
  TestPubNodeNode()
  : rclcpp::Node("test_pub_node")
  {
    pub_0_ = this->create_publisher<std_msgs::msg::String>("/pub1_topic", 10);
    pub_1_ = this->create_publisher<std_msgs::msg::String>("/pub2_topic", 10);
    pub_2_ = this->create_publisher<std_msgs::msg::String>("/pub_topic", 10);
    pub_3_ = this->create_publisher<std_msgs::msg::String>("/raw_subscription_test", 10);
    pub_4_ = this->create_publisher<std_msgs::msg::String>("/take_test", 10);
    pub_5_ = this->create_publisher<std_msgs::msg::String>("/test_topic", 10);
    pub_6_ = this->create_publisher<std_msgs::msg::String>("/topic", 10);
    pub_7_ = this->create_publisher<std_msgs::msg::String>("/trigger_long", 10);
    pub_8_ = this->create_publisher<std_msgs::msg::String>("/trigger_short", 10);


    srv_client_0_ = this->create_client<std_srvs::srv::Trigger>("/test_service");
    timer_pub_0_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_0, this));
    timer_pub_1_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_1, this));
    timer_pub_2_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_2, this));
    timer_pub_3_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_3, this));
    timer_pub_4_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_4, this));
    timer_pub_5_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_5, this));
    timer_pub_6_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_6, this));
    timer_pub_7_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_7, this));
    timer_pub_8_ = this->create_wall_timer(500ms, std::bind(&TestPubNodeNode::on_pub_timer_8, this));
    timer_srv_client_0_ = this->create_wall_timer(1000ms, std::bind(&TestPubNodeNode::on_srv_client_timer_0, this));
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
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr srv_client_0_;
  rclcpp::TimerBase::SharedPtr timer_srv_client_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestPubNodeNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
