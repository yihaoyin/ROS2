#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

class BehaviorServerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  BehaviorServerMini()
  : rclcpp_lifecycle::LifecycleNode("behavior_server"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    behavior_pub_ = this->create_publisher<std_msgs::msg::String>("behavior_tree_log", 10);

    spin_srv_ = this->create_service<std_srvs::srv::Trigger>(
      "behavior_server/spin",
      [this](
        const std::shared_ptr<std_srvs::srv::Trigger::Request>,
        std::shared_ptr<std_srvs::srv::Trigger::Response> resp) {
        if (!active_) {
          resp->success = false;
          resp->message = "inactive";
          return;
        }
        std_msgs::msg::String msg;
        msg.data = "behavior_spin_invoked";
        behavior_pub_->publish(msg);
        resp->success = true;
        resp->message = "spin_done";
      });

    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &)
  {
    active_ = true;
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &)
  {
    active_ = false;
    return CallbackReturn::SUCCESS;
  }

private:
  bool active_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr behavior_pub_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr spin_srv_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<BehaviorServerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
