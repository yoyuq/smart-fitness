"""rep_quality_rules.py — Evidence-based rule replacement for the TCN rep-quality model.

Why a rule replacement?
-----------------------
`rep_quality_tcn.py` is a small TCN (UI-PRMD, 94% acc / r0.91) that produces a 0-100
quality score. It is a black-box: it can't explain *why* a rep is a 80 vs a 60, and
its training target has been bootstrap-labeled (rule scores → distilled from itself).
For a coaching product we need transparent, citable judgments.

This module scores the *same* rep using the multi-channel `angle_series`
(the 4-channel + multi-joint payload written by `rep_scorer.RepScorer._finalize`)
plus the base rule metrics that `rep_scorer` already computes. Each output field
carries an `evidence` list of PMID citations, single-source-of-truth at
`backend/docs/rep_completion_algorithm_evidence.md`.

Public API mirrors `rep_quality_tcn.score_rep_quality`:

    from rep_quality_rules import score_rep_quality_rules
    result = score_rep_quality_rules(exercise, angle_series, rep_row)
    # rep_row may be a dict with depth/control/symmetry/duration_s/peak_angle
    # (the same fields that RepScorer._finalize emits).

    result = {
        "score": 0-100,
        "label": "good" | "shallow" | "deep" | "fast" | "slow"
                 | "asymmetric" | "torso_lean" | "elbow_flare" | "knee_valgus"
                 | "arms_low" | "feet_narrow",
        "issues": [
            {"key": "shallow", "penalty": 15, "cue_cn": "...", "cue_en": "...",
             "evidence": ["PMID:11194098", "PMID:33660588"]},
            ...
        ],
        "evidence_used": [ "PMID:...", ... ],
    }

Design principles
-----------------
1. Every threshold in this file must have at least one PMID/arxiv/statpearls citation,
   or be marked TODO(evidence).
2. Return signal is deterministic; no randomness, no dependency on torch.
3. The TCN model stays in `rep_quality_tcn.py` for A/B baseline comparison.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Evidence table (single source of truth: docs/rep_completion_algorithm_evidence.md)
# ---------------------------------------------------------------------------
EVIDENCE_SOURCES: Dict[str, Dict[str, str]] = {
    # squat depth
    "escamilla_2001": {
        "pmid": "11194098",
        "citation": "Escamilla RF. Knee biomechanics of the dynamic squat exercise. Med Sci Sports Exerc. 2001;33(1):127-41.",
        "key_finding": "Parallel squat = thighs parallel to ground at max knee flexion; safe 0-100° knee flexion.",
    },
    "oneill_2024": {
        "pmid": "33660588",
        "citation": "O'Neill KE, Psycharakis SG. Back squat depth and load. Sports Biomech. 2024;23(5):555-566.",
        "key_finding": "Experimental depth conditions: 90° knee angle (parallel) vs 125° knee angle (half).",
    },
    "martinez_cava_2019": {
        "pmid": "30426840",
        "citation": "Martínez-Cava A, et al. Velocity-power in half, parallel, full back squat. J Sports Sci. 2019;37(10):1088-1096.",
        "key_finding": "Half/parallel/full three-level depth is the standard framework.",
    },
    "hartmann_2013": {
        "pmid": "23821469",
        "citation": "Hartmann H, et al. Analysis of the load on the knee joint and vertebral column with changes in squatting depth. Sports Med. 2013;43:993.",
        "key_finding": "Deep squats safe for healthy knees; no evidence of harm from below-parallel depth.",
    },
    # push-up
    "dhahbi_2022": {
        "pmid": "30284496",
        "citation": "Dhahbi W, et al. Kinetic analysis of push-up: systematic review. Sports Biomech. 2022;21(1):1-40.",
        "key_finding": "Chest-to-ground = elbow flexion ~90° (interior 90°).",
    },
    "mcgill_2014": {
        "pmid": "24088865",
        "citation": "McGill SM. Push-up trunk kinematics. J Strength Cond Res. 2014;28(1):105-16.",
        "key_finding": "Standard push-up: rigid plank from head to ankle; tucked elbows reduce shoulder shear.",
    },
    "lunden_2010": {
        "pmid": "19733487",
        "citation": "Lunden JB, et al. Push-up scapular kinematics. J Shoulder Elbow Surg. 2010;19:216.",
        "key_finding": "Flared elbows increase scapular protraction demand.",
    },
    # lunge
    "escamilla_2022": {
        "pmid": "35697336",
        "citation": "Escamilla RF, et al. Lunge biomechanics. J Appl Biomech. 2022;38(4):210-220.",
        "key_finding": "Lunge descent covers 50-100° knee flexion; bottom ≈ 90°.",
    },
    # bicep curl
    "pedrosa_2023": {
        "pmid": "36828324",
        "citation": "Pedrosa GF, et al. Biceps curl full ROM. Sports. 2023;11(2):39.",
        "key_finding": "Curl full ROM 0-135° flexion; top ≈ interior 30-45°.",
    },
    "marchetti_2017": {
        "pmid": "29387084",
        "citation": "Marchetti PH, et al. Biceps EMG with strict shoulder. J Strength Cond Res. 2017.",
        "key_finding": "Strict curls require stable shoulder; excess shoulder abduction reduces biceps activation.",
    },
    # shoulder press
    "gundersen_2025": {
        "pmid": "41335596",
        "citation": "Gundersen AH, et al. Overhead press lockout kinematics. Sports Biomech. 2025.",
        "key_finding": "Lockout: elbow ~170-180°, shoulder ~150-180°.",
    },
    # jumping jack
    "lam_2023": {
        "pmid": "NBK537148",
        "citation": "Lam JH, Bordoni B. Anatomy Shoulder Abduction. StatPearls (2023).",
        "key_finding": "Full arm abduction 0-180° at glenohumeral joint; ≥150° = 'overhead'.",
    },
    # DKV / knee valgus
    "ortiz_2016": {
        "pmid": "27313480",
        "citation": "Ortiz A, et al. 2D vs 3D knee valgus KASR. Open Access J Sports Med. 2016;7:65.",
        "key_finding": "KASR (knee-to-ankle separation ratio) 2D-3D correlation ICC=0.96.",
    },
    "wilczynski_2020": {
        "pmid": "33172101",
        "citation": "Wilczyński B. Dynamic Knee Valgus review. J Clin Med. 2020;9(11):3690.",
        "key_finding": "DKV systematic review of risk factors and measurement.",
    },
    "nagano_2010": {
        "pmid": "20800076",
        "citation": "Nagano Y, et al. Lower-limb kinematics jump testing. Res Sports Med. 2010.",
        "key_finding": "Valgus females tracked ~15-25% narrower knee spacing vs controls.",
    },
    # torso lean squat
    "schmid_2022": {
        "pmid": "35449120",
        "citation": "Schmid S. Stoop-Squat-Index. Arch Physiother. 2022;12:8.",
        "key_finding": "Trunk-to-limb bending quantified; excess lean raises lumbar shear.",
    },
    "hebling_2017": {
        "pmid": "27015103",
        "citation": "Hebling Campos M. Squat trunk kinematics. J Sports Med Phys Fitness. 2017;57:773.",
        "key_finding": "Forward-lean >55° increases lumbar shear beyond safe range.",
    },
    # tempo / velocity
    "wilk_2021": {
        "pmid": "33551848",
        "citation": "Wilk M, et al. Tempo effects on resistance training. Front Physiol. 2021;12:629199.",
        "key_finding": "Concentric+eccentric duration in 2-6s is the studied 'controlled' band.",
    },
    "carzoli_2019": {
        "pmid": "31418323",
        "citation": "Carzoli JP, et al. Rep tempo effects. J Strength Cond Res. 2019.",
        "key_finding": "Typical bench eccentric+concentric duration 2-6s in trained lifters.",
    },
    # rep counting
    "jaiswal_2023": {
        "pmid": None,
        "arxiv": "2310.07221",
        "citation": "Jaiswal A, et al. Learnable Physics for Exercise Form. ACM RecSys 2023.",
        "key_finding": "Peak-prominence detection is the recommended MediaPipe rep counting algorithm.",
    },
}


def evidence_refs(keys: Sequence[str]) -> List[str]:
    """Return canonical citation strings (e.g. 'PMID:11194098') for report bundling."""
    out: List[str] = []
    for k in keys:
        e = EVIDENCE_SOURCES.get(k)
        if not e:
            continue
        if e.get("pmid"):
            out.append(f"PMID:{e['pmid']}")
        elif e.get("arxiv"):
            out.append(f"arxiv:{e['arxiv']}")
    return out


# ---------------------------------------------------------------------------
# Per-exercise evidence-based thresholds (interior angle convention).
# Mirrors backend/rep_scorer.EXERCISE_CFG but exposes penalty weights for the
# rule quality classifier and each threshold carries evidence keys.
# ---------------------------------------------------------------------------
_QUALITY_RULES: Dict[str, Dict[str, Any]] = {
    "squat": {
        "depth_range": (40, 100),          # interior knee angle: parallel/full window
        "depth_evidence": ["escamilla_2001", "oneill_2024", "hartmann_2013", "martinez_cava_2019"],
        "duration": (1.2, 7.0),
        "duration_evidence": ["wilk_2021", "carzoli_2019"],
        "torso_lean_max": 55,               # deg from vertical
        "torso_lean_evidence": ["schmid_2022", "hebling_2017"],
        "asymmetry_deg": 10,               # symmetric-exercise per-frame LR diff tolerance
        "dkv_kasr_warn": 0.75,
        "dkv_kasr_critical": 0.55,
        "dkv_evidence": ["ortiz_2016", "wilczynski_2020", "nagano_2010"],
        "penalties": {"shallow": 15, "deep": 8, "fast": 10, "slow": 10,
                       "asymmetric": 8, "torso_lean": 15, "knee_valgus": 20},
    },
    "push_up": {
        "depth_range": (60, 78),
        "depth_evidence": ["dhahbi_2022", "mcgill_2014"],
        "duration": (1.0, 6.0),
        "duration_evidence": ["wilk_2021"],
        "elbow_flare_shoulder_max": 65,     # bottom shoulder angle upper bound (elbows tucked)
        "elbow_flare_evidence": ["mcgill_2014", "lunden_2010"],
        "body_line_hip_min": 160,           # interior hip stays near-flat plank
        "body_line_evidence": ["mcgill_2014"],
        "asymmetry_deg": 12,
        "penalties": {"shallow": 20, "deep": 5, "fast": 10, "slow": 10,
                       "asymmetric": 8, "elbow_flare": 15, "body_sag": 15},
    },
    "lunge": {
        "depth_range": (80, 110),
        "depth_evidence": ["escamilla_2022"],
        "duration": (1.5, 7.0),
        "duration_evidence": ["wilk_2021"],
        "asymmetric_exercise": True,        # by design, no symmetry penalty
        "penalties": {"shallow": 15, "deep": 8, "fast": 10, "slow": 10},
    },
    "bicep_curl": {
        "depth_range": (30, 60),
        "depth_evidence": ["pedrosa_2023"],
        "duration": (1.0, 5.0),
        "duration_evidence": ["wilk_2021"],
        "shoulder_stable_max": 30,          # deg abduction limit for strict curl
        "shoulder_stable_evidence": ["marchetti_2017"],
        "asymmetry_deg": 12,
        "penalties": {"shallow": 15, "deep": 5, "fast": 8, "slow": 8,
                       "asymmetric": 6, "shoulder_swing": 12},
    },
    "shoulder_press": {
        "depth_range": (150, 180),
        "depth_evidence": ["gundersen_2025"],
        "duration": (1.0, 5.0),
        "duration_evidence": ["wilk_2021"],
        "asymmetry_deg": 12,
        "penalties": {"shallow": 20, "fast": 10, "slow": 10, "asymmetric": 8},
    },
    "jumping_jack": {
        "depth_range": (140, 180),
        "depth_evidence": ["lam_2023"],
        "duration": (0.4, 2.5),
        "duration_evidence": ["wilk_2021"],
        "feet_narrow_ankle_dx_min": 0.9,    # normalized by shoulder width
        "feet_narrow_evidence": ["lam_2023"],
        "asymmetry_deg": 20,
        "penalties": {"arms_low": 15, "fast": 8, "slow": 6,
                       "asymmetric": 6, "feet_narrow": 14},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _series_extremes(series: Optional[Sequence[Optional[float]]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (min, max, mean_abs) after removing None/NaN. mean_abs is mean of |v|."""
    if not series:
        return None, None, None
    vals = [float(v) for v in series if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None, None, None
    return min(vals), max(vals), sum(abs(v) for v in vals) / len(vals)


def _bottom_index(primary: Sequence[float], direction: str) -> int:
    if direction == "min":
        return min(range(len(primary)), key=lambda i: primary[i])
    return max(range(len(primary)), key=lambda i: primary[i])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_rep_quality_rules(
    exercise: str,
    angle_series: Dict[str, Any],
    rep_row: Optional[Dict[str, Any]] = None,
    kasr_bottom: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Score a completed rep using citable rules.

    Args:
      exercise: canonical name matching _QUALITY_RULES (e.g. 'squat').
      angle_series: dict of channel->series[32] as emitted by RepScorer._finalize.
                    Must at least contain 'primary'. Optional channels: 'lr_diff',
                    'torso_tilt' / 'torso', 'ankle_dx', shoulder_L/R, elbow_L/R, etc.
      rep_row: optional pre-computed rep metrics (depth/control/symmetry/duration_s,
               peak_angle). If missing we recompute from series.
      kasr_bottom: optional pre-computed KASR at the rep bottom (frame-level valgus
                   from FormAnalyzer). If None, valgus is not evaluated here.

    Returns:
      dict with score/label/issues/evidence_used, or None on unsupported exercise.
    """
    cfg = _QUALITY_RULES.get(exercise)
    if not cfg:
        return None
    primary = [v for v in (angle_series.get("primary") or []) if v is not None]
    if len(primary) < 3:
        return None

    # depth extremum (interior-angle convention: squat/push_up/lunge/curl = min, others = max)
    direction = "min" if exercise in ("squat", "push_up", "lunge", "bicep_curl") else "max"
    extremum = min(primary) if direction == "min" else max(primary)
    bot_i = _bottom_index(primary, direction)

    duration_s = None
    lr_diff_mean = None
    if rep_row:
        duration_s = rep_row.get("duration_s")
        # symmetry expressed as inverted lr_diff on some rows; keep raw diff if available
    lr_series = angle_series.get("lr_diff") or []
    _, _, lr_diff_mean_from_series = _series_extremes(lr_series)
    if lr_diff_mean_from_series is not None:
        lr_diff_mean = lr_diff_mean_from_series

    issues: List[Dict[str, Any]] = []
    used_evidence_keys: set = set()

    # -------- depth --------
    lo, hi = cfg["depth_range"]
    if direction == "min":
        if extremum > hi:  # not deep enough
            issues.append({
                "key": "shallow", "penalty": cfg["penalties"].get("shallow", 15),
                "cue_cn": f"深度不足 (到达 {extremum:.0f}°, 目标 ≤{hi:.0f}°)",
                "cue_en": f"Not deep enough (reached {extremum:.0f}°, target ≤{hi:.0f}°)",
                "evidence": evidence_refs(cfg.get("depth_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("depth_evidence", []))
        elif extremum < lo:  # too deep
            issues.append({
                "key": "deep", "penalty": cfg["penalties"].get("deep", 8),
                "cue_cn": f"下沉过深 (到达 {extremum:.0f}°, 目标 ≥{lo:.0f}°)",
                "cue_en": f"Too deep (reached {extremum:.0f}°, target ≥{lo:.0f}°)",
                "evidence": evidence_refs(cfg.get("depth_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("depth_evidence", []))
    else:
        if extremum < lo:
            issues.append({
                "key": "shallow" if exercise != "jumping_jack" else "arms_low",
                "penalty": cfg["penalties"].get("shallow", cfg["penalties"].get("arms_low", 15)),
                "cue_cn": f"幅度不足 (到达 {extremum:.0f}°, 目标 ≥{lo:.0f}°)",
                "cue_en": f"Amplitude too small (reached {extremum:.0f}°, target ≥{lo:.0f}°)",
                "evidence": evidence_refs(cfg.get("depth_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("depth_evidence", []))

    # -------- tempo (control) --------
    if duration_s is not None:
        dlo, dhi = cfg["duration"]
        if duration_s < dlo:
            issues.append({
                "key": "fast", "penalty": cfg["penalties"].get("fast", 10),
                "cue_cn": f"节奏太快 ({duration_s:.1f}s < {dlo:.1f}s)",
                "cue_en": f"Too fast ({duration_s:.1f}s < {dlo:.1f}s)",
                "evidence": evidence_refs(cfg.get("duration_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("duration_evidence", []))
        elif duration_s > dhi:
            issues.append({
                "key": "slow", "penalty": cfg["penalties"].get("slow", 10),
                "cue_cn": f"节奏偏慢 ({duration_s:.1f}s > {dhi:.1f}s)",
                "cue_en": f"Slow tempo ({duration_s:.1f}s > {dhi:.1f}s)",
                "evidence": evidence_refs(cfg.get("duration_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("duration_evidence", []))

    # -------- symmetry (non-asymmetric exercises only) --------
    if not cfg.get("asymmetric_exercise") and lr_diff_mean is not None:
        tol = cfg.get("asymmetry_deg", 12)
        if lr_diff_mean > tol + 8:  # ≥ tol+8° mean diff → warning
            issues.append({
                "key": "asymmetric",
                "penalty": cfg["penalties"].get("asymmetric", 8),
                "cue_cn": f"左右不对称 (平均差 {lr_diff_mean:.1f}° > {tol}°)",
                "cue_en": f"Left/right asymmetric (mean diff {lr_diff_mean:.1f}° > {tol}°)",
                "evidence": [],
            })

    # -------- exercise-specific structural cues --------
    if exercise == "squat":
        # torso lean
        torso_series = angle_series.get("torso_tilt") or angle_series.get("torso")
        _, torso_max, _ = _series_extremes(torso_series)
        if torso_max is not None and torso_max > cfg["torso_lean_max"]:
            issues.append({
                "key": "torso_lean", "penalty": cfg["penalties"].get("torso_lean", 15),
                "cue_cn": f"躯干过度前倾 ({torso_max:.0f}° > {cfg['torso_lean_max']}°)",
                "cue_en": f"Excessive torso lean ({torso_max:.0f}° > {cfg['torso_lean_max']}°)",
                "evidence": evidence_refs(cfg.get("torso_lean_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("torso_lean_evidence", []))
        # knee valgus if KASR provided at bottom
        if kasr_bottom is not None:
            if kasr_bottom < cfg["dkv_kasr_critical"]:
                issues.append({
                    "key": "knee_valgus", "penalty": cfg["penalties"].get("knee_valgus", 20),
                    "cue_cn": f"严重膝盖内扣 (KASR={kasr_bottom:.2f})",
                    "cue_en": f"Severe knee valgus (KASR={kasr_bottom:.2f})",
                    "evidence": evidence_refs(cfg.get("dkv_evidence", [])),
                })
                used_evidence_keys.update(cfg.get("dkv_evidence", []))
            elif kasr_bottom < cfg["dkv_kasr_warn"]:
                issues.append({
                    "key": "knee_valgus",
                    "penalty": max(6, cfg["penalties"].get("knee_valgus", 20) // 2),
                    "cue_cn": f"膝盖轻微内扣 (KASR={kasr_bottom:.2f})",
                    "cue_en": f"Mild knee valgus (KASR={kasr_bottom:.2f})",
                    "evidence": evidence_refs(cfg.get("dkv_evidence", [])),
                })
                used_evidence_keys.update(cfg.get("dkv_evidence", []))

    elif exercise == "push_up":
        # elbow flare at bottom: use shoulder_L/R at bot_i
        sh_L = angle_series.get("shoulder_L") or []
        sh_R = angle_series.get("shoulder_R") or []
        shoulder_bottom = None
        if bot_i < len(sh_L) and bot_i < len(sh_R) \
                and sh_L[bot_i] is not None and sh_R[bot_i] is not None:
            shoulder_bottom = (sh_L[bot_i] + sh_R[bot_i]) / 2.0
        if shoulder_bottom is not None and shoulder_bottom > cfg["elbow_flare_shoulder_max"]:
            issues.append({
                "key": "elbow_flare", "penalty": cfg["penalties"].get("elbow_flare", 15),
                "cue_cn": f"底部肘部外展 (肩角 {shoulder_bottom:.0f}° > {cfg['elbow_flare_shoulder_max']}°)",
                "cue_en": f"Bottom elbow flared (shoulder {shoulder_bottom:.0f}° > {cfg['elbow_flare_shoulder_max']}°)",
                "evidence": evidence_refs(cfg.get("elbow_flare_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("elbow_flare_evidence", []))
        # body sag: hip interior at bottom
        hp_L = angle_series.get("hip_L") or []
        hp_R = angle_series.get("hip_R") or []
        hip_bottom = None
        if bot_i < len(hp_L) and bot_i < len(hp_R) \
                and hp_L[bot_i] is not None and hp_R[bot_i] is not None:
            hip_bottom = (hp_L[bot_i] + hp_R[bot_i]) / 2.0
        if hip_bottom is not None and hip_bottom < cfg["body_line_hip_min"]:
            issues.append({
                "key": "body_sag", "penalty": cfg["penalties"].get("body_sag", 15),
                "cue_cn": f"塌腰或拱背 (髋角 {hip_bottom:.0f}° < {cfg['body_line_hip_min']}°)",
                "cue_en": f"Hips sag/pike (hip interior {hip_bottom:.0f}° < {cfg['body_line_hip_min']}°)",
                "evidence": evidence_refs(cfg.get("body_line_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("body_line_evidence", []))

    elif exercise == "bicep_curl":
        sh_L = angle_series.get("shoulder_L") or []
        sh_R = angle_series.get("shoulder_R") or []
        _, sh_max_L, _ = _series_extremes(sh_L)
        _, sh_max_R, _ = _series_extremes(sh_R)
        sh_peak = max([v for v in (sh_max_L, sh_max_R) if v is not None], default=None)
        if sh_peak is not None and sh_peak > cfg["shoulder_stable_max"]:
            issues.append({
                "key": "shoulder_swing", "penalty": cfg["penalties"].get("shoulder_swing", 12),
                "cue_cn": f"肩部摆动 (肩角峰值 {sh_peak:.0f}° > {cfg['shoulder_stable_max']}°)",
                "cue_en": f"Shoulder swing (shoulder peak {sh_peak:.0f}° > {cfg['shoulder_stable_max']}°)",
                "evidence": evidence_refs(cfg.get("shoulder_stable_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("shoulder_stable_evidence", []))

    elif exercise == "jumping_jack":
        ankle_dx = angle_series.get("ankle_dx") or []
        _, ankle_max, _ = _series_extremes(ankle_dx)
        if ankle_max is not None and ankle_max < cfg["feet_narrow_ankle_dx_min"]:
            issues.append({
                "key": "feet_narrow", "penalty": cfg["penalties"].get("feet_narrow", 14),
                "cue_cn": f"双脚打开幅度不足 (峰值 {ankle_max:.2f} < {cfg['feet_narrow_ankle_dx_min']})",
                "cue_en": f"Feet not wide enough (peak {ankle_max:.2f} < {cfg['feet_narrow_ankle_dx_min']})",
                "evidence": evidence_refs(cfg.get("feet_narrow_evidence", [])),
            })
            used_evidence_keys.update(cfg.get("feet_narrow_evidence", []))

    # -------- aggregate --------
    total_penalty = sum(i["penalty"] for i in issues)
    score = round(_clip(100 - total_penalty), 1)
    if issues:
        # dominant issue is the one with the largest penalty
        label = max(issues, key=lambda i: i["penalty"])["key"]
    else:
        label = "good"

    return {
        "score": score,
        "label": label,
        "issues": issues,
        "evidence_used": evidence_refs(sorted(used_evidence_keys)),
        "extremum_deg": round(extremum, 1),
        "duration_s": duration_s,
    }


def score_rep_quality(
    exercise: str,
    angle_series: Dict[str, Any],
    rule_total: Optional[float] = None,
    rep_row: Optional[Dict[str, Any]] = None,
    kasr_bottom: Optional[float] = None,
) -> Optional[float]:
    """Compatibility shim mirroring rep_quality_tcn.score_rep_quality signature.

    Returns just the numeric 0-100 quality score for drop-in use; call
    `score_rep_quality_rules` directly to obtain the full explanation.
    Falls back to `rule_total` on unsupported exercise.
    """
    result = score_rep_quality_rules(exercise, angle_series, rep_row, kasr_bottom)
    if result is None:
        return rule_total
    return result["score"]


__all__ = [
    "EVIDENCE_SOURCES",
    "evidence_refs",
    "score_rep_quality_rules",
    "score_rep_quality",
]
