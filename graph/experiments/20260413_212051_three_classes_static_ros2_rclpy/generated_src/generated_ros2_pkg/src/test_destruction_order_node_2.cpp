#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/executors/single_threaded_executor.hpp"
#include "rclcpp/executors/multi_threaded_executor.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class TestDestructionOrderNode2Node : public rclcpp::Node
{
public:
  TestDestructionOrderNode2Node()
  : rclcpp::Node("test_destruction_order_node_2")
  {





  }

private:
  void idle() {}

  int unused_ = 0;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
    auto node = std::make_shared<TestDestructionOrderNode2Node>();
  rclcpp::executors::SingleThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
