#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestServiceNode : public rclcpp::Node
{
public:
  TestServiceNode()
  : rclcpp::Node("test_service")
  {


    srv_server_0_ = this->create_service<std_srvs::srv::Trigger>("/hello_world", std::bind(&TestServiceNode::on_srv_server_0, this, std::placeholders::_1, std::placeholders::_2));


  }

private:
  void on_srv_server_0(const std::shared_ptr<std_srvs::srv::Trigger::Request> req, std::shared_ptr<std_srvs::srv::Trigger::Response> resp)
  {
    (void)req;
    resp->success = true;
    resp->message = "ok";
  }

  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr srv_server_0_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestServiceNode>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
