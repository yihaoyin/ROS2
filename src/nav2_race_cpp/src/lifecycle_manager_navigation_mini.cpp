#include <chrono>
#include <future>
#include <mutex>
#include <memory>
#include <string>
#include <vector>

#include "lifecycle_msgs/msg/state.hpp"
#include "lifecycle_msgs/msg/transition.hpp"
#include "lifecycle_msgs/srv/change_state.hpp"
#include "lifecycle_msgs/srv/get_state.hpp"
#include "nav2_msgs/srv/manage_lifecycle_nodes.hpp"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

using namespace std::chrono_literals;

class LifecycleManagerNavigationMini : public rclcpp::Node
{
public:
  LifecycleManagerNavigationMini()
  : Node("lifecycle_manager_navigation")
  {
    this->declare_parameter("managed_nodes", std::vector<std::string>{
      "planner_server", "controller_server", "bt_navigator"});
    this->declare_parameter("autostart", true);

    managed_nodes_ = this->get_parameter("managed_nodes").as_string_array();

    event_pub_ = this->create_publisher<std_msgs::msg::String>("lifecycle_manager_navigation/events", 10);

    manage_srv_ = this->create_service<nav2_msgs::srv::ManageLifecycleNodes>(
      "lifecycle_manager_navigation/manage_nodes",
      std::bind(
        &LifecycleManagerNavigationMini::on_manage_nodes, this,
        std::placeholders::_1, std::placeholders::_2));

    if (this->get_parameter("autostart").as_bool()) {
      autostart_timer_ = this->create_wall_timer(1200ms, [this]() {
        autostart_timer_->cancel();
        (void)run_startup();
      });
    }
  }

private:
  uint8_t get_state(const std::string & node_name)
  {
    const auto service_name = "/" + node_name + "/get_state";
    auto client = this->create_client<lifecycle_msgs::srv::GetState>(service_name);
    if (!client->wait_for_service(1200ms)) {
      return lifecycle_msgs::msg::State::PRIMARY_STATE_UNKNOWN;
    }

    auto req = std::make_shared<lifecycle_msgs::srv::GetState::Request>();
    auto fut = client->async_send_request(req);
    if (fut.wait_for(1200ms) != std::future_status::ready) {
      return lifecycle_msgs::msg::State::PRIMARY_STATE_UNKNOWN;
    }
    return fut.get()->current_state.id;
  }

  bool request_transition(const std::string & node_name, uint8_t transition_id)
  {
    const auto service_name = "/" + node_name + "/change_state";
    auto client = this->create_client<lifecycle_msgs::srv::ChangeState>(service_name);
    if (!client->wait_for_service(1200ms)) {
      RCLCPP_WARN(get_logger(), "service not available: %s", service_name.c_str());
      return false;
    }

    auto req = std::make_shared<lifecycle_msgs::srv::ChangeState::Request>();
    req->transition.id = transition_id;
    auto fut = client->async_send_request(req);
    if (fut.wait_for(1200ms) != std::future_status::ready) {
      RCLCPP_WARN(get_logger(), "change_state timeout on %s", node_name.c_str());
      return false;
    }
    return fut.get()->success;
  }

  bool ensure_started(const std::string & node_name)
  {
    auto s = get_state(node_name);
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE) {
      return true;
    }

    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_UNCONFIGURED) {
      if (!request_transition(node_name, lifecycle_msgs::msg::Transition::TRANSITION_CONFIGURE)) {
        return false;
      }
      s = get_state(node_name);
    }

    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
      return request_transition(node_name, lifecycle_msgs::msg::Transition::TRANSITION_ACTIVATE);
    }

    return s == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE;
  }

  bool ensure_paused(const std::string & node_name)
  {
    auto s = get_state(node_name);
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
      return true;
    }
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE) {
      return request_transition(node_name, lifecycle_msgs::msg::Transition::TRANSITION_DEACTIVATE);
    }
    return true;
  }

  bool ensure_unconfigured(const std::string & node_name)
  {
    auto s = get_state(node_name);
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_UNCONFIGURED) {
      return true;
    }
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE) {
      if (!request_transition(node_name, lifecycle_msgs::msg::Transition::TRANSITION_DEACTIVATE)) {
        return false;
      }
      s = get_state(node_name);
    }
    if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
      return request_transition(node_name, lifecycle_msgs::msg::Transition::TRANSITION_CLEANUP);
    }
    return true;
  }

  bool run_startup()
  {
    std::lock_guard<std::mutex> lock(mu_);
    bool ok = true;
    for (const auto & n : managed_nodes_) {
      ok = ok && ensure_started(n);
    }
    return ok;
  }

  bool run_pause()
  {
    std::lock_guard<std::mutex> lock(mu_);
    bool ok = true;
    for (const auto & n : managed_nodes_) {
      ok = ok && ensure_paused(n);
    }
    return ok;
  }

  bool run_resume()
  {
    std::lock_guard<std::mutex> lock(mu_);
    bool ok = true;
    for (const auto & n : managed_nodes_) {
      ok = ok && ensure_started(n);
    }
    return ok;
  }

  bool run_reset()
  {
    std::lock_guard<std::mutex> lock(mu_);
    bool ok = true;
    for (const auto & n : managed_nodes_) {
      ok = ok && ensure_unconfigured(n);
    }
    return ok;
  }

  bool run_shutdown()
  {
    std::lock_guard<std::mutex> lock(mu_);
    bool ok = true;
    for (const auto & n : managed_nodes_) {
      auto s = get_state(n);
      if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_FINALIZED) {
        continue;
      }
      if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE &&
        !request_transition(n, lifecycle_msgs::msg::Transition::TRANSITION_DEACTIVATE))
      {
        ok = false;
        continue;
      }
      s = get_state(n);
      if (s == lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE &&
        !request_transition(n, lifecycle_msgs::msg::Transition::TRANSITION_CLEANUP))
      {
        ok = false;
        continue;
      }
      ok = ok && request_transition(n, lifecycle_msgs::msg::Transition::TRANSITION_UNCONFIGURED_SHUTDOWN);
    }
    return ok;
  }

  void on_manage_nodes(
    const std::shared_ptr<nav2_msgs::srv::ManageLifecycleNodes::Request> req,
    std::shared_ptr<nav2_msgs::srv::ManageLifecycleNodes::Response> resp)
  {
    bool ok = false;
    switch (req->command) {
      case nav2_msgs::srv::ManageLifecycleNodes::Request::STARTUP:
        ok = run_startup();
        break;
      case nav2_msgs::srv::ManageLifecycleNodes::Request::PAUSE:
        ok = run_pause();
        break;
      case nav2_msgs::srv::ManageLifecycleNodes::Request::RESUME:
        ok = run_resume();
        break;
      case nav2_msgs::srv::ManageLifecycleNodes::Request::RESET:
        ok = run_reset();
        break;
      case nav2_msgs::srv::ManageLifecycleNodes::Request::SHUTDOWN:
        ok = run_shutdown();
        break;
      default:
        ok = false;
        break;
    }

    std_msgs::msg::String ev;
    ev.data = std::string("manage_nodes cmd=") + std::to_string(req->command) + (ok ? " ok" : " fail");
    event_pub_->publish(ev);

    resp->success = ok;
  }

  std::vector<std::string> managed_nodes_;
  std::mutex mu_;
  rclcpp::Service<nav2_msgs::srv::ManageLifecycleNodes>::SharedPtr manage_srv_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr event_pub_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<LifecycleManagerNavigationMini>();
  rclcpp::spin(node);
  rclcpp::shutdown();
  return 0;
}
