"""pose_engine.py - MediaPipe Tasks API + 我们训练的动作分类器, 端到端推理."""
import os, pickle, time, logging
import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode

log = logging.getLogger("pose_engine")

DATASET_DIR = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\models"
CLASSIFIER_PATH = os.path.join(DATASET_DIR, "pose_classifier.pkl")
LANDMARKER_PATH = os.path.join(DATASET_DIR, "pose_landmarker_lite.task")

L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_SHO, R_SHO = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16


def angle3(a, b, c):
    ba = a - b
    bc = c - b
    cos = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-8)
    cos = float(np.clip(cos, -1, 1))
    return float(np.arccos(cos) * 180.0 / np.pi)


def make_features_single(landmarks_33x4):
    lm = landmarks_33x4.reshape(1, 33, 4)
    xyz = lm[..., :3]
    vis = lm[..., 3]
    hip_mid = (xyz[:, L_HIP] + xyz[:, R_HIP]) / 2
    sho_mid = (xyz[:, L_SHO] + xyz[:, R_SHO]) / 2
    torso_len = np.linalg.norm(sho_mid - hip_mid, axis=-1, keepdims=True) + 1e-6
    xyz_n = (xyz - hip_mid[:, None, :]) / torso_len[:, None, :]

    def _ang(idx_a, idx_b, idx_c):
        return angle3(xyz[0, idx_a], xyz[0, idx_b], xyz[0, idx_c])

    a_knee_L = _ang(L_HIP, L_KNEE, L_ANKLE)
    a_knee_R = _ang(R_HIP, R_KNEE, R_ANKLE)
    a_hip_L  = _ang(L_SHO, L_HIP, L_KNEE)
    a_hip_R  = _ang(R_SHO, R_HIP, R_KNEE)
    a_elb_L  = _ang(L_SHO, L_ELB, L_WRI)
    a_elb_R  = _ang(R_SHO, R_ELB, R_WRI)
    a_sho_L  = _ang(L_ELB, L_SHO, L_HIP)
    a_sho_R  = _ang(R_ELB, R_SHO, R_HIP)
    torso_vec = sho_mid[0] - hip_mid[0]
    torso_tilt = float(np.arctan2(torso_vec[0], -torso_vec[1]) * 180.0 / np.pi)
    hip_y = float(hip_mid[0, 1])
    sho_hip_y = float(abs(sho_mid[0, 1] - hip_mid[0, 1]))
    vis_mean = float(vis.mean())
    flat = xyz_n.reshape(1, -1)
    extras = np.array([[a_knee_L, a_knee_R, a_hip_L, a_hip_R, a_elb_L, a_elb_R,
                        a_sho_L, a_sho_R, torso_tilt, hip_y, sho_hip_y, vis_mean]], dtype=np.float32)
    feats = np.concatenate([flat.astype(np.float32), extras], axis=-1)

    # ---- 多关节生物力学量 (评分V2 第四阶段): 规则用单关节角看不见的维度 ----
    # 图像坐标 y 向下为正; 全部用 torso_len 归一化, 抵消远近.
    tl = float(torso_len[0, 0])
    ankle_dx = float(abs(xyz[0, L_ANKLE, 0] - xyz[0, R_ANKLE, 0]) / tl)      # 双脚横向间距(开合跳脚距)
    wrist_mid_y = (xyz[0, L_WRI, 1] + xyz[0, R_WRI, 1]) / 2
    wrist_above = float((sho_mid[0, 1] - wrist_mid_y) / tl)                   # >0 腕高于肩(手举过头)
    nose_y, nose_x = float(xyz[0, 0, 1]), float(xyz[0, 0, 0])
    head_drop = float((nose_y - sho_mid[0, 1]) / tl)                         # >0 头低于肩(俯卧撑头下垂)
    head_fwd = float(abs(nose_x - sho_mid[0, 0]) / tl)                       # 头相对肩的水平前探

    return feats, {
        "knee_L": a_knee_L, "knee_R": a_knee_R,
        "hip_L": a_hip_L,   "hip_R": a_hip_R,
        "elbow_L": a_elb_L, "elbow_R": a_elb_R,
        "shoulder_L": a_sho_L, "shoulder_R": a_sho_R,
        "torso_tilt": torso_tilt,
        "ankle_dx": round(ankle_dx, 3),
        "wrist_above": round(wrist_above, 3),
        "head_drop": round(head_drop, 3),
        "head_fwd": round(head_fwd, 3),
    }


def _score_squat(ang):
    knee = (ang["knee_L"] + ang["knee_R"]) / 2
    torso = abs(ang["torso_tilt"])
    s, fb = 100, []
    if knee < 80: s -= 25; fb.append("蹲太深")
    elif knee > 150: s -= 30; fb.append("蹲不够深, 大腿要平行地面")
    elif knee > 130: s -= 10; fb.append("再蹲深一点")
    if torso > 60: s -= 20; fb.append("躯干过度前倾, 收紧核心")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_pushup(ang):
    elb = (ang["elbow_L"] + ang["elbow_R"]) / 2
    s, fb = 100, []
    if elb > 150: s -= 30; fb.append("肘没弯下去, 要触底")
    elif elb < 70: s -= 10; fb.append("肘弯太多")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_plank(ang):
    hip = (ang["hip_L"] + ang["hip_R"]) / 2
    s, fb = 100, []
    if hip < 160: s -= 25; fb.append("臀部翘起或塌下, 保持一条线")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_lunge(ang):
    diff = abs(ang["knee_L"] - ang["knee_R"])
    s, fb = 100, []
    if diff < 20: s -= 30; fb.append("两腿膝盖应有明显角度差")
    front = min(ang["knee_L"], ang["knee_R"])
    if front > 110: s -= 15; fb.append("前膝再弯一点")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_jack(ang):
    sho = (ang["shoulder_L"] + ang["shoulder_R"]) / 2
    s, fb = 100, []
    if sho < 90: s -= 20; fb.append("手要举过头顶")
    return max(0, s), "; ".join(fb) if fb else "标准!"


def _score_bicep_curl(ang):
    """弯举: 胘应从 180° 弯到 ≈50°, 肩不动 (肩角保持 ~20°)."""
    elb = (ang.get("elbow_L", 180) + ang.get("elbow_R", 180)) / 2
    sho = (ang.get("shoulder_L", 30) + ang.get("shoulder_R", 30)) / 2
    s, fb = 100, []
    if elb > 160: s -= 20; fb.append("手臂未弯起, 完整收缩")
    if elb < 30: s -= 15; fb.append("胘弯得太过, 容易损胘")
    if sho > 70: s -= 25; fb.append("肩膁在发力, 固定肩骨, 只动胘")
    return max(0, s), "; ".join(fb) if fb else "标准弯举!"


def _score_shoulder_press(ang):
    """肩推: 手从肩高推过头顶, 胘从 90° 进行到 180°, 肩从 70° 到 170°."""
    elb = (ang.get("elbow_L", 90) + ang.get("elbow_R", 90)) / 2
    sho = (ang.get("shoulder_L", 90) + ang.get("shoulder_R", 90)) / 2
    torso = abs(ang.get("torso_tilt", 0))
    s, fb = 100, []
    if elb < 80: s -= 30; fb.append("未推到位, 手臂要完全伸直")
    if sho < 100: s -= 20; fb.append("手未过头顶")
    if torso > 30: s -= 15; fb.append("躯干反弓, 胸腔不要前頂")
    return max(0, s), "; ".join(fb) if fb else "肩推到位!"


FORM_RULES = {
    "squat": _score_squat, "push_up": _score_pushup, "plank": _score_plank,
    "lunge": _score_lunge, "jumping_jack": _score_jack,
    "bicep_curl": _score_bicep_curl, "shoulder_press": _score_shoulder_press,
}

# ============ 人体有效性门禁 ============
# MediaPipe 对只拍到脸/半身的画面也会"幻觉"出全部 33 个关节点(可见度很低),
# 用幻觉坐标算角度 → 扣分制规则不触发 → 满分。必须先校验关键关节可见度。

# 各动作评分所必需的关节 (MediaPipe id)
REQUIRED_VISIBLE = {
    "squat":          [23, 24, 25, 26, 27, 28],          # 髋/膝/踝
    "lunge":          [23, 24, 25, 26, 27, 28],
    "jumping_jack":   [11, 12, 13, 14, 23, 24],          # 肩/肘/髋
    "push_up":        [11, 12, 13, 14, 15, 16, 23, 24],  # 肩/肘/腕/髋
    "plank":          [11, 12, 23, 24, 25, 26],
    "bicep_curl":     [11, 12, 13, 14, 15, 16],
    "shoulder_press": [11, 12, 13, 14, 15, 16],
}
CORE_IDS = [11, 12, 23, 24]   # 双肩 + 双髋: 任何动作都必须看到躯干
MIN_VIS = 0.5
LOW_QUALITY = 0.7             # 可见度均值低于此值时分数封顶
LOW_QUALITY_CAP = 80
INVALID_FEEDBACK = "未检测到完整人体, 请让身体进入画面"


def check_pose_validity(vis_33, exercise=None):
    """根据关节可见度判断该帧能否用于评分/计数.

    Args:
        vis_33: 长度 33 的可见度数组 (0-1).
        exercise: 动作名; 未知动作只校验躯干核心关节.
    Returns:
        (valid: bool, quality: float)  quality = 必需关节可见度均值
    """
    vis = np.asarray(vis_33, dtype=np.float32)
    if vis.shape[0] < 33:
        return False, 0.0
    core_ok = float(vis[CORE_IDS].mean()) >= MIN_VIS
    req = REQUIRED_VISIBLE.get(exercise or "", CORE_IDS)
    visible = int(sum(1 for i in req if vis[i] >= MIN_VIS))
    req_ok = visible >= max(1, int(len(req) * 0.7))
    quality = float(np.mean([vis[i] for i in req]))
    return (core_ok and req_ok), quality


# ============ 体态匹配门禁 ============
# 防止"做着深蹲却给俯卧撑计数": 俯卧撑/平板是趴姿(躯干接近水平, |torso_tilt| 大),
# 其余动作是站姿(躯干竖直, |torso_tilt| 小). 体态与目标动作不符 → 不计数.
_PRONE_EXERCISES = {"push_up", "plank"}     # 趴姿
_PRONE_MIN_TILT = 50.0                       # 趴姿: |torso_tilt| 需 >= 此值
_UPRIGHT_MAX_TILT = 62.0                     # 站姿: |torso_tilt| 需 <= 此值


def posture_matches_exercise(angles, exercise) -> bool:
    """该帧体态是否与目标动作一致 (趴/站). torso_tilt 缺失时放行(不误杀)."""
    tilt = angles.get("torso_tilt") if angles else None
    if tilt is None:
        return True
    a = abs(float(tilt))
    if exercise in _PRONE_EXERCISES:
        return a >= _PRONE_MIN_TILT
    return a <= _UPRIGHT_MAX_TILT


def apply_score_gate(score, feedback, valid, quality):
    """门禁后处理: 无效帧不给分; 低可见度封顶."""
    if not valid:
        return None, INVALID_FEEDBACK
    if quality < LOW_QUALITY and score is not None:
        capped = min(int(score), LOW_QUALITY_CAP)
        if capped < score:
            feedback = (feedback + "; " if feedback and feedback != "标准!" else "") + "部分关节可见度低"
        return capped, feedback
    return score, feedback


class PoseEngine:
    def __init__(self, classifier_path=CLASSIFIER_PATH, landmarker_path=LANDMARKER_PATH):
        opts = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=landmarker_path),
            running_mode=RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.landmarker = PoseLandmarker.create_from_options(opts)
        self.clf = None
        self.labels = None
        if os.path.exists(classifier_path):
            with open(classifier_path, "rb") as f:
                pkg = pickle.load(f)
            self.clf = pkg["model"]
            self.labels = pkg["labels"]
            log.info(f"loaded classifier, classes={self.labels}")

    def infer_from_image(self, image_bgr):
        t0 = time.time()
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = self.landmarker.detect(mp_image)
        if not res.pose_landmarks:
            return {"detected": False, "infer_ms": int((time.time()-t0)*1000)}
        lm = res.pose_landmarks[0]  # 第一个人
        arr = np.array([[p.x, p.y, p.z, p.visibility] for p in lm], dtype=np.float32)
        feats, angles = make_features_single(arr)
        out = {
            "detected": True,
            "landmarks": [{"x": float(p.x), "y": float(p.y), "z": float(p.z), "v": float(p.visibility)} for p in lm],
            "angles": {k: round(v, 1) for k, v in angles.items()},
            "infer_ms": int((time.time()-t0)*1000),
        }
        valid, quality = check_pose_validity(arr[:, 3], None)
        out["pose_valid"] = bool(valid)
        out["vis_quality"] = round(quality, 2)
        if self.clf is not None:
            probs = self.clf.predict_proba(feats)[0]
            top_id = int(np.argmax(probs))
            exercise = self.labels[top_id]
            out["exercise"] = exercise
            out["confidence"] = float(probs[top_id])
            out["all_probs"] = {self.labels[i]: float(p) for i, p in enumerate(probs)}
            # 针对识别出的动作重算有效性 (不同动作必需关节不同)
            valid, quality = check_pose_validity(arr[:, 3], exercise)
            out["pose_valid"] = bool(valid)
            out["vis_quality"] = round(quality, 2)
            rule = FORM_RULES.get(exercise)
            if rule:
                score, fb = rule(angles)
                score, fb = apply_score_gate(int(score), fb, valid, quality)
                out["form_score"] = score
                out["feedback"] = fb
        return out

    def infer_from_landmarks(self, landmarks_33x4):
        """已有 (33,4) landmarks 时直接走分类 (跳过 mediapipe)."""
        arr = np.asarray(landmarks_33x4, dtype=np.float32).reshape(33, 4)
        feats, angles = make_features_single(arr)
        out = {
            "detected": True,
            "angles": {k: round(v, 1) for k, v in angles.items()},
        }
        valid, quality = check_pose_validity(arr[:, 3], None)
        out["pose_valid"] = bool(valid)
        out["vis_quality"] = round(quality, 2)
        if self.clf is not None:
            probs = self.clf.predict_proba(feats)[0]
            top_id = int(np.argmax(probs))
            exercise = self.labels[top_id]
            out["exercise"] = exercise
            out["confidence"] = float(probs[top_id])
            out["all_probs"] = {self.labels[i]: float(p) for i, p in enumerate(probs)}
            valid, quality = check_pose_validity(arr[:, 3], exercise)
            out["pose_valid"] = bool(valid)
            out["vis_quality"] = round(quality, 2)
            rule = FORM_RULES.get(exercise)
            if rule:
                score, fb = rule(angles)
                score, fb = apply_score_gate(int(score), fb, valid, quality)
                out["form_score"] = score
                out["feedback"] = fb
        return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from synth_dataset import GENERATORS
    print("=== Self-test: 用合成数据测每类 1 帧 ===")
    eng = PoseEngine()
    for label, gen in GENERATORS.items():
        arr = gen(T=30, fps=10, noise=0.01)
        res = eng.infer_from_landmarks(arr[15])
        ok = "✅" if res.get("exercise") == label else "❌"
        print(f"  {ok} truth={label:14s} pred={res.get('exercise'):14s} conf={res.get('confidence',0):.2f} score={res.get('form_score','-')} fb={res.get('feedback','-')[:40]}")


# ============================================================
# YOLO26 后端 (评分V2 第二阶段)
# 实测本机 CPU: yolo26n-pose 109ms/帧, yolo26m-pose 480ms/帧;
# 原生多人检测, 主体锁定 = bbox 面积 × 画面中心接近度.
# ============================================================

# COCO-17 → MediaPipe-33 槽位映射 (未覆盖槽位 visibility=0)
COCO_TO_MP = {
    0: 0,    # nose
    1: 2, 2: 5,      # eyes
    3: 7, 4: 8,      # ears
    5: 11, 6: 12,    # shoulders
    7: 13, 8: 14,    # elbows
    9: 15, 10: 16,   # wrists
    11: 23, 12: 24,  # hips
    13: 25, 14: 26,  # knees
    15: 27, 16: 28,  # ankles
}


def _classify_and_score(arr, angles, out, clf, labels):
    """共用尾段: 有效性门禁 → 分类 → 规则评分. (MediaPipe/YOLO 两后端一致)"""
    valid, quality = check_pose_validity(arr[:, 3], None)
    out["pose_valid"] = bool(valid)
    out["vis_quality"] = round(quality, 2)
    if clf is None:
        return out
    feats, _ = make_features_single(arr)
    probs = clf.predict_proba(feats)[0]
    top_id = int(np.argmax(probs))
    exercise = labels[top_id]
    out["exercise"] = exercise
    out["confidence"] = float(probs[top_id])
    out["all_probs"] = {labels[i]: float(p) for i, p in enumerate(probs)}
    valid, quality = check_pose_validity(arr[:, 3], exercise)
    out["pose_valid"] = bool(valid)
    out["vis_quality"] = round(quality, 2)
    rule = FORM_RULES.get(exercise)
    if rule:
        score, fb = rule(angles)
        score, fb = apply_score_gate(int(score), fb, valid, quality)
        out["form_score"] = score
        out["feedback"] = fb
    return out


class PoseEngineYolo26:
    """YOLO26-pose 后端: 多人检测 + 主体锁定, 输出契约与 PoseEngine 完全一致.

    额外字段: persons (本帧人数), backend ("yolo26").
    注意: 动作分类器是用 MediaPipe 33 点合成数据训的, COCO 升格后
    16 个槽位为零值, 分类置信度会偏低 — 训练流程以用户选择的目标动作
    为准 (route 的 exercise_hint), 不受影响.
    """

    def __init__(self, model_name=None, classifier_path=CLASSIFIER_PATH):
        from ultralytics import YOLO
        self.model_name = model_name or os.environ.get("POSE_YOLO_MODEL", "yolo26n-pose.pt")
        self.model = YOLO(self.model_name)
        self.clf = None
        self.labels = None
        if os.path.exists(classifier_path):
            with open(classifier_path, "rb") as f:
                pkg = pickle.load(f)
            self.clf = pkg["model"]
            self.labels = pkg["labels"]
        log.info(f"PoseEngineYolo26 ready: {self.model_name}, classifier={'yes' if self.clf else 'no'}")

    @staticmethod
    def _pick_primary(boxes_xyxy, w, h):
        """主体锁定: 面积 × 中心接近度加权, 选'正在锻炼的那个人'."""
        best_i, best_score = 0, -1.0
        cx0, cy0 = w / 2.0, h / 2.0
        for i, (x1, y1, x2, y2) in enumerate(boxes_xyxy):
            area = max(0.0, (x2 - x1)) * max(0.0, (y2 - y1)) / (w * h + 1e-6)
            bx, by = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            dist = ((bx - cx0) ** 2 + (by - cy0) ** 2) ** 0.5 / ((w ** 2 + h ** 2) ** 0.5 / 2)
            score = area * (1.0 - 0.5 * dist)
            if score > best_score:
                best_score, best_i = score, i
        return best_i

    def infer_from_image(self, image_bgr):
        t0 = time.time()
        h, w = image_bgr.shape[:2]
        res = self.model.predict(image_bgr, imgsz=640, verbose=False)[0]
        n = 0 if res.boxes is None else len(res.boxes)
        if n == 0 or res.keypoints is None:
            return {"detected": False, "persons": 0, "backend": "yolo26",
                    "infer_ms": int((time.time() - t0) * 1000)}

        idx = self._pick_primary(res.boxes.xyxy.cpu().numpy(), w, h)
        kxy = res.keypoints.xyn[idx].cpu().numpy()          # (17,2) 归一化
        if res.keypoints.conf is not None:
            kconf = res.keypoints.conf[idx].cpu().numpy()   # (17,)
        else:
            kconf = np.ones(17, dtype=np.float32)

        arr = np.zeros((33, 4), dtype=np.float32)
        for coco_i, mp_i in COCO_TO_MP.items():
            arr[mp_i, 0] = kxy[coco_i, 0]
            arr[mp_i, 1] = kxy[coco_i, 1]
            arr[mp_i, 2] = 0.0
            arr[mp_i, 3] = kconf[coco_i]

        _, angles = make_features_single(arr)
        out = {
            "detected": True,
            "persons": int(n),
            "backend": "yolo26",
            "landmarks": [{"x": float(arr[i, 0]), "y": float(arr[i, 1]),
                           "z": 0.0, "v": float(arr[i, 3])} for i in range(33)],
            "angles": {k: round(v, 1) for k, v in angles.items()},
            "infer_ms": int((time.time() - t0) * 1000),
        }
        return _classify_and_score(arr, angles, out, self.clf, self.labels)

    def infer_from_landmarks(self, landmarks_33x4):
        arr = np.asarray(landmarks_33x4, dtype=np.float32).reshape(33, 4)
        _, angles = make_features_single(arr)
        out = {"detected": True, "backend": "yolo26",
               "angles": {k: round(v, 1) for k, v in angles.items()}}
        return _classify_and_score(arr, angles, out, self.clf, self.labels)


def create_engine(backend=None):
    """工厂: POSE_BACKEND=yolo26|mediapipe (默认 yolo26, 失败回退 MediaPipe)."""
    backend = (backend or os.environ.get("POSE_BACKEND", "yolo26")).lower()
    if backend in ("yolo26", "yolo"):
        try:
            return PoseEngineYolo26()
        except Exception as e:
            log.warning(f"yolo26 backend init failed ({e}), falling back to mediapipe")
    return PoseEngine()
