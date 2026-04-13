#include <chrono>
#include <functional>
#include <iomanip>
#include <memory>
#include <mutex>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <thread>

#include "geometry_msgs/msg/pose_stamped.hpp"
#include "lifecycle_msgs/msg/state.hpp"
#include "nav2_msgs/action/compute_path_to_pose.hpp"
#include "nav2_msgs/action/follow_path.hpp"
#include "nav2_msgs/action/navigate_to_pose.hpp"
#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_srvs/srv/trigger.hpp"

using namespace std::chrono_literals;

class BtNavigatorMini : public rclcpp_lifecycle::LifecycleNode
{
public:
  using ComputePathToPose = nav2_msgs::action::ComputePathToPose;
  using FollowPath = nav2_msgs::action::FollowPath;
  using NavigateToPose = nav2_msgs::action::NavigateToPose;
  using GoalHandleNav = rclcpp_action::ServerGoalHandle<NavigateToPose>;

  BtNavigatorMini()
  : rclcpp_lifecycle::LifecycleNode("bt_navigator"), active_(false)
  {
    this->declare_parameter("execute_seconds", 3.0);
    this->declare_parameter("deactivate_wait_seconds", 1.0);
    this->declare_parameter("race_bug_mode", true);
    this->declare_parameter("cancel_leak_probability", 0.03);
    this->declare_parameter("autogoal_enabled", false);
    this->declare_parameter("autogoal_period_sec", 0.8);
    this->declare_parameter("autogoal_x", 1.0);
    this->declare_parameter("autogoal_y", 0.0);
  }

  CallbackReturn on_configure(const rclcpp_lifecycle::State &)
  {
    plan_sub_ = this->create_subscription<nav_msgs::msg::Path>(
      "plan", rclcpp::SystemDefaultsQoS(),
      [this](const nav_msgs::msg::Path::SharedPtr msg) {
        (void)msg;
        std_msgs::msg::String log;
        log.data = "plan_received";
        bt_log_pub_->publish(log);
      });

    goal_sub_ = this->create_subscription<geometry_msgs::msg::PoseStamped>(
      "goal_pose", rclcpp::SystemDefaultsQoS(),
      [this](const geometry_msgs::msg::PoseStamped::SharedPtr msg) {
        (void)msg;
        std_msgs::msg::String log;
        log.data = "goal_pose_received";
        bt_log_pub_->publish(log);
      });

    goal_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>("goal_pose", 10);

    bt_log_pub_ = this->create_publisher<std_msgs::msg::String>("behavior_tree_log", 10);

    planner_client_ = rclcpp_action::create_client<ComputePathToPose>(
      this,
      "compute_path_to_pose");

    controller_client_ = rclcpp_action::create_client<FollowPath>(
      this,
      "follow_path");

    self_nav_client_ = rclcpp_action::create_client<NavigateToPose>(
      this,
      "navigate_to_pose");

    smoother_client_ = this->create_client<std_srvs::srv::Trigger>("smooth_path");
    behavior_client_ = this->create_client<std_srvs::srv::Trigger>("behavior_server/spin");

    action_server_ = rclcpp_action::create_server<NavigateToPose>(
      get_node_base_interface(),
      get_node_clock_interface(),
      get_node_logging_interface(),
      get_node_waitables_interface(),
      "navigate_to_pose",
      std::bind(&BtNavigatorMini::handle_goal, this, std::placeholders::_1, std::placeholders::_2),
      std::bind(&BtNavigatorMini::handle_cancel, this, std::placeholders::_1),
      std::bind(&BtNavigatorMini::handle_accepted, this, std::placeholders::_1));

    RCLCPP_INFO(get_logger(), "bt_navigator configured");
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &)
  {
    active_ = true;
    RCLCPP_INFO(get_logger(), "bt_navigator activated");

    if (this->get_parameter("autogoal_enabled").as_bool()) {
      const auto period = this->get_parameter("autogoal_period_sec").as_double();
      auto_goal_timer_ = this->create_wall_timer(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::duration<double>(period)),
        std::bind(&BtNavigatorMini::publish_and_send_auto_goal, this));
    }

    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &)
  {
    active_ = false;
    auto_goal_timer_.reset();
    const auto timeout = this->get_parameter("deactivate_wait_seconds").as_double();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout);

    while (std::chrono::steady_clock::now() < deadline) {
      {
        std::lock_guard<std::mutex> lock(mu_);
        if (in_flight_.empty()) {
          RCLCPP_INFO(get_logger(), "bt_navigator deactivated cleanly");
          return CallbackReturn::SUCCESS;
        }
      }
      std::this_thread::sleep_for(10ms);
    }

    size_t left = 0;
    {
      std::lock_guard<std::mutex> lock(mu_);
      left = in_flight_.size();
    }
    RCLCPP_ERROR(get_logger(), "deactivate timeout, in-flight goals=%zu", left);
    return CallbackReturn::FAILURE;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &)
  {
    action_server_.reset();
    goal_sub_.reset();
    goal_pub_.reset();
    plan_sub_.reset();
    bt_log_pub_.reset();
    planner_client_.reset();
    controller_client_.reset();
    self_nav_client_.reset();
    smoother_client_.reset();
    behavior_client_.reset();
    auto_goal_timer_.reset();

    std::lock_guard<std::mutex> lock(mu_);
    in_flight_.clear();
    return CallbackReturn::SUCCESS;
  }

private:
  rclcpp_action::GoalResponse handle_goal(
    const rclcpp_action::GoalUUID &,
    std::shared_ptr<const NavigateToPose::Goal>)
  {
    if (!active_) {
      return rclcpp_action::GoalResponse::REJECT;
    }
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
  }

  rclcpp_action::CancelResponse handle_cancel(const std::shared_ptr<GoalHandleNav>)
  {
    if (this->get_parameter("race_bug_mode").as_bool()) {
      static thread_local std::mt19937 gen{std::random_device{}()};
      std::uniform_int_distribution<int> dist(10, 120);
      std::this_thread::sleep_for(std::chrono::milliseconds(dist(gen)));
    }
    return rclcpp_action::CancelResponse::ACCEPT;
  }

  void handle_accepted(const std::shared_ptr<GoalHandleNav> goal_handle)
  {
    std::thread{std::bind(&BtNavigatorMini::execute, this, std::placeholders::_1), goal_handle}.detach();
  }

  std::string uuid_to_string(const rclcpp_action::GoalUUID & uuid)
  {
    std::ostringstream oss;
    for (auto b : uuid) {
      oss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(b);
    }
    return oss.str();
  }

  bool call_trigger_blocking(
    const rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr & client,
    const std::chrono::milliseconds & timeout,
    const std::string & label)
  {
    if (!client || !client->wait_for_service(timeout)) {
      std_msgs::msg::String log;
      log.data = label + "_service_unavailable";
      bt_log_pub_->publish(log);
      return false;
    }

    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    auto fut = client->async_send_request(req);
    if (fut.wait_for(timeout) != std::future_status::ready) {
      std_msgs::msg::String log;
      log.data = label + "_timeout";
      bt_log_pub_->publish(log);
      return false;
    }

    auto resp = fut.get();
    if (!resp->success) {
      std_msgs::msg::String log;
      log.data = label + "_failed";
      bt_log_pub_->publish(log);
      return false;
    }
    return true;
  }

  void execute(const std::shared_ptr<GoalHandleNav> goal_handle)
  {
    const auto gid = uuid_to_string(goal_handle->get_goal_id());
    {
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.insert(gid);
    }

    auto result = std::make_shared<NavigateToPose::Result>();
    auto feedback = std::make_shared<NavigateToPose::Feedback>();

    std_msgs::msg::String log;
    log.data = "navigate_begin";
    bt_log_pub_->publish(log);

    if (!planner_client_->wait_for_action_server(2s) || !controller_client_->wait_for_action_server(2s)) {
      log.data = "action_server_unavailable";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto planner_goal = ComputePathToPose::Goal();
    planner_goal.start.header.frame_id = "map";
    planner_goal.start.pose.orientation.w = 1.0;
    planner_goal.goal = goal_handle->get_goal()->pose;

    auto planner_goal_future = planner_client_->async_send_goal(planner_goal);
    if (planner_goal_future.wait_for(2s) != std::future_status::ready) {
      log.data = "planner_goal_send_timeout";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto planner_handle = planner_goal_future.get();
    if (!planner_handle) {
      log.data = "planner_goal_rejected";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto planner_result_future = planner_client_->async_get_result(planner_handle);
    if (planner_result_future.wait_for(3s) != std::future_status::ready) {
      log.data = "planner_result_timeout";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto planner_wrapped = planner_result_future.get();
    if (planner_wrapped.code != rclcpp_action::ResultCode::SUCCEEDED) {
      log.data = "planner_failed";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    if (!call_trigger_blocking(smoother_client_, 1500ms, "smoother_call")) {
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto controller_goal = FollowPath::Goal();
    controller_goal.path = planner_wrapped.result->path;

    auto controller_goal_future = controller_client_->async_send_goal(controller_goal);
    if (controller_goal_future.wait_for(2s) != std::future_status::ready) {
      log.data = "controller_goal_send_timeout";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto controller_handle = controller_goal_future.get();
    if (!controller_handle) {
      log.data = "controller_goal_rejected";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto controller_result_future = controller_client_->async_get_result(controller_handle);
    const auto exec_s = this->get_parameter("execute_seconds").as_double();
    const auto end_t = std::chrono::steady_clock::now() + std::chrono::duration<double>(exec_s);

    while (std::chrono::steady_clock::now() < end_t) {
      if (goal_handle->is_canceling()) {
        (void)call_trigger_blocking(behavior_client_, 800ms, "behavior_spin_on_cancel");
        controller_client_->async_cancel_goal(controller_handle);
        goal_handle->canceled(result);
        if (this->get_parameter("race_bug_mode").as_bool()) {
          static thread_local std::mt19937 gen{std::random_device{}()};
          std::uniform_real_distribution<double> dist(0.0, 1.0);
          if (dist(gen) < this->get_parameter("cancel_leak_probability").as_double()) {
            return;
          }
        }
        std::lock_guard<std::mutex> lock(mu_);
        in_flight_.erase(gid);
        return;
      }

      const auto ns = rclcpp::Duration::from_seconds(exec_s).nanoseconds();
      feedback->navigation_time.sec = static_cast<int32_t>(ns / 1000000000LL);
      feedback->navigation_time.nanosec = static_cast<uint32_t>(ns % 1000000000LL);
      feedback->distance_remaining = 1.0f;
      goal_handle->publish_feedback(feedback);

      if (controller_result_future.wait_for(10ms) == std::future_status::ready) {
        break;
      }
      std::this_thread::sleep_for(20ms);
    }

    if (controller_result_future.wait_for(100ms) != std::future_status::ready) {
      log.data = "controller_result_timeout";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    auto controller_wrapped = controller_result_future.get();
    if (controller_wrapped.code != rclcpp_action::ResultCode::SUCCEEDED) {
      log.data = "controller_failed";
      bt_log_pub_->publish(log);
      goal_handle->abort(result);
      std::lock_guard<std::mutex> lock(mu_);
      in_flight_.erase(gid);
      return;
    }

    log.data = "navigate_succeeded";
    bt_log_pub_->publish(log);
    goal_handle->succeed(result);
    std::lock_guard<std::mutex> lock(mu_);
    in_flight_.erase(gid);
  }

  void publish_and_send_auto_goal()
  {
    if (!active_ || !self_nav_client_) {
      return;
    }
    if (!self_nav_client_->wait_for_action_server(100ms)) {
      return;
    }

    geometry_msgs::msg::PoseStamped pose;
    pose.header.frame_id = "map";
    pose.header.stamp = this->now();
    pose.pose.position.x = this->get_parameter("autogoal_x").as_double();
    pose.pose.position.y = this->get_parameter("autogoal_y").as_double();
    pose.pose.orientation.w = 1.0;
    goal_pub_->publish(pose);

    NavigateToPose::Goal nav_goal;
    nav_goal.pose = pose;
    auto fut = self_nav_client_->async_send_goal(nav_goal);
    self_goal_futures_.push_back(fut);
    if (self_goal_futures_.size() > 32) {
      self_goal_futures_.erase(self_goal_futures_.begin(), self_goal_futures_.begin() + 16);
    }
  }

  bool active_;
  std::mutex mu_;
  std::set<std::string> in_flight_;

  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr goal_sub_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr goal_pub_;
  rclcpp::Subscription<nav_msgs::msg::Path>::SharedPtr plan_sub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr bt_log_pub_;
  rclcpp_action::Server<NavigateToPose>::SharedPtr action_server_;
  rclcpp_action::Client<ComputePathToPose>::SharedPtr planner_client_;
  rclcpp_action::Client<FollowPath>::SharedPtr controller_client_;
  rclcpp_action::Client<NavigateToPose>::SharedPtr self_nav_client_;
  std::vector<std::shared_future<rclcpp_action::ClientGoalHandle<NavigateToPose>::SharedPtr>> self_goal_futures_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr smoother_client_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr behavior_client_;
  rclcpp::TimerBase::SharedPtr auto_goal_timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<BtNavigatorMini>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node->get_node_base_interface());
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
