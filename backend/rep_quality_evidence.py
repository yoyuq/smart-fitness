"""rep_quality_evidence.py — Evidence-based rep quality scorer

替代 rep_quality_tcn 的可解释算法, 每一条判定都带 peer-reviewed 引用。

输入: rep_scorer 产出的 angle_series (dict of channel -> [32 values]).
输出: 质量分 0-100 + 判定细节 (deficits: list of {code, penalty, evidence}).

设计原则:
- 与 rep_scorer.EXERCISE_CFG 的 depth_range/duration 保持一致 (来源已在 rep_scorer.py 顶部注释).
- 单角度看不见的问题走 angle_series 时序 (torso_tilt / shoulder / knee L-R diff / etc).
- 每条扣分都有 evidence.pmid + evidence.claim.

依赖: 纯 Python, 无 torch/numpy. 供 CI 与生产回退共用.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple


# ============================================================
# EVIDENCE_SOURCES 单一来源. 与 pose_engine.EVIDENCE_SOURCES 互补,
# 这里专门列 rep-level (angle_series 时序) 判定用的引用.
# ============================================================
EVIDENCE = {
    # -------- squat --------
    "squat.depth.parallel": {
        "claim": "Parallel squat = thighs parallel to ground at max knee flexion; "
                 "safe healthy-knee range 0-100° knee flexion (interior 80-180°).",
        "cite": "Escamilla RF. Med Sci Sports Exerc. 2001;33(1):127-41.",
        "pmid": "11194098",
    },
    "squat.depth.classification": {
        "claim": "Experimental depth conditions: 90° knee angle (parallel) vs 125° (half).",
        "cite": "O'Neill KE, Psycharakis SG. Sports Biomech. 2024;23(5):555-566.",
        "pmid": "33660588",
    },
    "squat.depth.threetier": {
        "claim": "Half / parallel / full three-tier depth classification (standard academic framework).",
        "cite": "Martínez-Cava A, et al. J Sports Sci. 2019;37(10):1088-1096.",
        "pmid": "30426840",
    },
    "squat.deep_safe": {
        "claim": "Deep squats are not inherently unsafe for healthy knees; concerns are load-dependent.",
        "cite": "Hartmann H, et al. Sports Med. 2013;43(10):993-1008.",
        "pmid": "23821469",
    },
    "squat.torso_lean": {
        "claim": "Excessive trunk forward lean during squat raises lumbar shear.",
        "cite": "Russell PJ, Phillips SJ. RQES. 1989;60(3):201-208.",
        "pmid": "2489844",
    },
    "squat.knee_valgus": {
        "claim": "Dynamic knee valgus (DKV) during single-leg squat is a risk factor for knee injury.",
        "cite": "Wilczyński B, et al. Int J Environ Res Public Health. 2020;17(21):8208.",
        "pmid": "33172101",
    },
    # -------- push-up --------
    "pushup.depth": {
        "claim": "Standard push-up bottom ≈ 90° elbow flexion (interior 90°).",
        "cite": "Dhahbi W, et al. Sports Biomech. 2022;21(1):1-40.",
        "pmid": "30284496",
    },
    "pushup.tucked_elbows": {
        "claim": "Standard push-up: tucked elbows (arms close to torso) reduce shoulder shear.",
        "cite": "McGill SM. J Strength Cond Res. 2014;28(1):105-16.",
        "pmid": "24088865",
    },
    "pushup.scapular": {
        "claim": "Flared elbows during push-up raise scapular protraction demand.",
        "cite": "Lunden JB, et al. J Shoulder Elbow Surg. 2010;19(2):216-23.",
        "pmid": "19733487",
    },
    # -------- lunge --------
    "lunge.front_knee": {
        "claim": "Forward-lunge descent covers 50-100° knee flexion; bottom ≈ 90° (interior 90°).",
        "cite": "Escamilla RF, et al. J Appl Biomech. 2022;38(4):210-220.",
        "pmid": "35697336",
    },
    "lunge.asymmetry": {
        "claim": "Clinically meaningful bilateral knee-flexion asymmetry begins around 4°.",
        "cite": "Hall M, et al. The Knee. 2015;22(6):506-9.",
        "pmid": "25907262",
    },
    # -------- rep counting algorithm --------
    "rep.peak_prominence": {
        "claim": "Peak-prominence detection on joint-angle series is the recommended "
                 "MediaPipe-based rep-counting algorithm for real-time systems.",
        "cite": "Jaiswal A, Chauhan G, Srivastava N. ACM RecSys 2023.",
        "arxiv": "2310.07221",
    },
    # -------- tempo --------
    "tempo.eccentric": {
        "claim": "Eccentric phase duration influences concentric velocity/power; "
                 "typical ecc+con range 2-6 s in resistance training.",
        "cite": "Carzoli JP, et al. J Sports Sci. 2019;37(23):2676-2684.",
        "pmid": "31418323",
    },
    "tempo.tut": {
        "claim": "Time-under-tension is a valid volume descriptor separate from rep count.",
        "cite": "Wilk M, et al. Front Physiol. 2021;12:629199.",
        "pmid": "33551848",
    },
    # -------- bicep curl / shoulder press --------
    "curl.rom": {
        "claim": "Standard biceps-curl ROM 0-135° elbow flexion (interior 45-180°).",
        "cite": "Pedrosa GF, et al. Sports. 2023;11(2):39.",
        "pmid": "36828324",
    },
    "shoulder_press.lockout": {
        "claim": "Overhead press lockout: elbow interior ~170-180°, glenohumeral ~150-180°.",
        "cite": "Gundersen AH, et al. Sports Biomech. 2025 (online).",
        "pmid": "41335596",
    },
    # -------- jumping jack --------
    "jack.arm_leg": {
        "claim": "Jumping jack = full arm abduction 0-180° at glenohumeral joint plus leg abduction.",
        "cite": "Lam JH, Bordoni B. StatPearls NBK537148 (2023).",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK537148/",
    },
    # -------- plank --------
    "plank.straight": {
        "claim": "Prone plank is defined by a straight body line (hip interior ~180°); "
                 "sag <160° or pike >200° is a technique failure.",
        "cite": "Ekstrom RA, et al. J Orthop Sports Phys Ther. 2007;37(12):754-62.",
        "pmid": "18560185",
    },
}


# ============================================================
# Per-exercise深度 range (与 rep_scorer.EXERCISE_CFG 保持一致).
# ============================================================
DEPTH = {
    # 2026-07-04: 上界 100°→90° (parallel squat, Escamilla 2001 PMID:11194098).
    # 旧 100° 导致 27/52 用户标为 shallow 的 rep 仍得满分.
    "squat":          {"joint": "primary", "extremum": "min", "range": (20, 90),
                       "shallow_cite": "squat.depth.parallel", "deep_cite": "squat.deep_safe",
                       "deep_severity": "info"},
    "push_up":        {"joint": "primary", "extremum": "min", "range": (60, 78),
                       "shallow_cite": "pushup.depth", "deep_cite": "pushup.depth"},
    "lunge":          {"joint": "primary", "extremum": "min", "range": (60, 110),
                       "shallow_cite": "lunge.front_knee", "deep_cite": "lunge.front_knee",
                       "deep_severity": "info"},
    "bicep_curl":     {"joint": "primary", "extremum": "min", "range": (30, 60),
                       "shallow_cite": "curl.rom", "deep_cite": "curl.rom"},
    "shoulder_press": {"joint": "primary", "extremum": "max", "range": (150, 180),
                       "shallow_cite": "shoulder_press.lockout", "deep_cite": None},
    "jumping_jack":   {"joint": "shoulder", "extremum": "max", "range": (140, 180),
                       "shallow_cite": "jack.arm_leg", "deep_cite": None},
}


def _peak(seq, mode):
    """Return extremum value ignoring None."""
    vals = [v for v in seq if v is not None]
    if not vals:
        return None
    return min(vals) if mode == "min" else max(vals)


def _mean(seq):
    vals = [v for v in seq if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _abs_mean(seq):
    return _mean([abs(v) if v is not None else None for v in seq or []])


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def score_rep_quality_evidence(
    angle_series: Dict[str, List[float]],
    exercise: str,
    duration_s: Optional[float] = None,
) -> Dict:
    """Evidence-based 质量分 (0-100) + 判定明细.

    Args:
        angle_series: rep_scorer 的输出, keys 包含 'primary', 'torso',
            'lr_diff', 'shoulder' 等 (至少要有 'primary' 才能算深度).
        exercise: 动作代码.
        duration_s: rep 时长 (可选, 有则参与 tempo 判定).

    Returns:
        {
            'score': float 0-100,
            'exercise': str,
            'deficits': [{'code','penalty','feedback','evidence'}],
            'evidence_used': [{'code','citation','pmid|arxiv|url'}],
        }
    """
    result = {
        "score": 100.0,
        "exercise": exercise,
        "deficits": [],
        "evidence_used": [],
    }
    cfg = DEPTH.get(exercise)
    if cfg is None:
        return result

    # ---------------- 深度 (depth) ----------------
    primary = angle_series.get(cfg["joint"]) or []
    peak = _peak(primary, cfg["extremum"])
    if peak is None:
        result["deficits"].append({
            "code": "no_signal",
            "penalty": 100,
            "feedback": "关节角度未采集到, 无法评分",
            "evidence": None,
        })
        result["score"] = 0.0
        return result

    lo, hi = cfg["range"]
    depth_penalty = 0
    if cfg["extremum"] == "min":
        if peak > hi:
            depth_penalty = min(50, (peak - hi) * 2)
            result["deficits"].append({
                "code": "too_shallow",
                "penalty": depth_penalty,
                "feedback": f"深度不足 (峰值 {peak:.0f}°, 应≤ {hi}°)",
                "evidence": _evidence_of(cfg["shallow_cite"]),
            })
        elif peak < lo and cfg["deep_cite"]:
            # 无负重场景下很深的蹲不应重扣分 (Hartmann 2013).
            # deep_severity == "info" 时 penalty 降级为反馈性提示 (最多 -8).
            base = (lo - peak) * 1.5
            if cfg.get("deep_severity") == "info":
                depth_penalty = min(8, base)
                fb = f"下沉很深 (峰值 {peak:.0f}°, 无负重下安全; 负重时注意膝关节压力)"
            else:
                depth_penalty = min(25, base)
                fb = f"下沉过深 (峰值 {peak:.0f}°, 建议不低于 {lo}°)"
            result["deficits"].append({
                "code": "too_deep",
                "penalty": depth_penalty,
                "feedback": fb,
                "evidence": _evidence_of(cfg["deep_cite"]),
            })
    else:  # extremum == "max"
        if peak < lo:
            depth_penalty = min(50, (lo - peak) * 2)
            result["deficits"].append({
                "code": "too_shallow",
                "penalty": depth_penalty,
                "feedback": f"幅度不足 (峰值 {peak:.0f}°, 应≥ {lo}°)",
                "evidence": _evidence_of(cfg["shallow_cite"]),
            })

    # ---------------- 结构性错误 ----------------
    # squat: 底部过度前倾 → torso_tilt 均值 > 45° (evidence: Russell 1989)
    if exercise == "squat":
        torso = angle_series.get("torso") or angle_series.get("torso_tilt")
        if torso:
            tm = _abs_mean(torso)
            if tm is not None and tm > 45:
                pen = min(20, (tm - 45) * 1.0)
                result["deficits"].append({
                    "code": "trunk_forward_lean",
                    "penalty": pen,
                    "feedback": f"躯干过度前倾 (平均 {tm:.0f}°, 挺胸收紧核心)",
                    "evidence": _evidence_of("squat.torso_lean"),
                })

    # push_up: 底部 shoulder 均值 > 55° = 肘外展 (evidence: McGill 2014)
    if exercise == "push_up":
        sho = angle_series.get("shoulder") or angle_series.get("left_shoulder")
        if sho:
            sm = _mean(sho[-8:])  # 底部帧
            if sm is not None and sm > 55:
                pen = min(20, (sm - 55) * 1.0)
                result["deficits"].append({
                    "code": "elbow_flare",
                    "penalty": pen,
                    "feedback": f"底部肘外展 (肩角 {sm:.0f}°, 大臂应约45°收向躯干)",
                    "evidence": _evidence_of("pushup.tucked_elbows"),
                })

    # 左右不对称 (适用于对称动作, 弓步跳过)
    if exercise != "lunge":
        lr = angle_series.get("lr_diff") or []
        lr_mean = _abs_mean(lr)
        if lr_mean is not None and lr_mean > 10:
            pen = min(15, (lr_mean - 10) * 1.5)
            result["deficits"].append({
                "code": "left_right_asymmetry",
                "penalty": pen,
                "feedback": f"左右差异过大 (均 {lr_mean:.0f}°, 注意发力均衡)",
                "evidence": _evidence_of("lunge.asymmetry"),  # 借用 Hall 2015
            })

    # ---------------- 节奏 (tempo) ----------------
    if duration_s is not None:
        # 与 rep_scorer 一致的合理区间; 极端速度扣分
        if duration_s < 0.6:
            pen = 20
            result["deficits"].append({
                "code": "too_fast",
                "penalty": pen,
                "feedback": f"速度过快 ({duration_s:.1f}s), 离心失控",
                "evidence": _evidence_of("tempo.eccentric"),
            })
        elif duration_s > 10:
            pen = 10
            result["deficits"].append({
                "code": "too_slow",
                "penalty": pen,
                "feedback": f"速度过慢 ({duration_s:.1f}s), 保持连贯",
                "evidence": _evidence_of("tempo.tut"),
            })

    # ---------------- 汇总 ----------------
    total_penalty = sum(d["penalty"] for d in result["deficits"])
    result["score"] = round(_clamp(100.0 - total_penalty), 1)
    result["evidence_used"] = list({
        d["evidence"]["code"]: d["evidence"]
        for d in result["deficits"] if d.get("evidence")
    }.values())
    return result


def _evidence_of(code: Optional[str]) -> Optional[Dict]:
    if not code:
        return None
    src = EVIDENCE.get(code)
    if not src:
        return None
    return {
        "code": code,
        "claim": src["claim"],
        "citation": src.get("cite", ""),
        "pmid": src.get("pmid"),
        "arxiv": src.get("arxiv"),
        "url": src.get("url") or (f"https://pubmed.ncbi.nlm.nih.gov/{src['pmid']}/"
                                   if src.get("pmid") else None),
    }


# CLI ------------------------------------------------------------
if __name__ == "__main__":
    # python rep_quality_evidence.py [rep_id]
    # 读取 fitness.db, 用 angle_series 打分并打印明细.
    import os, sys, sqlite3, json
    dbp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fitness.db")
    if not os.path.exists(dbp):
        print("fitness.db not found"); sys.exit(1)
    rep_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
    if rep_id:
        rows = c.execute(
            "SELECT id, exercise, total, duration_s, angle_series FROM rep_scores WHERE id=?",
            (rep_id,)).fetchall()
    else:
        rows = c.execute(
            "SELECT id, exercise, total, duration_s, angle_series FROM rep_scores "
            "WHERE angle_series IS NOT NULL ORDER BY id DESC LIMIT 5").fetchall()
    for r in rows:
        try:
            asr = json.loads(r["angle_series"] or "{}")
        except Exception:
            print(f"rep {r['id']}: 无法解析 angle_series"); continue
        out = score_rep_quality_evidence(asr, r["exercise"], r["duration_s"])
        print(f"\nRep #{r['id']} {r['exercise']}: rule_total={r['total']} evidence_score={out['score']}")
        for d in out["deficits"]:
            ev = d.get("evidence") or {}
            print(f"  - {d['code']}: -{d['penalty']:.1f} — {d['feedback']}")
            if ev:
                print(f"    -> {ev.get('citation','?')} PMID:{ev.get('pmid') or ev.get('arxiv','?')}")
    c.close()
