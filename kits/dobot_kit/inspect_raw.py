#!/usr/bin/env python3
"""Diagnose an episode from its raw.bin: prints every change of RobotMode / CurrentCommandId
with time and J6 position, so you can see how the controller reported the motion.

Usage: python inspect_raw.py episodes/<folder>
       python inspect_raw.py --latest
"""
import argparse
from pathlib import Path

import dobot_proto as dp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", nargs="?")
    ap.add_argument("--latest", action="store_true")
    args = ap.parse_args()
    if args.latest or not args.episode:
        ep = max(Path("episodes").iterdir(), key=lambda p: p.stat().st_mtime)
    else:
        ep = Path(args.episode)
    data = (ep / "raw.bin").read_bytes()
    n = len(data) // dp.PACKET_SIZE
    print(f"{ep}: {n} packets ({n * 0.008:.1f} s)")
    names = ["TimeStamp", "RobotMode", "CurrentCommandId", "QActual", "QDActual", "SpeedScaling"]
    t0, prev = None, None
    j6 = []
    last = None
    for k in range(n):
        d = dp.unpack(data[k * dp.PACKET_SIZE:(k + 1) * dp.PACKET_SIZE], names)
        t0 = t0 or d["TimeStamp"]
        key = (d["RobotMode"], d["CurrentCommandId"])
        j6.append(d["QActual"][5])
        if key != prev:
            print(f"  t={(d['TimeStamp'] - t0) / 1000:7.3f} s  mode={d['RobotMode']:<3} "
                  f"({dp.ROBOT_MODE.get(d['RobotMode'], '?'):<12}) cmd_id={d['CurrentCommandId']:<4} "
                  f"J6={d['QActual'][5]:8.3f}  J6 speed={d['QDActual'][5]:7.3f}  speed%={d['SpeedScaling']}")
            prev = key
        last = d
    print(f"  end: t={(last['TimeStamp'] - t0) / 1000:.3f} s  mode={last['RobotMode']}  J6={last['QActual'][5]:.3f}")
    print(f"  J6 min/max over the run: {min(j6):.3f} / {max(j6):.3f}")


if __name__ == "__main__":
    main()
