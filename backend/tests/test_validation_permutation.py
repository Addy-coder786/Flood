"""Spatial permutation test (floodnet.validation.flooding_spots.permutation_test) — mechanics only.

These tests assert that the test *machinery* is correct and reproducible:
  * the same seed gives byte-identical output, and a different seed does not;
  * p-values are real probabilities in [0, 1], bounded below by the Monte-Carlo floor 1/(n+1);
  * relocated footprints preserve shape and exact cell count, and stay inside the grid;
  * the FFT placement-count helper agrees with a brute-force reference.

They deliberately assert NOTHING about whether the model beats chance. That outcome is a measurement, and
baking today's value into the suite would turn a result into a requirement. The measured p-values live in
`docs/VALIDATION.md` §2.F/G, reproducible with the command recorded there.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from floodnet.data.fixtures import synthetic_pilot

fs = pytest.importorskip("floodnet.validation.flooding_spots")

N_PERM = 64          # small: these tests check mechanics, not the published numbers


def _lonlat(grid, i, j):
    from pyproj import Transformer
    inv = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lon, lat = inv.transform(grid.x0 + (i + 0.5) * grid.res, grid.y0 + (j + 0.5) * grid.res)
    return [float(lon), float(lat)]


@pytest.fixture(scope="module")
def synthetic_run():
    pytest.importorskip("floodnet.terrain.surface")
    pytest.importorskip("floodnet.drainage.hydraulics")
    pilot = synthetic_pilot()
    res = fs.run_pilot_scenario(pilot, "cloudburst", horizon_min=30)
    g = pilot["terrain"].grid
    z = pilot["terrain"].z
    jmin, imin = np.unravel_index(np.argmin(z), z.shape)
    spots = [
        # a polygon footprint over the bowl minimum, and a small radius footprint away from it
        {"name": "bowl", "active": True,
         "polygon_lonlat": [_lonlat(g, int(imin) + di, int(jmin) + dj)
                            for di, dj in ((-3, -3), (3, -3), (3, 3), (-3, 3))],
         "lonlat_centroid": _lonlat(g, int(imin), int(jmin))},
        {"name": "corner", "active": True, "lonlat_centroid": _lonlat(g, 6, 6)},
        {"name": "ignored (Delete)", "active": False, "lonlat_centroid": _lonlat(g, 8, 8)},
    ]
    return pilot, res, spots


# ------------------------------------------------------------------ determinism
def test_same_seed_is_bitwise_reproducible(synthetic_run):
    pilot, res, spots = synthetic_run
    a = fs.permutation_test(pilot, res, spots=spots, n_perm=N_PERM, seed=4242)
    b = fs.permutation_test(pilot, res, spots=spots, n_perm=N_PERM, seed=4242)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_seed_actually_drives_the_sample(synthetic_run):
    """A different seed must draw different placements, while the seed-independent exact null probability
    (computed over *all* valid placements) must be unchanged."""
    pilot, res, spots = synthetic_run
    a = fs.permutation_test(pilot, res, spots=spots, n_perm=512, seed=1)
    b = fs.permutation_test(pilot, res, spots=spots, n_perm=512, seed=2)
    exact_a = [s["null_detect_prob_exact"] for s in a["spots"]]
    exact_b = [s["null_detect_prob_exact"] for s in b["spots"]]
    assert exact_a == exact_b, "exact null probability must not depend on the seed"
    sampled_a = [s["null_detect_prob_sampled"] for s in a["spots"]]
    sampled_b = [s["null_detect_prob_sampled"] for s in b["spots"]]
    assert sampled_a != sampled_b or a["sum_max_depth_cm"]["null_mean"] != b["sum_max_depth_cm"]["null_mean"], \
        "two different seeds produced an identical sample — the RNG is not wired in"


def test_published_seed_and_permutation_count_are_pinned():
    """docs/VALIDATION.md quotes these; if they change the published p-values stop being reproducible."""
    assert fs.PERM_SEED == 20260910
    assert fs.PERM_N == 2000


# ------------------------------------------------------------------ p-value is a probability
@pytest.mark.parametrize("seed", [7, 20260910])
def test_p_values_are_probabilities(synthetic_run, seed):
    pilot, res, spots = synthetic_run
    r = fs.permutation_test(pilot, res, spots=spots, n_perm=N_PERM, seed=seed)
    floor = 1.0 / (N_PERM + 1)
    for key in ("detected", "sum_max_depth_cm"):
        p = r[key]["p_value"]
        assert isinstance(p, float)
        assert 0.0 <= p <= 1.0, (key, p)
        assert p >= floor - 1e-12, f"{key}: Monte-Carlo p cannot fall below 1/(n+1)"
    assert r["n_perm"] == N_PERM and r["seed"] == seed
    assert r["n_active_spots"] == 2, "inactive spots must be excluded from the test"
    assert 0 <= r["detected"]["observed"] <= r["n_active_spots"]
    assert 0.0 <= r["detected"]["null_mean"] <= r["n_active_spots"]
    for s in r["spots"]:
        assert 0.0 <= s["null_detect_prob_exact"] <= 1.0
        assert s["n_valid_placements"] >= 1


# ------------------------------------------------------------------ the null preserves shape and size
def test_kernel_preserves_shape_and_cell_count():
    m = np.zeros((20, 25), dtype=bool)
    cells = [(4, 5), (4, 6), (5, 5), (7, 9), (6, 7)]
    for j, i in cells:
        m[j, i] = True
    k = fs._kernel_of(m)
    assert k.sum() == len(cells) == m.sum(), "cell count must be preserved exactly"
    assert k.shape == (7 - 4 + 1, 9 - 5 + 1)
    # the kernel is the footprint translated to the bbox origin: same relative geometry
    got = {(j, i) for j, i in zip(*np.nonzero(k))}
    want = {(j - 4, i - 5) for j, i in cells}
    assert got == want


def test_placement_counts_match_brute_force():
    rng = np.random.default_rng(0)
    field = rng.random((30, 34)) > 0.4
    kernel = rng.random((5, 7)) > 0.5
    kernel[0, 0] = True
    got = fs._placement_counts(field, kernel)
    h, w = kernel.shape
    want = np.array([[int((field[j:j + h, i:i + w] & kernel).sum())
                      for i in range(field.shape[1] - w + 1)]
                     for j in range(field.shape[0] - h + 1)])
    assert got.shape == want.shape
    assert np.array_equal(got, want), "FFT correlation must reproduce exact integer counts"


def test_placement_counts_empty_when_kernel_larger_than_field():
    assert fs._placement_counts(np.ones((4, 4), bool), np.ones((5, 5), bool)).size == 0


def test_all_sampled_relocations_fit_inside_the_grid(synthetic_run):
    """Re-derive the placement set the test draws from and check every member is in-domain and valid."""
    pilot, res, spots = synthetic_run
    grid = pilot["terrain"].grid
    flowable = ~np.asarray(pilot["terrain"].building, dtype=bool)
    for s in spots:
        if not s.get("active", True):
            continue
        mask, _ = fs.footprint_mask(grid, s)
        kernel = fs._kernel_of(mask)
        h, w = kernel.shape
        counts = fs._placement_counts(flowable, kernel)
        assert counts.shape == (grid.ny - h + 1, grid.nx - w + 1), \
            "placement grid must exclude every position that would run off the domain"
        ok = counts >= max(int((mask & flowable).sum()), 1)
        assert ok.any(), s["name"]
        # every valid placement covers at least the observed flowable area, with the same cell count
        assert counts[ok].min() >= 1
        assert int(kernel.sum()) == int(mask.sum())


def test_rendered_markdown_states_the_direction_of_the_p_value(synthetic_run):
    pilot, res, spots = synthetic_run
    r = fs.permutation_test(pilot, res, spots=spots, n_perm=N_PERM, seed=3)
    md = fs.render_permutation_markdown([r])
    assert "did not beat chance" in md
    assert str(r["seed"]) in md and str(r["n_perm"]) in md
