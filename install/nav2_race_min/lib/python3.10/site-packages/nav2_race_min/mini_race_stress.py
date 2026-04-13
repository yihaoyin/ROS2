#!/usr/bin/env python3
import argparse
import random
import time

import rclpy
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState, GetState
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node


class MiniRaceStress(Node):
    def __init__(self) -> None:
        super().__init__("mini_race_stress")
        self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.change_state = self.create_client(ChangeState, "mini_navigator/change_state")
        self.get_state = self.create_client(GetState, "mini_navigator/get_state")

    def wait_ready(self, timeout_sec: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            ok = self.change_state.wait_for_service(timeout_sec=0.5)
            ok = ok and self.get_state.wait_for_service(timeout_sec=0.5)
            if ok:
                return True
        return False

    def wait_action(self, timeout_sec: float = 30.0) -> bool:
        return self.client.wait_for_server(timeout_sec=timeout_sec)

    def call_get_state(self, timeout_sec: float = 2.0) -> str:
        fut = self.get_state.call_async(GetState.Request())
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout_sec)
        if not fut.done() or fut.result() is None:
            return "NO_RESPONSE"
        return fut.result().current_state.label

    def do_transition(self, transition_id: int, timeout_sec: float = 2.0) -> bool:
        req = ChangeState.Request()
        req.transition.id = transition_id
        fut = self.change_state.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout_sec)
        if not fut.done() or fut.result() is None:
            return False
        return bool(fut.result().success)

    def ensure_active(self) -> bool:
        state = self.call_get_state()
        if state == "active":
            return True
        if state == "unconfigured":
            if not self.do_transition(Transition.TRANSITION_CONFIGURE, timeout_sec=4.0):
                return False
            state = self.call_get_state()
        if state == "inactive":
            if not self.do_transition(Transition.TRANSITION_ACTIVATE, timeout_sec=4.0):
                return False
            state = self.call_get_state()
        return state == "active"

    def run_once(self, cancel_delay: float) -> dict:
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.orientation.w = 1.0
        goal.pose.pose.position.x = 1.0

        send_fut = self.client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, send_fut, timeout_sec=2.0)
        if not send_fut.done() or send_fut.result() is None:
            return {"ok": False, "reason": "send_timeout"}

        goal_handle = send_fut.result()
        if goal_handle is None or not goal_handle.accepted:
            return {"ok": False, "reason": "rejected"}

        time.sleep(cancel_delay)
        cancel_fut = goal_handle.cancel_goal_async()

        deactive_ok = self.do_transition(Transition.TRANSITION_DEACTIVATE, timeout_sec=1.5)
        state_after_deactive = self.call_get_state(timeout_sec=1.0)

        reactive_ok = False
        if state_after_deactive == "inactive":
            reactive_ok = self.do_transition(Transition.TRANSITION_ACTIVATE, timeout_sec=1.5)
        state_after_reactive = self.call_get_state(timeout_sec=1.0)

        rclpy.spin_until_future_complete(self, cancel_fut, timeout_sec=2.0)
        cancel_ok = cancel_fut.done() and cancel_fut.result() is not None

        bad = (
            (not deactive_ok)
            or (not reactive_ok)
            or (state_after_deactive in ["deactivating", "errorprocessing", "NO_RESPONSE"])
            or (state_after_reactive != "active")
            or (not cancel_ok)
        )

        return {
            "ok": not bad,
            "deactive_ok": deactive_ok,
            "reactive_ok": reactive_ok,
            "cancel_ok": cancel_ok,
            "state_after_deactive": state_after_deactive,
            "state_after_reactive": state_after_reactive,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loops", type=int, default=200)
    parser.add_argument("--cancel-delay", type=float, default=0.03)
    parser.add_argument("--jitter", type=float, default=0.02)
    args = parser.parse_args()

    rclpy.init()
    node = MiniRaceStress()

    if not node.wait_ready():
        print("ERROR: services/actions not ready")
        node.destroy_node()
        rclpy.shutdown()
        raise SystemExit(2)

    if not node.ensure_active():
        print("ERROR: mini_navigator cannot become active")
        node.destroy_node()
        rclpy.shutdown()
        raise SystemExit(3)

    if not node.wait_action(timeout_sec=10.0):
        print("ERROR: action server not available after activation")
        node.destroy_node()
        rclpy.shutdown()
        raise SystemExit(4)

    fail = 0
    for i in range(1, args.loops + 1):
        delay = max(0.0, args.cancel_delay + random.uniform(-args.jitter, args.jitter))
        res = node.run_once(delay)
        if not res["ok"]:
            fail += 1
        print({"iter": i, **res})
        time.sleep(0.01)

    print(f"SUMMARY fail={fail}/{args.loops}")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
