#!/usr/bin/env python3
import random
import threading
import time
from typing import Set

import rclpy
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode, State, TransitionCallbackReturn


class MiniNavigatorServer(LifecycleNode):
    def __init__(self) -> None:
        super().__init__("mini_navigator", namespace="")
        self.declare_parameter("execute_seconds", 3.0)
        self.declare_parameter("deactivate_wait_seconds", 1.0)
        self.declare_parameter("race_bug_mode", True)
        self.declare_parameter("cancel_leak_probability", 0.03)

        self._action_server = None
        self._active = False
        self._active_goal_ids: Set[bytes] = set()
        self._lock = threading.Lock()

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        self._action_server = ActionServer(
            self,
            NavigateToPose,
            "navigate_to_pose",
            execute_callback=self._execute_callback,
            goal_callback=self._goal_callback,
            cancel_callback=self._cancel_callback,
        )
        self.get_logger().info("configured")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        self._active = True
        self.get_logger().info("activated")
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        self._active = False
        deadline = time.monotonic() + float(self.get_parameter("deactivate_wait_seconds").value)

        # This wait reproduces the same class of race: lifecycle transition waits
        # for in-flight action work to drain while cancel is happening concurrently.
        while time.monotonic() < deadline:
            with self._lock:
                if not self._active_goal_ids:
                    self.get_logger().info("deactivated cleanly")
                    return TransitionCallbackReturn.SUCCESS
            time.sleep(0.01)

        with self._lock:
            left = len(self._active_goal_ids)
        self.get_logger().error(f"deactivate timeout, in-flight goals={left}")
        return TransitionCallbackReturn.FAILURE

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        if self._action_server is not None:
            self._action_server.destroy()
            self._action_server = None
        with self._lock:
            self._active_goal_ids.clear()
        self.get_logger().info("cleaned up")
        return TransitionCallbackReturn.SUCCESS

    def _goal_callback(self, goal_request: NavigateToPose.Goal) -> GoalResponse:
        if not self._active:
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _cancel_callback(self, goal_handle) -> CancelResponse:
        # In race_bug_mode we intentionally delay cancellation completion path to
        # amplify overlap with deactivation and expose transition fragility.
        if bool(self.get_parameter("race_bug_mode").value):
            time.sleep(random.uniform(0.01, 0.12))
        return CancelResponse.ACCEPT

    def _execute_callback(self, goal_handle):
        gid = bytes(goal_handle.goal_id.uuid)
        with self._lock:
            self._active_goal_ids.add(gid)

        execute_seconds = float(self.get_parameter("execute_seconds").value)
        end_t = time.monotonic() + execute_seconds

        while time.monotonic() < end_t:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                if bool(self.get_parameter("race_bug_mode").value):
                    leak_p = float(self.get_parameter("cancel_leak_probability").value)
                    if random.random() < leak_p:
                        # Intentionally skip cleanup to emulate a stuck in-flight task.
                        return NavigateToPose.Result()
                with self._lock:
                    self._active_goal_ids.discard(gid)
                return NavigateToPose.Result()
            time.sleep(0.02)

        goal_handle.succeed()
        with self._lock:
            self._active_goal_ids.discard(gid)
        return NavigateToPose.Result()


def main() -> None:
    rclpy.init()
    node = MiniNavigatorServer()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
