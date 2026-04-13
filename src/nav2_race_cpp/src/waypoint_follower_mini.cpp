#include <chrono>
#include <memory>
#include <thread>

#include "geometry_msgs/msg/pose_stamped.hpp"
#include "nav2_msgs/action/follow_waypoints.hpp"
#include "nav2_msgs/action/navigate_to_pose.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"

using namespace std::chrono_literals;

class WaypointFollowerMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  using FollowWaypoints = nav2_msgs::action::FollowWaypoints;
  using NavigateToPose = nav2_msgs::action::NavigateToPose;
  using GoalHandleFW = rclcpp_action::ServerGoalHandle<FollowWaypoints>;

  WaypointFollowerMini()
  : rclcpp_lifecycle::LifecycleNode("waypoint_follower"), active_(false)
  {}

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    nav_client_ = rclcpp_action::create_client<NavigateToPose>(this, "navigate_to_pose");

    action_server_ = rclcpp_action::create_server<FollowWaypoints>(
      get_node_base_interface(),
      get_node_clock_interface(),
      get_node_logging_interface(),
      get_node_waitables_interface(),
      "follow_waypoints",
      std::bind(&WaypointFollowerMini::handle_goal, this, std::placeholders::_1, std::placeholders::_2),
      std::bind(&WaypointFollowerMini::handle_cancel, this, std::placeholders::_1),
      std::bind(&WaypointFollowerMini::handle_accepted, this, std::placeholders::_1));

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
  rclcpp_action::GoalResponse handle_goal(
    const rclcpp_action::GoalUUID &,
    std::shared_ptr<const FollowWaypoints::Goal>)
  {
    return active_ ? rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE : rclcpp_action::GoalResponse::REJECT;
  }

  rclcpp_action::CancelResponse handle_cancel(const std::shared_ptr<GoalHandleFW>)
  {
    return rclcpp_action::CancelResponse::ACCEPT;
  }

  void handle_accepted(const std::shared_ptr<GoalHandleFW> goal_handle)
  {
    std::thread([this, goal_handle]() {
      auto result = std::make_shared<FollowWaypoints::Result>();

      if (!nav_client_->wait_for_action_server(2s)) {
        result->missed_waypoints = {0};
        goal_handle->abort(result);
        return;
      }

      for (size_t i = 0; i < goal_handle->get_goal()->poses.size(); ++i) {
        if (goal_handle->is_canceling()) {
          goal_handle->canceled(result);
          return;
        }

        NavigateToPose::Goal g;
        g.pose = goal_handle->get_goal()->poses[i];

        auto nav_goal_future = nav_client_->async_send_goal(g);
        if (nav_goal_future.wait_for(2s) != std::future_status::ready) {
          result->missed_waypoints.push_back(static_cast<int32_t>(i));
          continue;
        }

        auto nav_handle = nav_goal_future.get();
        if (!nav_handle) {
          result->missed_waypoints.push_back(static_cast<int32_t>(i));
          continue;
        }

        auto nav_result_future = nav_client_->async_get_result(nav_handle);
        if (nav_result_future.wait_for(4s) != std::future_status::ready) {
          result->missed_waypoints.push_back(static_cast<int32_t>(i));
          continue;
        }
      }

      goal_handle->succeed(result);
    }).detach();
  }

  bool active_;
  rclcpp_action::Server<FollowWaypoints>::SharedPtr action_server_;
  rclcpp_action::Client<NavigateToPose>::SharedPtr nav_client_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<WaypointFollowerMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
