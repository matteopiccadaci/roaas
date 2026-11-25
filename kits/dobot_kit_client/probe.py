#!/usr/bin/env python3
"""READ-ONLY robot check: never enables the motors and never sends motion commands.

Queries the dashboard (port 29999) with RobotMode(), GetErrorID(), GetAngle()
and reads a few seconds of port 30004 to check rate and state.

Note: on controller 4.5.x the dashboard only answers when the robot is in TCP mode
(set in DobotStudio Pro); in online mode the queries time out but 30004 still streams.

Usage:  python probe.py --host 192.168.201.1
"""
import argparse
import socket
import statistics
import time

import dobot_proto as dp

READ_ONLY = ("RobotMode()", "GetErrorID()", "GetAngle()")


def ask(sock, cmd):
    sock.sendall(cmd.encode())
    buf = b""
    t0 = time.time()
    while not buf.rstrip().endswith(b";") and time.time() - t0 < 3:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--seconds", type=float, default=2.0)
    args = ap.parse_args()

    print(f"== Dashboard {args.host}:29999 (read-only queries)")
    try:
        with socket.create_connection((args.host, 29999), timeout=5) as s:
            s.settimeout(3)
            for c in READ_ONLY:
                try:
                    print(f"  {c:<14} -> {ask(s, c)}")
                except socket.timeout:
                    print(f"  {c:<14} -> no answer within 3 s (robot not in TCP mode?)")
    except OSError as e:
        print(f"  cannot connect: {e}")

    print(f"\n== Feedback {args.host}:30004 for {args.seconds} s")
    try:
        with socket.create_connection((args.host, 30004), timeout=5) as s:
            pkts, t_end = [], time.time() + args.seconds
            buf = b""
            while time.time() < t_end:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= dp.PACKET_SIZE:
                    pkts.append(buf[:dp.PACKET_SIZE]); buf = buf[dp.PACKET_SIZE:]
    except OSError as e:
        print(f"  cannot connect: {e}")
        return

    good = [dp.unpack(p) for p in pkts if dp.unpack(p, ["TestValue"])["TestValue"] == dp.TEST_VALUE]
    print(f"  packets: {len(pkts)}, valid: {len(good)}")
    if not good:
        return
    ts = [g["TimeStamp"] for g in good]
    dts = [b - a for a, b in zip(ts, ts[1:])]
    last = good[-1]
    mode = last["RobotMode"]
    offset_s = time.time() - last["TimeStamp"] / 1000
    print(f"  median period: {statistics.median(dts) if dts else '-'} ms, max: {max(dts) if dts else '-'} ms")
    print(f"  PC clock minus controller clock: {offset_s:.1f} s")
    print(f"  RobotMode: {mode} ({dp.ROBOT_MODE.get(mode, 'unknown')})")
    print(f"  SpeedScaling: {last['SpeedScaling']}")
    print(f"  CRRobotType: {last['CRRobotType']}")
    print(f"  Joints (deg): {[round(x, 3) for x in last['QActual']]}")
    print(f"  Tool pose (mm, deg): {[round(x, 2) for x in last['ToolVectorActual']]}")
    print(f"  Currents: {[round(x, 3) for x in last['IActual']]}")
    print(f"  Temperatures: {[round(x, 1) for x in last['MotorTemperatures']]}")
    print(f"  Payload setting: {last['Load']} kg, center {[last['CenterX'], last['CenterY'], last['CenterZ']]} mm")
    print(f"  User frame (unverified): {[round(x, 2) for x in last['UserFrame']]}")
    print(f"  Tool frame (unverified): {[round(x, 2) for x in last['ToolFrame']]}")
    print(f"  CollisionState: {last['CollisionState']}   SafetyState (bits, unverified): {last['SafetyState']:08b}")


if __name__ == "__main__":
    main()
