"""
exercise_detector.py - Exercise Classification and Rep Counting
===============================================================
Framework: MediaPipe Pose (Google)
  Source: https://github.com/google/mediapipe
  License: Apache 2.0

Detects exercises from pose landmarks and counts repetitions.
Uses angle-based state machines for robust rep tracking.

Supported exercises:
  - squat, push_up, jumping_jack, lunge
  - plank, bicep_curl, shoulder_press
"""

import os
import json
import time
import logging
import numpy as np
from typing import Optional, Dict, List, Tuple
from enum import Enum

_log = logging.getLogger("exercise_detector")

# rep 计数默认配置（与历史运行值一致，保证 exercises.json 缺失时行为不变）。
# joint 仅作文档说明，关节角度的实际选取仍在各 count_* 方法里。
#
# 阈值来源（interior-angle 约定：180° = 完全伸展，越弯越小；文献常用 flexion 角，
# 二者互补：interior = 180° - flexion）：
#
# * squat  down=100 (interior≈parallel), up=160 (near-extension)
#   - Escamilla RF. Med Sci Sports Exerc. 2001;33(1):127-41. PMID:11194098
#     "parallel squat = thighs parallel to ground at max knee flexion";
#     safe range 0-100° knee flexion → interior 80-180°. Parallel ≈ interior 90°;
#     取 100° 作为"到位"下限，比 strict parallel 略宽松，避免家用挫败感。
#   - O'Neill KE, Psycharakis SG. Sports Biomech. 2024;23(5):555-566. PMID:33660588
#     实验中用 "90° knee angle (parallel)" 与 "125° knee angle (half)" 两档；
#     interior 100° 处于两档之间，防止 half squat 被误计数。
#   - Martínez-Cava A, et al. J Sports Sci. 2019;37(10):1088-1096. PMID:30426840
#     Half / parallel / full 三档命名是学术界通用框架。
#
# * push_up  down=90 (elbow≈90° chest-to-ground), up=150 (near-lockout)
#   - Dhahbi W, et al. Sports Biomech. 2022;21(1):1-40. PMID:30284496
#     Systematic review: standard push-up bottom ≈ elbow flexion 90° (interior 90°).
#   - McGill SM. J Strength Cond Res. 2014;28(1):105-16. PMID:24088865
#     标准 push-up spine compression 数据集来源；ROM 与上文一致。
#
# * lunge  down=100 (front knee ~parallel), up=145 (near-standing)
#   - Escamilla RF, et al. J Appl Biomech. 2022;38(4):210-220. PMID:35697336
#     Lunge descent 覆盖 knee flexion 50°-100°；bottom 约 90° flexion → interior 90°；
#     取 100° 作为"到位"下限；up 145° = 大腿接近直立但仍轻微弯（clinical asymmetry 阈值参考 Hall 2015）。
#
# * bicep_curl  down=110, up=35 (方向反转：curl 越弯 elbow interior 越小)
#   - Pedrosa GF, et al. Sports. 2023;11(2):39. PMID:36828324
#     标准 curl ROM 0-135° flexion (interior 45-180°); curl top ≈ interior 30-45°。
#
# * shoulder_press  down=30 (肩内收), up=130 (推举过头)
#   - Gundersen AH, et al. Sports Biomech. 2025 (online). PMID:41335596
#     Overhead press lockout: elbow interior ~170-180°, glenohumeral ~150-180°。
#     Down 阶段肩角约 30° 起始（拳头齐肩）。
#
# * jumping_jack  down=120, up=165
#   - Lam JH, Bordoni B. StatPearls NBK537148 (2023).
#     Full arm abduction 0-180° at glenohumeral joint；手臂过头顶 ≥ 150° 视为完成。
#
# rep counting 算法本身（peak-prominence detection）：
#   - Jaiswal A, Chauhan G, Srivastava N. ACM RecSys 2023. arxiv:2310.07221
#     MediaPipe + peak-prominence detection 是当前推荐的实时 rep-counting 方法。
#
# 单一 evidence 来源：`backend/docs/rep_completion_algorithm_evidence.md`
_DEFAULT_COUNT_CFG: Dict[str, Dict[str, float]] = {
    "squat":          {"joint": "avg_knee",     "down": 100, "up": 160},   # was 130/145
    "push_up":        {"joint": "avg_elbow",    "down": 90,  "up": 150},
    "jumping_jack":   {"joint": "avg_elbow",    "down": 120, "up": 165},
    "lunge":          {"joint": "min_knee",     "down": 100, "up": 145},   # was 100/145 (kept)
    "bicep_curl":     {"joint": "avg_elbow",    "down": 110, "up": 35},
    "shoulder_press": {"joint": "avg_shoulder", "down": 30,  "up": 130},
}
_DEFAULT_REQUIRED_FRAMES = 1
_CONFIG_FILENAME = "exercises.json"


def load_count_config() -> Tuple[Dict[str, Dict[str, float]], int]:
    """读取同目录 exercises.json 覆盖默认阈值。任何错误都回退默认，绝不抛出。"""
    cfg = {k: dict(v) for k, v in _DEFAULT_COUNT_CFG.items()}
    required_frames = _DEFAULT_REQUIRED_FRAMES
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _CONFIG_FILENAME)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            required_frames = int(data.get("required_frames", required_frames))
            for name, ov in (data.get("exercises") or {}).items():
                if not isinstance(ov, dict):
                    continue
                entry = cfg.get(name, {"joint": ov.get("joint", "avg_elbow")})
                if "down" in ov:  entry["down"] = float(ov["down"])
                if "up" in ov:    entry["up"] = float(ov["up"])
                if "joint" in ov: entry["joint"] = ov["joint"]
                cfg[name] = entry
            _log.info("exercise_detector: loaded %d exercises from %s (required_frames=%d)",
                      len(cfg), _CONFIG_FILENAME, required_frames)
    except Exception as e:
        _log.warning("exercise_detector: exercises.json load failed (%s), using defaults", e)
    return cfg, required_frames


class ExerciseType(Enum):
    """Supported exercise types."""
    SQUAT = "squat"
    PUSH_UP = "push_up"
    JUMPING_JACK = "jumping_jack"
    LUNGE = "lunge"
    PLANK = "plank"
    BICEP_CURL = "bicep_curl"
    SHOULDER_PRESS = "shoulder_press"
    IDLE = "idle"


class RepStage(Enum):
    """Current stage of a repetition."""
    UP = "up"          # Starting/relaxed position
    DOWN = "down"      # Contracted/active position
    TRANSITION = "transition"  # Between stages


class ExerciseDetector:
    """
    Detects exercises and counts reps using angle-based state machines.

    Each exercise has:
      - Key angles to monitor
      - Thresholds for up/down detection
      - A state machine for rep counting
    """

    def __init__(self):
        self.current_exercise = ExerciseType.IDLE
        self.rep_count = 0
        self.stage = RepStage.UP
        self._consecutive_frames_down = 0
        self._consecutive_frames_up = 0
        self._angle_hist: List[float] = []   # 主角度近 3 帧, 做中值平滑滤单帧噪声尖刺
        # 按谷计数状态(正常方向动作: 角度在底部变小, 深蹲/俯卧撑/弓步)
        self._v_phase = "asc"        # asc=上升/找峰, desc=下降/找谷
        self._v_extreme: Optional[float] = None   # 当前段的极值(谷或峰)
        self._v_deep = False         # 本次下蹲是否到过"够深"(谷 <= 深度门槛)
        # rep 计数阈值改为外置配置 ai_vision/exercises.json（借鉴 Good-GYM），缺失则回退默认。
        # _required_frames: 相位确认所需连续帧数。预览仅 ~2fps, 一个动作相位常只采到 1 帧,
        # 要求连续 2 帧会漏计 (2026-06-13 真实视频诊断: rf=2 大量漏到 0, rf=1 恢复)。
        # 去抖由 down/up 阈值间隙 + 完整 UP->DOWN->UP 循环保证, 不靠帧数。
        self._count_cfg, self._required_frames = load_count_config()
        self._history: List[Tuple[ExerciseType, float, int]] = []  # (exercise, timestamp, rep)
        self._target_exercise: Optional[ExerciseType] = None
        self._max_history = 10000  # cap to prevent unbounded growth

    def set_target_exercise(self, exercise) -> None:
        """Set a specific exercise to track. Only this exercise will be detected.
        Accepts ExerciseType enum or string (e.g. "push_up")."""
        if isinstance(exercise, str):
            exercise = ExerciseType(exercise)
        self._target_exercise = exercise
        self.rep_count = 0
        self.stage = RepStage.UP
        self._consecutive_frames_down = 0
        self._consecutive_frames_up = 0
        self._angle_hist = []
        self._v_phase = "asc"; self._v_extreme = None; self._v_deep = False

    def get_target_exercise(self) -> Optional[ExerciseType]:
        """Return the currently targeted exercise, or None if no filter is active."""
        return self._target_exercise

    def clear_target_exercise(self) -> None:
        """Remove the target exercise filter; classify_exercise resumes normal behaviour."""
        self._target_exercise = None

    def classify_exercise(self, angles: Dict[str, Optional[float]]) -> ExerciseType:
        """
        Classify the current exercise based on joint angles.
        Returns the most likely exercise type.

        When a target exercise is set via set_target_exercise(), any detected
        exercise that does not match the target is reported as IDLE so that only
        target-exercise reps are counted.
        """
        detected = self._detect_exercise(angles)
        if (self._target_exercise is not None
                and detected not in (self._target_exercise, ExerciseType.IDLE)):
            return ExerciseType.IDLE
        return detected

    def _detect_exercise(self, angles: Dict[str, Optional[float]]) -> ExerciseType:
        """Raw exercise detection without target filtering."""
        # Check for jumping jack - uses arm and leg spread
        if self._check_jumping_jack(angles):
            return ExerciseType.JUMPING_JACK

        # Check plank - body should be nearly horizontal
        if self._check_plank(angles):
            return ExerciseType.PLANK

        # Check squat - knees bent significantly
        left_knee = angles.get('left_knee')
        right_knee = angles.get('right_knee')
        left_hip = angles.get('left_hip')
        right_hip = angles.get('right_hip')
        avg_knee = self._safe_avg(left_knee, right_knee)
        avg_hip = self._safe_avg(left_hip, right_hip)

        # Squat: knee < 120 (actually bent) AND hip < 130 (torso not standing upright)
        if avg_knee is not None and avg_knee < 120 and avg_hip is not None and avg_hip < 130:
            return ExerciseType.SQUAT

        # Check push up - elbows bent, body low
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is not None and avg_elbow < 110:
            return ExerciseType.PUSH_UP

        # Check lunge - one knee bent more than the other
        if self._check_lunge(angles):
            return ExerciseType.LUNGE

        # Check bicep curl - elbows flexed, shoulders stable
        if self._check_bicep_curl(angles):
            return ExerciseType.BICEP_CURL

        # Check shoulder press
        if self._check_shoulder_press(angles):
            return ExerciseType.SHOULDER_PRESS

        return ExerciseType.IDLE

    def _check_jumping_jack(self, angles: Dict[str, Optional[float]]) -> bool:
        """Detect jumping jack: arms straight overhead AND legs nearly straight."""
        avg_elbow = self._safe_avg(angles.get('left_elbow'), angles.get('right_elbow'))
        avg_knee = self._safe_avg(angles.get('left_knee'), angles.get('right_knee'))
        avg_shoulder = self._safe_avg(angles.get('left_shoulder'), angles.get('right_shoulder'))
        # Arms overhead (shoulder > 120), straight (elbow > 155), AND legs straight
        return (avg_elbow is not None and avg_elbow > 155
                and avg_shoulder is not None and avg_shoulder > 120
                and avg_knee is not None and avg_knee > 155)

    def _check_plank(self, angles: Dict[str, Optional[float]]) -> bool:
        """Detect plank: arms extended, body straight."""
        avg_shoulder = self._safe_avg(angles.get('left_shoulder'), angles.get('right_shoulder'))
        avg_hip = self._safe_avg(angles.get('left_hip'), angles.get('right_hip'))
        avg_elbow = self._safe_avg(angles.get('left_elbow'), angles.get('right_elbow'))

        # Plank: shoulders ~90deg, hips ~180deg, elbows extended (> 150 to avoid push-up)
        if all(v is not None for v in [avg_shoulder, avg_hip, avg_elbow]):
            if 60 < avg_shoulder < 120 and avg_hip > 150 and avg_elbow > 150:
                return True
        return False

    def _check_lunge(self, angles: Dict[str, Optional[float]]) -> bool:
        """Detect lunge: one knee bent more than the other."""
        left_knee = angles.get('left_knee')
        right_knee = angles.get('right_knee')
        if left_knee is not None and right_knee is not None:
            diff = abs(left_knee - right_knee)
            avg_knee = (left_knee + right_knee) / 2
            # One knee bent significantly more than the other
            if diff > 40 and avg_knee < 140:
                return True
        return False

    def _check_bicep_curl(self, angles: Dict[str, Optional[float]]) -> bool:
        """Detect bicep curl: elbow actively flexed with stable shoulder by side."""
        avg_elbow = self._safe_avg(angles.get('left_elbow'), angles.get('right_elbow'))
        avg_shoulder = self._safe_avg(angles.get('left_shoulder'), angles.get('right_shoulder'))
        # Elbow flexed (bent < 100), shoulder stable by side (50-110)
        return (avg_elbow is not None and avg_elbow < 100
                and avg_shoulder is not None and 50 < avg_shoulder < 110)

    def _check_shoulder_press(self, angles: Dict[str, Optional[float]]) -> bool:
        """Detect shoulder press: arms raised overhead, not just standing."""
        avg_shoulder = self._safe_avg(angles.get('left_shoulder'), angles.get('right_shoulder'))
        avg_elbow = self._safe_avg(angles.get('left_elbow'), angles.get('right_elbow'))
        # Shoulder angle large (arm raised), AND elbow extended (not bent at rest)
        return (avg_shoulder is not None and avg_shoulder > 130
                and avg_elbow is not None and avg_elbow > 100)

    def _safe_avg(self, a: Optional[float], b: Optional[float]) -> Optional[float]:
        """Safely compute average of two optional values."""
        vals = [v for v in [a, b] if v is not None]
        return np.mean(vals) if vals else None

    def _thr(self, name: str) -> Tuple[float, float]:
        """从外置配置取该动作的 (down, up) 阈值, 缺失回退默认。"""
        c = self._count_cfg.get(name) or _DEFAULT_COUNT_CFG.get(name, {})
        d = _DEFAULT_COUNT_CFG.get(name, {})
        return float(c.get("down", d.get("down", 90))), float(c.get("up", d.get("up", 150)))

    def _update_exercise(self, exercise: ExerciseType):
        """Update current exercise tracking."""
        if exercise != self.current_exercise and exercise != ExerciseType.IDLE:
            if self.current_exercise != ExerciseType.IDLE:
                # Exercise changed - log it
                pass
            self.current_exercise = exercise
            self.rep_count = 0
            self.stage = RepStage.UP
            self._consecutive_frames_down = 0
            self._consecutive_frames_up = 0
            self._angle_hist = []
            self._v_phase = "asc"; self._v_extreme = None; self._v_deep = False

    def count_squat(self, angles: Dict[str, Optional[float]]) -> int:
        """Count squat reps using average knee angle."""
        left_knee = angles.get('left_knee')
        right_knee = angles.get('right_knee')
        avg_knee = self._safe_avg(left_knee, right_knee)
        if avg_knee is None:
            return self.rep_count

        down, up = self._thr("squat")
        return self._count_with_threshold(avg_knee, ExerciseType.SQUAT, down, up)

    def count_push_up(self, angles: Dict[str, Optional[float]]) -> int:
        """Count push-up reps using average elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count

        down, up = self._thr("push_up")
        return self._count_with_threshold(avg_elbow, ExerciseType.PUSH_UP, down, up)

    def count_jumping_jack(self, angles: Dict[str, Optional[float]]) -> int:
        """Count jumping jack reps using elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count
        down, up = self._thr("jumping_jack")
        return self._count_with_threshold(avg_elbow, ExerciseType.JUMPING_JACK, down, up)

    def count_lunge(self, angles: Dict[str, Optional[float]]) -> int:
        """Count lunge reps using the more-bent knee."""
        left_knee = angles.get('left_knee')
        right_knee = angles.get('right_knee')
        if left_knee is None and right_knee is None:
            return self.rep_count

        # Use the more bent (smaller) knee angle
        min_knee = min(
            v for v in [left_knee, right_knee] if v is not None
        )

        down, up = self._thr("lunge")
        return self._count_with_threshold(min_knee, ExerciseType.LUNGE, down, up)

    def count_bicep_curl(self, angles: Dict[str, Optional[float]]) -> int:
        """Count bicep curl reps using average elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count

        down, up = self._thr("bicep_curl")
        return self._count_with_threshold(avg_elbow, ExerciseType.BICEP_CURL, down, up)

    def count_shoulder_press(self, angles: Dict[str, Optional[float]]) -> int:
        """Count shoulder press reps using average shoulder angle."""
        left_shoulder = angles.get('left_shoulder')
        right_shoulder = angles.get('right_shoulder')
        avg_shoulder = self._safe_avg(left_shoulder, right_shoulder)
        if avg_shoulder is None:
            return self.rep_count

        down, up = self._thr("shoulder_press")
        return self._count_with_threshold(avg_shoulder, ExerciseType.SHOULDER_PRESS, down, up)

    def _count_with_threshold(self,
                              angle: float,
                              exercise: ExerciseType,
                              down_threshold: float,
                              up_threshold: float) -> int:
        """
        Generic rep counting with hysteresis.

        State machine:
          UP -> (angle < down_threshold) -> DOWN
          DOWN -> (angle > up_threshold) -> UP (increment count)
        """
        self._update_exercise(exercise)

        # 3点中值平滑: 单帧噪声尖刺(站立时膝角偶尔被误估到很小)会被中值滤掉, 不再误触发;
        # 真实动作连续多帧, 中值照常跟随。代价: 状态切换延迟约1帧(可接受)。
        self._angle_hist.append(angle)
        if len(self._angle_hist) > 3:
            self._angle_hist.pop(0)
        _s = sorted(self._angle_hist)
        angle = _s[len(_s) // 2]

        # 正常方向动作(角度在底部变小: 深蹲/俯卧撑/弓步)改用"按谷计数":
        # 每个下蹲谷底回升 ~25° 即算一次, 不要求完全站直 → 根治"多次并一次", 且不放大误触发。
        # 反向动作(二头弯举/肩推, down>up)保留原阈值状态机。
        if down_threshold < up_threshold:
            return self._count_valley(angle, down_threshold)

        if self.stage == RepStage.UP:
            if angle < down_threshold:
                self._consecutive_frames_down += 1
                self._consecutive_frames_up = 0
                if self._consecutive_frames_down >= self._required_frames:
                    self.stage = RepStage.DOWN
            else:
                self._consecutive_frames_down = 0

        elif self.stage == RepStage.DOWN:
            if angle > up_threshold:
                self._consecutive_frames_up += 1
                self._consecutive_frames_down = 0
                if self._consecutive_frames_up >= self._required_frames:
                    self.stage = RepStage.UP
                    self.rep_count += 1
                    if len(self._history) < self._max_history:
                        self._history.append((exercise, time.time(), self.rep_count))
            else:
                self._consecutive_frames_up = 0

        return self.rep_count

    def _count_valley(self, a: float, depth_gate: float, rise: float = 18.0) -> int:
        """按谷计数: 跟踪角度的谷/峰交替, 每个"到过深度门槛的谷 + 回升≥rise"算一次。

        depth_gate: 谷必须到达 <= 此值才算真的下蹲(过滤站立小晃动)。
        rise: 回升/下降确认幅度(去抖), 小于此的抖动不切相位、不计数。
        不依赖"完全站直", 故连续做、起身不充分也能一个个分开; 站立小幅噪声不计。
        """
        if self._v_extreme is None:
            self._v_extreme = a
            self._v_phase = "asc"
            self._v_deep = False
            return self.rep_count

        if self._v_phase == "asc":               # 找峰(站起/上升段)
            if a > self._v_extreme:
                self._v_extreme = a
            elif a < self._v_extreme - rise:      # 从峰下降足够 → 进入下蹲段
                self._v_phase = "desc"
                self._v_extreme = a
                self._v_deep = (a <= depth_gate)
        else:                                     # desc: 找谷(下蹲段)
            if a < self._v_extreme:
                self._v_extreme = a
                if a <= depth_gate:
                    self._v_deep = True
            elif a > self._v_extreme + rise:      # 从谷回升足够 → 完成一次
                if self._v_deep:
                    self.rep_count += 1
                    if len(self._history) < self._max_history:
                        self._history.append((self.current_exercise, time.time(), self.rep_count))
                self._v_phase = "asc"
                self._v_extreme = a
                self._v_deep = False
        return self.rep_count

    def add_detection(self, timestamp: float):
        """Record a detection event for tracking."""
        if len(self._history) < self._max_history:
            self._history.append((self.current_exercise, timestamp, self.rep_count))

    def get_history(self) -> List[Tuple[ExerciseType, float, int]]:
        """Get detection history for analysis."""
        return self._history.copy()

    def reset(self):
        """Reset detector state."""
        self.current_exercise = ExerciseType.IDLE
        self.rep_count = 0
        self.stage = RepStage.UP
        self._consecutive_frames_down = 0
        self._consecutive_frames_up = 0
        self._angle_hist = []
        self._v_phase = "asc"; self._v_extreme = None; self._v_deep = False
        self._history.clear()
