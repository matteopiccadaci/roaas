"""Manual settlement: today's flow. An order file arrives by email, the operator runs it, a zip goes back.

The zip keeps the kit's layout (one folder: manifest.json, manifest.sig and every file the manifest lists),
plus preview.json, so the buyer's own verify_episode.py from the kit keeps working (decision D10).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path


def package_zip(episode_dir: Path, deliveries_dir: Path) -> Path:
    ep = Path(episode_dir)
    manifest = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise ValueError(f"episode status is {manifest.get('status')}: not packaged")
    Path(deliveries_dir).mkdir(parents=True, exist_ok=True)
    zpath = Path(deliveries_dir) / f"{ep.name}.zip"
    names = ["manifest.json", "manifest.sig", *manifest["files"].keys()]
    if (ep / "preview.json").exists():
        names.append("preview.json")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(ep / name, f"{ep.name}/{name}")
    return zpath
