"""rep_scorer.py - 按次(rep)评分引擎 (评分 V2 第一阶段)

评分单位从"帧"改为"一次完整动作":
  - 跟随 ExerciseDetector 的 rep_count, 在计数自增的瞬间结算一次动作
  - 结算三个分项:
      depth    深度分: 动作期间主角度极值 vs 该动作的满分区间
      control  控制分: 动作时长 vs 合理区间 (过快=自由落体, 过慢=卡顿)
      symmetry 对称分: 动作期间左右主角度差的均值
  - total = 0.5*depth + 0.3*control + 0.2*symmetry
会话成绩 = 各 rep total 均值, 不再被组间站立帧稀释。

plank 等静态动作不适用 rep 模型, 不在本模块处理(保留帧评分)。
"""
import time
import logging
from typing import Dict, List, Optional

log = logging.getLogger("rep_scorer")

# 各动作: (主角度键, 极值方向, 深度满分区间, 时长合理区间秒)
# 主角度键对应 route 的 det_angles: left_/right_ + knee/elbow/shoulder
EXERCISE_CFG = {
    "squat":          {"joint": "knee",     "extremum": "min", "depth_range": (70, 100),  "duration": (1.5, 6.0)},
    "push_up":        {"joint": "elbow",    "extremum": "min", "depth_range": (60, 90),   "duration": (1.0, 5.0)},
    "lunge":          {"joint": "knee",     "extremum": "min", "depth_range": (80, 110),  "duration": (1.5, 6.0), "asymmetric": True},
    "bicep_curl":     {"joint": "elbow",    "extremum": "min", "depth_range": (30, 60),   "duration": (1.0, 5.0)},
    "shoulder_press": {"joint": "shoulder", "extremum": "max", "depth_range": (150, 180), "duration": (1.0, 5.0)},
    "jumping_jack":   {"joint": "elbow",    "extremum": "max", "depth_range": (140, 180), "duration": (0.4, 2.0)},
}

DEPTH_FEEDBACK = {
    "squat":          ("蹲得太浅, 大腿尽量与地面平行", "蹲太深, 注意膝盖压力"),
    "push_up":        ("下放不够, 胸口尽量贴近地面", "下放过深"),
    "lunge":          ("前膝弯曲不足, 再往下沉", "下沉过深"),
    "bicep_curl":     ("没弯举到位, 收缩到顶", "弯举过度"),
    "shoulder_press": ("没推到顶, 手臂完全伸展", ""),
    "jumping_jack":   ("手臂摆动幅度不足, 举过头顶", ""),
}


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _score_depth(extremum: float, direction: str, lo: float, hi: float):
    """主角度极值落在 [lo,hi] 满分; 越界线性衰减."""
    if direction == "min":
        # 极小值: 大于 hi = 不够深; 小于 lo = 过深
        if extremum > hi:
            return _clamp(100 - (extremum - hi) * 2.0), "shallow"
        if extremum < lo:
            return _clamp(100 - (lo - extremum) * 1.5), "too_deep"
        return 100.0, None
    else:
        # 极大值: 小于 lo = 不到位
        if extremum < lo:
            return _clamp(100 - (lo - extremum) * 2.0), "shallow"
        return 100.0, None


def _score_control(duration_s: float, lo: float, hi: float):
    if duration_s < lo:
        # 过快: 离心失控
        return _clamp(100 - (lo - duration_s) / max(lo, 1e-6) * 80), "fast"
    if duration_s > hi:
        return _clamp(100 - (duration_s - hi) * 5), "slow"
    return 100.0, None


def _score_symmetry(mean_lr_diff: float):
    # 左右差 10° 以内不扣分, 之后每度扣 2 分, 最多扣 40
    return _clamp(100 - min(40, max(0.0, mean_lr_diff - 10) * 2)), \
        ("asym" if mean_lr_diff > 20 else None)


class RepScorer:
    """单设备单动作的按次评分器. 与 ExerciseDetector 同步使用."""

    def __init__(self):
        self.exercise: Optional[str] = None
        self.prev_count: int = 0
        self.frames: List[Dict] = []      # 当前进行中动作的帧缓冲
        self.rep_scores: List[Dict] = []  # 本会话已完成动作分数
        self.last_rep: Optional[Dict] = None

    def reset(self, exercise: Optional[str] = None):
        self.exercise = exercise
        self.prev_count = 0
        self.frames = []
        self.rep_scores = []
        self.last_rep = None

    def add_frame(self, exercise: str, angles: Dict[str, Optional[float]],
                  rep_count: int, ts: Optional[float] = None) -> Optional[Dict]:
        """喂入一个有效帧. rep_count 自增时结算并返回该次动作的分数, 否则返回 None.

        angles: route 的 det_angles (left_knee/right_knee/left_elbow/... 可为 None)
        """
        if exercise != self.exercise:
            self.reset(exercise)
        cfg = EXERCISE_CFG.get(exercise)
        if cfg is None:
            return None
        ts = ts if ts is not None else time.time()

        joint = cfg["joint"]
        l = angles.get(f"left_{joint}")
        r = angles.get(f"right_{joint}")
        vals = [v for v in (l, r) if v is not None]
        if vals:
            if cfg.get("asymmetric"):
                # 弓步取更弯的那条腿
                primary = min(vals) if cfg["extremum"] == "min" else max(vals)
            else:
                primary = sum(vals) / len(vals)
            lr_diff = abs(l - r) if (l is not None and r is not None) else None
            self.frames.append({"ts": ts, "primary": primary, "lr_diff": lr_diff})

        completed = None
        if rep_count > self.prev_count and self.frames:
            completed = self._finalize(cfg)
        self.prev_count = max(self.prev_count, rep_count)
        return completed

    def _finalize(self, cfg) -> Optional[Dict]:
        frames = self.frames
        self.frames = []
        if len(frames) < 2:
            return None

        primaries = [f["primary"] for f in frames]
        extremum = min(primaries) if cfg["extremum"] == "min" else max(primaries)
        duration = frames[-1]["ts"] - frames[0]["ts"]
        diffs = [f["lr_diff"] for f in frames if f["lr_diff"] is not None]
        mean_diff = (sum(diffs) / len(diffs)) if diffs else 0.0

        lo, hi = cfg["depth_range"]
        depth, depth_issue = _score_depth(extremum, cfg["extremum"], lo, hi)
        dlo, dhi = cfg["duration"]
        control, ctrl_issue = _score_control(duration, dlo, dhi)
        if cfg.get("asymmetric"):
            symmetry, sym_issue = 100.0, None   # 弓步天然不对称, 不计此项
        else:
            symmetry, sym_issue = _score_symmetry(mean_diff)

        total = round(0.5 * depth + 0.3 * control + 0.2 * symmetry, 1)

        fb = []
        shallow_fb, deep_fb = DEPTH_FEEDBACK.get(self.exercise, ("幅度不足", "幅度过大"))
        if depth_issue == "shallow" and shallow_fb:
            fb.append(shallow_fb)
        elif depth_issue == "too_deep" and deep_fb:
            fb.append(deep_fb)
        if ctrl_issue == "fast":
            fb.append("速度太快, 控制下放节奏")
        elif ctrl_issue == "slow":
            fb.append("速度偏慢, 保持连贯")
        if sym_issue == "asym":
            fb.append("左右不对称, 注意发力均衡")

        rep = {
            "rep_index": len(self.rep_scores) + 1,
            "exercise": self.exercise,
            "depth": round(depth, 1),
            "control": round(control, 1),
            "symmetry": round(symmetry, 1),
            "total": total,
            "peak_angle": round(extremum, 1),
            "duration_s": round(duration, 2),
            "feedback": "; ".join(fb) if fb else "漂亮, 标准动作!",
            "ts": frames[-1]["ts"],
        }
        self.rep_scores.append(rep)
        self.last_rep = rep
        return rep

    def session_avg(self) -> Optional[float]:
        if not self.rep_scores:
            return None
        return round(sum(r["total"] for r in self.rep_scores) / len(self.rep_scores), 1)


# ============ 模块级注册表 (与 _detectors 同生命周期) ============
_scorers: Dict[str, RepScorer] = {}


def get_rep_scorer(device_id: str) -> RepScorer:
    sc = _scorers.get(device_id or "default")
    if sc is None:
        sc = RepScorer()
        _scorers[device_id or "default"] = sc
    return sc


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rep_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            rep_index INTEGER,
            exercise TEXT,
            depth REAL, control REAL, symmetry REAL, total REAL,
            peak_angle REAL, duration_s REAL,
            feedback TEXT,
            ts REAL
        )""")
    conn.commit()
