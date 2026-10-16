"""Recompute node_storage_area_m2 in the BUILT pilot artefact using IS 4111 (Part 1) - 1986 depth bands.

WHY A MIGRATION RATHER THAN A REBUILD
-------------------------------------
`floodnet/data/mcgm.py` now derives manhole plan area from each node's own depth via
`storage_area_from_depth()` instead of a blanket 1.5 m2. But `data/processed/pilot/network.json` was built
before that change and carries the old constant, and a full `python -m floodnet.data.build_pilot` would need
network access and would also rebuild terrain/OSM layers that this change has nothing to do with.

This script therefore recomputes ONLY `node_storage_area_m2`, offline, from `node_ground` and `node_invert`
that are ALREADY in the artefact (both REAL MCGM values), calling the very same
`mcgm.storage_area_from_depth()` the builder now uses -- so a future full rebuild produces an identical
field. It also refreshes the `storage_area` provenance entry to the IS 4111 citation.

Nothing else in the artefact is touched. A timestamped backup is written first.

Run:  cd backend && .venv\\Scripts\\python.exe scripts/migrate_storage_area_is4111.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodnet.config import DATA_PROCESSED
from floodnet.data.mcgm import storage_area_from_depth, IS4111_RECT_BANDS_M2
from floodnet.data.mcgm import build_network  # noqa: F401  (import proves the module still loads)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report the change without writing")
    a = ap.parse_args()

    path = Path(DATA_PROCESSED) / "network.json"
    if not path.exists():
        print(f"ERROR: {path} not found; build the pilot first")
        return 1
    js = json.loads(path.read_text(encoding="utf-8"))

    ground = np.asarray(js["node_ground"], dtype=float)
    invert = np.asarray(js["node_invert"], dtype=float)
    depth = ground - invert
    old = np.asarray(js["node_storage_area_m2"], dtype=float)
    new = storage_area_from_depth(depth)

    print(f"nodes: {len(old)}")
    print(f"depth (m): min {depth.min():.2f}  median {np.median(depth):.2f}  max {depth.max():.2f}")
    print(f"OLD storage area distinct values: {Counter(np.round(old, 3).tolist()).most_common()}")
    print(f"NEW storage area distinct values: {Counter(np.round(new.tolist(), 3)).most_common()}")
    print("IS 4111 bands applied:")
    new64 = np.asarray(new, dtype=float)   # float32 -> float64; compare with isclose, never ==
    for _upper, area, label in IS4111_RECT_BANDS_M2:
        n = int(np.isclose(new64, area, rtol=0, atol=1e-6).sum())
        print(f"  {area:>5.2f} m2  n={n:>5}  {label}")
    tot_old, tot_new = float(old.sum()), float(np.asarray(new, dtype=float).sum())
    print(f"total plan area: {tot_old:.1f} m2 -> {tot_new:.1f} m2  ({100*(tot_new-tot_old)/tot_old:+.1f}%)")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    backup = path.with_suffix(".json.pre_is4111_backup")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"backup written: {backup.name}")

    js["node_storage_area_m2"] = [float(v) for v in new]
    prov = js.get("provenance", {})
    prov["storage_area"] = {
        "tag": "ESTIMATED",
        "source": "IS 4111 (Part 1) - 1986, Manholes, cl. 3.3.2 / 3.3.3 (rectangular series)",
        "note": ("Manhole plan area banded by the node's own depth (node_ground - node_invert, both REAL "
                 "MCGM values) using the Indian Standard's own depth bands: <0.90 m -> 900x800 mm = 0.72 m2; "
                 "0.90-2.5 m -> 1200x900 mm = 1.08 m2; >=2.5 m -> 1400x900 mm = 1.26 m2. Supersedes the "
                 "previous blanket 1.5 m2 assumption. IS 4111 cl. 3.3.4 permits CIRCULAR chambers instead "
                 "(0.64/1.13/1.77/2.54 m2); MCGM data has no chamber-size or shape field, so the rectangular "
                 "series is used because it alone covers the shallowest band. STILL ESTIMATED: the dimensions "
                 "are real and citable, but 'these chambers conform to IS 4111' is an assumption about this "
                 "network, not a measurement of it. Applied by scripts/migrate_storage_area_is4111.py.")}
    js["provenance"] = prov
    path.write_text(json.dumps(js), encoding="utf-8")
    print(f"WROTE {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
