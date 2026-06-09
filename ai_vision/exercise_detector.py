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

import time
import numpy as np
from typing import Optional, Dict, List, Tuple
from enum import Enum


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

    _THRESHOLDS = {
        ExerciseType.JUMPING_JACK: {'down': 120, 'up': 165},
        ExerciseType.BICEP_CURL: {'down': 110, 'up': 35},
        ExerciseType.SHOULDER_PRESS: {'down': 30, 'up': 130},
        ExerciseType.SQUAT: {'down': 110, 'up': 150},
        ExerciseType.PUSH_UP: {'down': 90, 'up': 150},
        ExerciseType.LUNGE: {'down': 100, 'up': 145},
        ExerciseType.PLANK: {'body_angle': 20},
    }

    def __init__(self):
        self.current_exercise = ExerciseType.IDLE
        self.rep_count = 0
        self.stage = RepStage.UP
        self._consecutive_frames_down = 0
        self._consecutive_frames_up = 0
        self._required_frames = 2  # At ~2fps preview, 3 frames is too strict; 2 frames = 1 second hysteresis
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

    def count_squat(self, angles: Dict[str, Optional[float]]) -> int:
        """Count squat reps using average knee angle."""
        left_knee = angles.get('left_knee')
        right_knee = angles.get('right_knee')
        avg_knee = self._safe_avg(left_knee, right_knee)
        if avg_knee is None:
            return self.rep_count

        return self._count_with_threshold(
            avg_knee, ExerciseType.SQUAT,
            down_threshold=130, up_threshold=145
        )

    def count_push_up(self, angles: Dict[str, Optional[float]]) -> int:
        """Count push-up reps using average elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count

        return self._count_with_threshold(
            avg_elbow, ExerciseType.PUSH_UP,
            down_threshold=90, up_threshold=150
        )

    def count_jumping_jack(self, angles: Dict[str, Optional[float]]) -> int:
        """Count jumping jack reps using elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count
        t = self._THRESHOLDS[ExerciseType.JUMPING_JACK]
        return self._count_with_threshold(avg_elbow, ExerciseType.JUMPING_JACK, t['down'], t['up'])

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

        return self._count_with_threshold(
            min_knee, ExerciseType.LUNGE,
            down_threshold=100, up_threshold=145
        )

    def count_bicep_curl(self, angles: Dict[str, Optional[float]]) -> int:
        """Count bicep curl reps using average elbow angle."""
        left_elbow = angles.get('left_elbow')
        right_elbow = angles.get('right_elbow')
        avg_elbow = self._safe_avg(left_elbow, right_elbow)
        if avg_elbow is None:
            return self.rep_count

        t = self._THRESHOLDS[ExerciseType.BICEP_CURL]
        return self._count_with_threshold(avg_elbow, ExerciseType.BICEP_CURL, t['down'], t['up'])

    def count_shoulder_press(self, angles: Dict[str, Optional[float]]) -> int:
        """Count shoulder press reps using average shoulder angle."""
        left_shoulder = angles.get('left_shoulder')
        right_shoulder = angles.get('right_shoulder')
        avg_shoulder = self._safe_avg(left_shoulder, right_shoulder)
        if avg_shoulder is None:
            return self.rep_count

        t = self._THRESHOLDS[ExerciseType.SHOULDER_PRESS]
        return self._count_with_threshold(avg_shoulder, ExerciseType.SHOULDER_PRESS, t['down'], t['up'])

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
        self._history.clear()
