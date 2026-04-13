#include <chrono>
#include <memory>
#include <thread>

#include "geometry_msgs/msg/twist.hpp"
#include "nav2_msgs/action/follow_path.hpp"
#include "nav_msgs/msg/occupancy_grid.hpp"
#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"

using namespace std::chrono_literals;

class ControllerServerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  using ActionT = nav2_msgs::action::FollowPath;
  using GoalHandle = rclcpp_action::ServerGoalHandle<ActionT>;

  ControllerServerMini()
  : rclcpp_lifecycle::LifecycleNode("controller_server"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    cmd_pub_ = this->create_publisher<geometry_msgs::msg::Twist>("cmd_vel_raw", 10);
    plan_sub_ = this->create_subscription<nav_msgs::msg::Path>(
      "plan", 10,
      [this](const nav_msgs::msg::Path::SharedPtr msg) {
        (void)msg;
      });
    local_costmap_sub_ = this->create_subscription<nav_msgs::msg::OccupancyGrid>(
      "local_costmap/costmap", 10,
      [this](const nav_msgs::msg::OccupancyGrid::SharedPtr msg) {
        (void)msg;
      });

    action_server_ = rclcpp_action::create_server<ActionT>(
      get_node_base_interface(), get_node_clock_interface(),
      get_node_logging_interface(), get_node_waitables_interface(),
      "follow_path",
      std::bind(&ControllerServerMini::handle_goal, this, std::placeholders::_1, std::placeholders::_2),
      std::bind(&ControllerServerMini::handle_cancel, this, std::placeholders::_1),
      std::bind(&ControllerServerMini::handle_accepted, this, std::placeholders::_1));

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
  rclcpp_action::GoalResponse handle_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const ActionT::Goal>)
  {
    return active_ ? rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE : rclcpp_action::GoalResponse::REJECT;
  }

  rclcpp_action::CancelResponse handle_cancel(const std::shared_ptr<GoalHandle>)
  {
    return rclcpp_action::CancelResponse::ACCEPT;
  }

  void handle_accepted(const std::shared_ptr<GoalHandle> goal_handle)
  {
    std::thread([this, goal_handle]() {
      auto feedback = std::make_shared<ActionT::Feedback>();
      auto result = std::make_shared<ActionT::Result>();

      for (int i = 0; i < 20; ++i) {
        if (goal_handle->is_canceling()) {
          goal_handle->canceled(result);
          return;
        }
        geometry_msgs::msg::Twist tw;
        tw.linear.x = 0.1;
        cmd_pub_->publish(tw);
        feedback->distance_to_goal = static_cast<float>(20 - i) * 0.05f;
        feedback->speed = 0.1f;
        goal_handle->publish_feedback(feedback);
        std::this_thread::sleep_for(10ms);
      }

      goal_handle->succeed(result);
    }).detach();
  }

  bool active_;
  rclcpp::Subscription<nav_msgs::msg::Path>::SharedPtr plan_sub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr local_costmap_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_pub_;
  rclcpp_action::Server<ActionT>::SharedPtr action_server_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<ControllerServerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
