#!/usr/bin/env python3
"""Print a human-readable summary of an episode (no checks, see verify_episode.py for those).

Usage: python summary.py episodes/<folder>
       python summary.py --latest
"""
import argparse
import csv
import gzip
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", nargs="?")
    ap.add_argument("--latest", action="store_true", help="use the most recent folder in ./episodes")
    args = ap.parse_args()
    if args.latest or not args.episode:
        ep = max(Path("episodes").iterdir(), key=lambda p: p.stat().st_mtime)
    else:
        ep = Path(args.episode)

    m = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
    with gzip.open(ep / "telemetry.csv.gz", "rt") as f:
        rows = list(csv.DictReader(f))
    col = lambda name: [float(r[name]) for r in rows]  # noqa: E731

    s = m["stats"]
    print(f"Episode: {ep}")
    print(f"Order: {m['order']['order_id']}  nonce {m['order']['nonce']}  speed {m['order']['speed_factor']}%")
    print(f"Samples: {s['samples']}  duration {s['duration_s']} s  "
          f"period median/max {s['controller_period_ms']['median']}/{s['controller_period_ms']['max']} ms  "
          f"resyncs {s.get('resyncs', 0)}  dropped {s.get('dropped_frames', 0)}  "
          f"jumps {s.get('continuity_violations', '?')}  status {m.get('status', 'completed')}")
    off = s["pc_minus_controller_clock_ms"]["median"] / 1000
    print(f"PC clock minus controller clock: {off:.1f} s ({off / 3600:.2f} h)")
    print(f"Robot modes seen: {sorted({int(r['robot_mode']) for r in rows})}")
    print("\nJoint   min(deg)   max(deg)   max|speed|(deg/s)   max|current|")
    for j in range(1, 7):
        q, qd, i = col(f"q{j}"), col(f"qd{j}"), col(f"i{j}")
        print(f"  J{j}  {min(q):9.3f}  {max(q):9.3f}  {max(map(abs, qd)):18.3f}  {max(map(abs, i)):12.3f}")
    print(f"\nTool z range: {min(col('z')):.1f} .. {max(col('z')):.1f} mm")
    print(f"Files: telemetry.csv.gz {m['files']['telemetry.csv.gz']['bytes'] / 1024:.0f} KB, "
          f"raw.bin {m['raw_recording']['bytes'] / 1024:.0f} KB")


if __name__ == "__main__":
    main()
