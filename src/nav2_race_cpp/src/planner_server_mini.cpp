#include <chrono>
#include <memory>
#include <thread>

#include "geometry_msgs/msg/pose_stamped.hpp"
#include "nav2_msgs/action/compute_path_to_pose.hpp"
#include "nav_msgs/msg/occupancy_grid.hpp"
#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "std_msgs/msg/string.hpp"

using namespace std::chrono_literals;

class PlannerServerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  using ActionT = nav2_msgs::action::ComputePathToPose;
  using GoalHandle = rclcpp_action::ServerGoalHandle<ActionT>;

  PlannerServerMini()
  : rclcpp_lifecycle::LifecycleNode("planner_server"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    plan_pub_ = this->create_publisher<nav_msgs::msg::Path>("plan", 10);
    state_pub_ = this->create_publisher<std_msgs::msg::String>("planner_state", 10);
    global_costmap_sub_ = this->create_subscription<nav_msgs::msg::OccupancyGrid>(
      "global_costmap/costmap", 10,
      [this](const nav_msgs::msg::OccupancyGrid::SharedPtr msg) {
        (void)msg;
      });

    action_server_ = rclcpp_action::create_server<ActionT>(
      get_node_base_interface(), get_node_clock_interface(),
      get_node_logging_interface(), get_node_waitables_interface(),
      "compute_path_to_pose",
      std::bind(&PlannerServerMini::handle_goal, this, std::placeholders::_1, std::placeholders::_2),
      std::bind(&PlannerServerMini::handle_cancel, this, std::placeholders::_1),
      std::bind(&PlannerServerMini::handle_accepted, this, std::placeholders::_1));

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
      auto result = std::make_shared<ActionT::Result>();
      nav_msgs::msg::Path path;
      path.header.frame_id = "map";
      path.poses.push_back(goal_handle->get_goal()->start);
      path.poses.push_back(goal_handle->get_goal()->goal);
      result->path = path;
      const auto ns = rclcpp::Duration::from_seconds(0.02).nanoseconds();
      result->planning_time.sec = static_cast<int32_t>(ns / 1000000000LL);
      result->planning_time.nanosec = static_cast<uint32_t>(ns % 1000000000LL);
      plan_pub_->publish(path);
      std_msgs::msg::String st;
      st.data = "plan_ready";
      state_pub_->publish(st);
      std::this_thread::sleep_for(20ms);
      goal_handle->succeed(result);
    }).detach();
  }

  bool active_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr global_costmap_sub_;
  rclcpp::Publisher<nav_msgs::msg::Path>::SharedPtr plan_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_pub_;
  rclcpp_action::Server<ActionT>::SharedPtr action_server_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<PlannerServerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
