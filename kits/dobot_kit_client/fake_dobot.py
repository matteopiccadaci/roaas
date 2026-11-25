#!/usr/bin/env python3
"""Fake Dobot CR3A controller, to test the kit without a robot.

- Feedback port (default 30004): sends one 1440-byte packet every 8 ms.
- Dashboard port (default 29999): accepts the basic V4 commands
  (RequestControl, EnableRobot, DisableRobot, ClearError, SpeedFactor, MovJ(joint={...}),
  RobotMode, GetErrorID, GetAngle, Stop) and replies as "ErrorID,{values},Command();".

By default it behaves like controller firmware 4.5.x, as found on a real CR3A:
RequestControl() does not exist (-10000) and TCP mode is set from DobotStudio Pro.

WARNING: dynamics (currents, tool pose) are made up to give plausible numbers.
The tool pose uses a generic UR3-like kinematic model, NOT the real CR3A kinematics.

Usage:  python fake_dobot.py            (Ctrl+C to stop)
"""
import argparse
import math
import random
import re
import socket
import threading
import time

import dobot_proto as dp

TICK = 0.008


class SimRobot:
    def __init__(self, clock_offset_ms=0, noise=True, firmware="4.5", alarm=False):
        self.lock = threading.Lock()
        self.q = [0.0] * 6           # the real CR3A rests at all-zero joints (arm upright)
        self.qd = [0.0] * 6
        self.qdd = [0.0] * 6
        self.enabled = False
        self.firmware = firmware
        self.tcp_mode = firmware == "4.5"   # on 4.5.x TCP mode is selected in DobotStudio Pro
        self.error = alarm
        self.speed_factor = 100
        self.queue = []          # list of (target, v_ratio, cmd_id)
        self.segment = None      # dict with start, target, T, t0
        self.cmd_id = 0
        self.current_cmd = 0
        self.t_start = time.time()
        self.clock_offset_ms = clock_offset_ms
        self.noise = noise
        self.temps = [30.0 + i * 0.4 for i in range(6)]

    # ---------- commands ----------
    def handle(self, cmd):
        name = cmd.split("(", 1)[0].strip()
        args = cmd[len(name) + 1:-1]
        ok = f"0,{{}},{cmd};"
        with self.lock:
            if name == "RequestControl":
                if self.firmware == "4.5":
                    return f"-10000,{{}},{cmd};"   # command not available on 4.5.x
                if self.enabled:
                    return f"-1,{{}},{cmd};"       # only allowed while disabled
                self.tcp_mode = True
                return ok
            if name == "PowerOn":
                return ok
            if name == "GetErrorID":
                return ("0,{[[22],[],[],[],[],[],[]]},GetErrorID();" if self.error
                        else "0,{[[],[],[],[],[],[],[]]},GetErrorID();")
            if name == "RobotMode":
                return f"0,{{{self.mode()}}},{cmd};"
            if name == "GetAngle":
                return "0,{" + ",".join(f"{x:.4f}" for x in self.q) + f"}},{cmd};"
            if not self.tcp_mode:
                return f"-2,{{}},{cmd};"
            if name == "EnableRobot":
                if self.error:
                    return f"-1,{{}},{cmd};"
                self.enabled = True
                return ok
            if name == "DisableRobot":
                self.enabled = False
                self.queue.clear(); self.segment = None
                self.qd = [0.0] * 6
                return ok
            if name == "ClearError":
                self.error = False
                return ok
            if name == "SpeedFactor":
                v = int(float(args))
                if not 1 <= v <= 100:
                    return f"-3,{{}},{cmd};"
                self.speed_factor = v
                return ok
            if name == "Stop":
                self.queue.clear(); self.segment = None
                self.qd = [0.0] * 6
                return ok
            if name == "MovJ":
                if not self.enabled or self.error:
                    return f"-1,{{}},{cmd};"
                m = re.search(r"joint\s*=\s*\{([^}]*)\}", args)
                if not m:
                    return f"-30001,{{}},{cmd};"  # this fake controller only supports MovJ(joint=...)
                target = [float(x) for x in m.group(1).split(",")]
                if len(target) != 6:
                    return f"-30001,{{}},{cmd};"
                for j, (lo, hi) in enumerate(dp.CR3A_SPEC["joint_range_deg"]):
                    if not lo <= target[j] <= hi:
                        return f"-40000,{{}},{cmd};"  # out of joint range
                vm = re.search(r"\bv\s*=\s*(\d+)", args)
                v_ratio = int(vm.group(1)) if vm else 100
                self.cmd_id += 1
                self.queue.append((target, v_ratio, self.cmd_id))
                return f"0,{{{self.cmd_id}}},{cmd};"
            return f"-10000,{{}},{cmd};"  # command not handled by the simulator

    def mode(self):
        if self.error:
            return dp.MODE_ERROR
        if not self.enabled:
            return dp.MODE_DISABLED
        return dp.MODE_RUNNING if (self.segment or self.queue) else dp.MODE_IDLE

    # ---------- simulation ----------
    def step(self, now):
        with self.lock:
            if self.segment is None and self.queue:
                target, v_ratio, cid = self.queue.pop(0)
                start = list(self.q)
                scale = self.speed_factor / 100 * v_ratio / 100
                T = 0.2
                for j in range(6):
                    vmax = dp.CR3A_SPEC["joint_speed_max_deg_s"][j] * scale
                    T = max(T, 1.875 * abs(target[j] - start[j]) / vmax)  # peak of the min-jerk profile
                self.segment = {"start": start, "target": target, "T": T, "t0": now}
                self.current_cmd = cid
            if self.segment:
                s = self.segment
                tau = min(1.0, (now - s["t0"]) / s["T"])
                pos = 10 * tau**3 - 15 * tau**4 + 6 * tau**5
                vel = (30 * tau**2 - 60 * tau**3 + 30 * tau**4) / s["T"]
                acc = (60 * tau - 180 * tau**2 + 120 * tau**3) / s["T"] ** 2
                for j in range(6):
                    d = s["target"][j] - s["start"][j]
                    self.q[j] = s["start"][j] + d * pos
                    self.qd[j] = d * vel
                    self.qdd[j] = d * acc
                if tau >= 1.0:
                    self.segment = None
                    self.qd = [0.0] * 6
                    self.qdd = [0.0] * 6
            return self.packet(now)

    def packet(self, now):
        n = (lambda s: random.gauss(0, s)) if self.noise else (lambda s: 0.0)
        q_meas = [x + n(0.002) for x in self.q]
        qd_meas = [x + n(0.01) for x in self.qd]
        r = [math.radians(x) for x in self.q]
        grav = [0.0, 1.6 * math.sin(r[1]) + 0.6 * math.sin(r[1] + r[2]), 0.6 * math.sin(r[1] + r[2]),
                0.05 * math.sin(r[1] + r[2] + r[3]), 0.02, 0.0]
        inertia = [0.004, 0.006, 0.003, 0.0008, 0.0006, 0.0003]
        friction = [0.0015, 0.0015, 0.0012, 0.0006, 0.0006, 0.0004]
        current = [0.0] * 6
        if self.enabled:
            current = [grav[j] + inertia[j] * self.qdd[j] + friction[j] * self.qd[j] + n(0.01) for j in range(6)]
        for j in range(6):
            self.temps[j] += (abs(current[j]) * 0.00002) - (self.temps[j] - 30) * 0.00001
        pose = fk_generic(self.q)
        return dp.pack({
            "RobotMode": self.mode(),
            "TimeStamp": int(now * 1000) + self.clock_offset_ms,
            "RunTime": int((now - self.t_start) * 1000),
            "SpeedScaling": float(self.speed_factor),
            "VRobot": 48.0 + n(0.05),
            "IRobot": sum(abs(c) for c in current) * 0.3 + 0.4,
            "QTarget": self.q, "QDTarget": self.qd, "QDDTarget": self.qdd,
            "QActual": q_meas, "QDActual": qd_meas, "IActual": current,
            "ToolVectorActual": pose, "ToolVectorTarget": pose,
            "MotorTemperatures": self.temps,
            "JointModes": [8.0] * 6,
            "VActual": [48.0] * 6,
            "CRRobotType": 113,  # value read on a real CR3A
            "CurrentCommandId": self.current_cmd,
            "Load": 0.0, "CenterX": 0.0, "CenterY": 0.0, "CenterZ": 0.0,
            "UserFrame": [0.0] * 6, "ToolFrame": [0.0] * 6,
            "SafetyState": 0,
        })


def fk_generic(q_deg):
    """GENERIC forward kinematics (UR3-like DH), only to produce a plausible pose."""
    d = [0.1519, 0, 0, 0.11235, 0.08535, 0.0819]
    a = [0, -0.24365, -0.21325, 0, 0, 0]
    alpha = [math.pi / 2, 0, 0, math.pi / 2, -math.pi / 2, 0]
    T = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    for i in range(6):
        th = math.radians(q_deg[i])
        ct, st, ca, sa = math.cos(th), math.sin(th), math.cos(alpha[i]), math.sin(alpha[i])
        A = [[ct, -st * ca, st * sa, a[i] * ct],
             [st, ct * ca, -ct * sa, a[i] * st],
             [0, sa, ca, d[i]],
             [0, 0, 0, 1]]
        T = [[sum(T[r][k] * A[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
    rx = math.degrees(math.atan2(T[2][1], T[2][2]))
    ry = math.degrees(math.atan2(-T[2][0], math.hypot(T[2][1], T[2][2])))
    rz = math.degrees(math.atan2(T[1][0], T[0][0]))
    return [T[0][3] * 1000, T[1][3] * 1000, T[2][3] * 1000, rx, ry, rz]


def serve_feedback(robot, port, stop, glitch_when_running=False):
    clients, clock = [], threading.Lock()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(8); srv.settimeout(0.2)

    def acceptor():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with clock:
                    clients.append(c)
            except socket.timeout:
                pass
    threading.Thread(target=acceptor, daemon=True).start()

    nxt = time.time()
    while not stop.is_set():
        nxt += TICK
        pkt = robot.step(nxt)  # use the scheduled tick time, like a controller with its own 8 ms clock
        if glitch_when_running and robot.mode() == dp.MODE_RUNNING and robot.segment \
                and time.time() - robot.segment["t0"] > 0.5:
            pkt = pkt[:360]   # one truncated packet (first 360 bytes only), as seen on the real CR3A
            glitch_when_running = False
        with clock:
            for c in list(clients):
                try:
                    c.sendall(pkt)
                except OSError:
                    clients.remove(c)
        delay = nxt - time.time()
        if delay > 0:
            time.sleep(delay)
        else:
            nxt = time.time()  # running late: restart without accumulating


def serve_dashboard(robot, port, stop):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(4); srv.settimeout(0.2)
    pattern = re.compile(r"[A-Za-z]+\((?:[^()]*)\)")

    def client(c):
        buf = ""
        with c:
            while not stop.is_set():
                try:
                    data = c.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                buf += data.decode("utf-8", "replace")
                while True:
                    m = pattern.search(buf)
                    if not m:
                        break
                    buf = buf[m.end():]
                    c.sendall(robot.handle(m.group(0)).encode())

    while not stop.is_set():
        try:
            c, _ = srv.accept()
            threading.Thread(target=client, args=(c,), daemon=True).start()
        except socket.timeout:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feedback-port", type=int, default=30004)
    ap.add_argument("--dashboard-port", type=int, default=29999)
    ap.add_argument("--firmware", choices=["4.5", "4.6"], default="4.5",
                    help="4.5 = no RequestControl(), as on the tested CR3A")
    ap.add_argument("--alarm", action="store_true", help="start with an active alarm")
    ap.add_argument("--glitch", action="store_true",
                    help="send one truncated packet during the motion (tests resync)")
    ap.add_argument("--clock-offset-ms", type=int, default=0,
                    help="shift the fake controller clock vs the PC (to test the clock check)")
    args = ap.parse_args()

    robot = SimRobot(clock_offset_ms=args.clock_offset_ms, firmware=args.firmware, alarm=args.alarm)
    stop = threading.Event()
    threading.Thread(target=serve_dashboard, args=(robot, args.dashboard_port, stop), daemon=True).start()
    print(f"Fake CR3A running: dashboard 127.0.0.1:{args.dashboard_port}, "
          f"feedback 127.0.0.1:{args.feedback_port} (Ctrl+C to stop)")
    try:
        serve_feedback(robot, args.feedback_port, stop, glitch_when_running=args.glitch)
    except KeyboardInterrupt:
        stop.set()
        print("\nStopped.")


if __name__ == "__main__":
    main()
