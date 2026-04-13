#include <chrono>
#include <memory>
#include <thread>

#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class SmootherServerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  SmootherServerMini()
  : rclcpp_lifecycle::LifecycleNode("smoother_server"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    plan_sub_ = this->create_subscription<nav_msgs::msg::Path>(
      "plan", 10,
      [this](const nav_msgs::msg::Path::SharedPtr msg) {
        last_plan_ = *msg;
      });

    smooth_pub_ = this->create_publisher<nav_msgs::msg::Path>("smoothed_plan", 10);

    smooth_srv_ = this->create_service<std_srvs::srv::Trigger>(
      "smooth_path",
      [this](
        const std::shared_ptr<std_srvs::srv::Trigger::Request>,
        std::shared_ptr<std_srvs::srv::Trigger::Response> resp) {
        if (!active_) {
          resp->success = false;
          resp->message = "inactive";
          return;
        }
        nav_msgs::msg::Path out = last_plan_;
        if (out.header.frame_id.empty()) {
          out.header.frame_id = "map";
        }
        smooth_pub_->publish(out);
        std::this_thread::sleep_for(20ms);
        resp->success = true;
        resp->message = "smoothed";
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
  nav_msgs::msg::Path last_plan_;
  rclcpp::Subscription<nav_msgs::msg::Path>::SharedPtr plan_sub_;
  rclcpp::Publisher<nav_msgs::msg::Path>::SharedPtr smooth_pub_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr smooth_srv_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<SmootherServerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
