"""Tests for smart clip-frame sampling used by the two-stage vision pipeline."""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parent)
sys.path.insert(0, BACKEND)

from fitness_agent.vision_pipeline import (
    normalize_frame_count,
    sample_rep_frames,
    _distribute_indices_around_bottom,
    _bottom_index_from_series,
)


def _make_clip(tmpdir, n):
    for i in range(n):
        with open(os.path.join(tmpdir, f"f{i:03d}.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff")  # tiny fake jpeg header, existence is what matters
    return tmpdir


def test_normalize_snaps_to_grid():
    assert normalize_frame_count(1) == 1
    assert normalize_frame_count(2) == 1
    assert normalize_frame_count(3) == 3
    assert normalize_frame_count(4) == 3
    assert normalize_frame_count(5) == 5
    assert normalize_frame_count(6) == 5
    assert normalize_frame_count(7) == 7
    assert normalize_frame_count(8) == 7
    assert normalize_frame_count(9) == 9
    assert normalize_frame_count(20) == 9
    assert normalize_frame_count(0) == 5
    assert normalize_frame_count(-4) == 5
    assert normalize_frame_count("5") == 5
    assert normalize_frame_count(None) == 5
    assert normalize_frame_count("bad") == 5


def test_distribute_pins_bottom_zero_and_last():
    idxs = _distribute_indices_around_bottom(n=9, k=5, bottom=4)
    assert idxs[0] == 0
    assert idxs[-1] == 8
    assert 4 in idxs
    assert len(idxs) == 5


def test_distribute_asymmetric_bottom():
    # 21 frame clip with bottom at 16 (rep 807 pattern)
    idxs = _distribute_indices_around_bottom(n=21, k=5, bottom=16)
    assert idxs[0] == 0
    assert idxs[-1] == 20
    assert 16 in idxs
    # more slots should land on ascent side because bottom is late
    assert sum(1 for i in idxs if i > 16) >= 1


def test_distribute_seven_and_nine():
    idxs7 = _distribute_indices_around_bottom(n=20, k=7, bottom=10)
    assert 0 in idxs7 and 19 in idxs7 and 10 in idxs7
    assert len(idxs7) == 7
    idxs9 = _distribute_indices_around_bottom(n=20, k=9, bottom=10)
    assert len(idxs9) == 9


def test_distribute_when_k_ge_n():
    idxs = _distribute_indices_around_bottom(n=5, k=9, bottom=2)
    assert idxs == [0, 1, 2, 3, 4]


def test_bottom_index_from_series_maps_correctly():
    # min primary at index 15 of 32 with n=21 -> ~10 in clip index space
    # (interior angle: smaller = deeper, so we make the min at middle)
    series = {"primary": [100 + abs(15 - i) * 5 for i in range(32)]}
    idx = _bottom_index_from_series(21, series)
    assert idx is not None
    assert 8 <= idx <= 12


def test_bottom_index_returns_none_without_series():
    assert _bottom_index_from_series(20, None) is None
    assert _bottom_index_from_series(20, {}) is None
    assert _bottom_index_from_series(20, {"primary": []}) is None
    assert _bottom_index_from_series(20, {"primary": [None, None]}) is None


def test_sample_missing_clip_falls_back_to_single_frame(tmp_path):
    fb = tmp_path / "peak.jpg"
    fb.write_bytes(b"\xff\xd8")
    r = sample_rep_frames(clip_dir=None, angle_series=None, k=5, fallback_frame=str(fb))
    assert r["ok"] is True
    assert r["source"] == "single_frame"
    assert r["frames"] == [str(fb)]
    assert r["actual_k"] == 1


def test_sample_missing_everything_returns_error():
    r = sample_rep_frames(clip_dir=None, angle_series=None, k=5, fallback_frame=None)
    assert r["ok"] is False
    assert r["source"] == "missing"


def test_sample_clip_9_frames_default(tmp_path):
    clip = _make_clip(str(tmp_path), 9)
    series = {"primary": [100 + abs(15 - i) * 5 for i in range(32)]}  # bottom ~ mid
    r = sample_rep_frames(clip, series, k=5)
    assert r["ok"] is True
    assert r["source"] == "clip"
    assert r["actual_k"] == 5
    assert 0 in r["indices"] and 8 in r["indices"]
    assert r["bottom_index"] is not None


def test_sample_clip_long_rep_bottom_pinned(tmp_path):
    clip = _make_clip(str(tmp_path), 21)
    # engineered so bottom is at ~16 of 21
    m = 32
    series = {"primary": [100 - i * 3 if i < 24 else 100 - (m - i - 1) * 3 for i in range(m)]}
    r = sample_rep_frames(clip, series, k=5)
    assert r["actual_k"] == 5
    assert r["bottom_index"] in r["indices"]
    assert r["indices"][0] == 0
    assert r["indices"][-1] == 20


def test_sample_supports_frames_7_and_9(tmp_path):
    clip = _make_clip(str(tmp_path), 15)
    series = {"primary": [100 + abs(15 - i) * 5 for i in range(32)]}
    r7 = sample_rep_frames(clip, series, k=7)
    r9 = sample_rep_frames(clip, series, k=9)
    assert r7["actual_k"] == 7
    assert r9["actual_k"] == 9
    assert 0 in r9["indices"] and 14 in r9["indices"]


def test_sample_k1_uses_fallback_peak(tmp_path):
    clip = _make_clip(str(tmp_path), 9)
    fb = tmp_path / "peak.jpg"
    fb.write_bytes(b"\xff\xd8")
    r = sample_rep_frames(clip, None, k=1, fallback_frame=str(fb))
    assert r["actual_k"] == 1
    # k=1 should use the explicit peak.jpg not a synthesized clip frame
    assert r["frames"] == [str(fb)]


def test_sample_short_clip_returns_all(tmp_path):
    clip = _make_clip(str(tmp_path), 4)
    r = sample_rep_frames(clip, None, k=5)
    assert r["ok"] is True
    assert r["actual_k"] == 4


def test_sample_normalizes_k(tmp_path):
    clip = _make_clip(str(tmp_path), 12)
    r = sample_rep_frames(clip, None, k=6)  # should snap to 5
    assert r["requested_k"] == 5
    assert r["actual_k"] == 5


def test_sample_bottom_within_bounds(tmp_path):
    """Bottom index should never be 0 or n-1 in multi-frame mode."""
    clip = _make_clip(str(tmp_path), 10)
    # engineer series so bottom would map to first frame
    series = {"primary": [10] + [100] * 31}
    r = sample_rep_frames(clip, series, k=5)
    assert r["bottom_index"] >= 1
    assert r["bottom_index"] <= 8
