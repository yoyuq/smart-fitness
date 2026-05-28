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
    return feats, {
        "knee_L": a_knee_L, "knee_R": a_knee_R,
        "hip_L": a_hip_L,   "hip_R": a_hip_R,
        "elbow_L": a_elb_L, "elbow_R": a_elb_R,
        "shoulder_L": a_sho_L, "shoulder_R": a_sho_R,
        "torso_tilt": torso_tilt,
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
        if self.clf is not None:
            probs = self.clf.predict_proba(feats)[0]
            top_id = int(np.argmax(probs))
            exercise = self.labels[top_id]
            out["exercise"] = exercise
            out["confidence"] = float(probs[top_id])
            out["all_probs"] = {self.labels[i]: float(p) for i, p in enumerate(probs)}
            rule = FORM_RULES.get(exercise)
            if rule:
                score, fb = rule(angles)
                out["form_score"] = int(score)
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
        if self.clf is not None:
            probs = self.clf.predict_proba(feats)[0]
            top_id = int(np.argmax(probs))
            exercise = self.labels[top_id]
            out["exercise"] = exercise
            out["confidence"] = float(probs[top_id])
            out["all_probs"] = {self.labels[i]: float(p) for i, p in enumerate(probs)}
            rule = FORM_RULES.get(exercise)
            if rule:
                score, fb = rule(angles)
                out["form_score"] = int(score)
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
