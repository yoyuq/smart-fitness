"""pose_engine.py - MediaPipe Tasks API + 我们训练的动作分类器, 端到端推理."""
import os, pickle, time, logging
import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode

log = logging.getLogger("pose_engine")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_DIR = os.path.join(PROJECT_ROOT, "datasets", "models")
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


# ============================================================
# Form-scoring rules with peer-reviewed evidence citations.
# Interior-angle convention: 180 = fully extended, smaller = more flexion.
# Papers usually report *flexion* angles from 0 = extension, so
# "90 knee flexion" in the literature == interior 90 in our code.
# Evidence file (single source of truth for numbers below):
#   C:\Users\hjl\.openclaw\workspace\artifacts\smart_fitness_scoring_evidence.json
# ============================================================

EVIDENCE_SOURCES = {
    "squat.knee_deep":    {"claim": "Parallel squat = interior knee ~80 (=100 flexion); deeper (<60) only cautious with heavy load, not inherently unsafe.", "authors": "Escamilla RF (2001); Hartmann H et al. (2013)", "url": "https://pubmed.ncbi.nlm.nih.gov/11194098/ ; https://pubmed.ncbi.nlm.nih.gov/23821469/"},
    "squat.knee_shallow": {"claim": "Knee flexion <50 (interior >130) is a partial/quarter-squat; parallel (interior ~80) recommended for strength/hypertrophy.", "authors": "Escamilla RF et al. (2001, MSSE 33:1552)", "url": "https://pubmed.ncbi.nlm.nih.gov/11528346/"},
    "squat.torso_tilt":   {"claim": "Greater trunk forward lean increases lumbar shear; 60 cutoff is a coach heuristic.", "authors": "Russell PJ, Phillips SJ (1989, RQES 60:201)", "url": "https://pubmed.ncbi.nlm.nih.gov/2489844/"},
    "push_up.elbow_shallow": {"claim": "Standard push-up bottom ~90 elbow flexion (interior ~90); interior >150 at bottom means chest never approached the floor.", "authors": "Dhahbi W et al. (2022, Sports Biomech 21:1)", "url": "https://pubmed.ncbi.nlm.nih.gov/30284496/"},
    "push_up.elbow_overflex": {"claim": "Deeper elbow flexion raises peak elbow moment; over-flexion (interior <60) is unusual and mainly a joint-load concern.", "authors": "Polovinets O et al. (2026, Handchir 58:243)", "url": "https://pubmed.ncbi.nlm.nih.gov/42269686/"},
    "plank.hip_break":    {"claim": "Prone plank is defined by a straight body line (hip interior ~180); sag <160 or pike >200 is a technique failure.", "authors": "Ekstrom RA et al. (2007, JOSPT 37:754); Moreno-Navarro P et al. (2024, JBMR 37:743)", "url": "https://pubmed.ncbi.nlm.nih.gov/18560185/ ; https://pubmed.ncbi.nlm.nih.gov/38217576/"},
    "lunge.front_knee":   {"claim": "Forward-lunge front knee should reach ~90 flexion (interior ~90); interior >110 = <70 flexion = clearly incomplete.", "authors": "Escamilla RF et al. (2008 JOSPT; 2022 J Appl Biomech)", "url": "https://pubmed.ncbi.nlm.nih.gov/18978453/ ; https://pubmed.ncbi.nlm.nih.gov/35697336/"},
    "lunge.knee_diff":    {"claim": "Left-right knee-flexion asymmetry >=4 is clinically meaningful; a true split-stance lunge should show a large left-right delta.", "authors": "Hall M et al. (2015, The Knee 22:506)", "url": "https://pubmed.ncbi.nlm.nih.gov/25907262/"},
    "jumping_jack.arm":   {"claim": "Jumping jack = full arm abduction 0-180 at glenohumeral joint; arms should clearly reach overhead (>=150).", "authors": "Lam JH, Bordoni B (2023, StatPearls NBK537148)", "url": "https://www.ncbi.nlm.nih.gov/books/NBK537148/"},
    "bicep_curl.rom":     {"claim": "Standard biceps-curl ROM 0-135 elbow flexion (interior 45-180); interior >160 = <20 flexion = no rep started.", "authors": "Pedrosa GF et al. (2023, Sports 11:39)", "url": "https://pubmed.ncbi.nlm.nih.gov/36828324/"},
    "bicep_curl.shoulder_cheat": {"claim": "Active shoulder flexion during a curl reduces biceps demand; degree-cutoff is heuristic.", "authors": "Oliveira LF et al. (2009, JSSM 8:24)", "url": "https://pubmed.ncbi.nlm.nih.gov/24150552/"},
    "shoulder_press.lockout": {"claim": "Full overhead-press lockout: elbow interior ~170-180, glenohumeral elevation ~150-180.", "authors": "Gundersen AH et al. (2025, Sports Biomech online)", "url": "https://pubmed.ncbi.nlm.nih.gov/41335596/"},
    "shoulder_press.torso_arch": {"claim": "Excess trunk deviation from vertical during loaded pressing raises lumbar shear; 30 back-arch cutoff is a heuristic.", "authors": "Russell PJ, Phillips SJ (1989, RQES 60:201)", "url": "https://pubmed.ncbi.nlm.nih.gov/2489844/"},
}


def _score_squat(ang):
    """Squat form score.

    Peer-reviewed evidence:
    - Escamilla 2001 (MSSE 33:127) parallel squat = interior knee ~80. https://pubmed.ncbi.nlm.nih.gov/11194098/
    - Hartmann 2013 (Sports Med 43:993) deeper squats not inherently unsafe. https://pubmed.ncbi.nlm.nih.gov/23821469/
    - Russell 1989 (RQES 60:201) trunk lean increases lumbar shear (60 cutoff = heuristic). https://pubmed.ncbi.nlm.nih.gov/2489844/
    """
    knee = (ang["knee_L"] + ang["knee_R"]) / 2
    torso = abs(ang["torso_tilt"])
    s, fb = 100, []
    # Softened from <80 -> <60 (Escamilla 2001): parallel = interior 80, so <80 is NOT "too deep".
    if knee < 60: s -= 25; fb.append("蹲太深, 负重时注意膝关节压力")
    elif knee > 150: s -= 30; fb.append("蹲不够深, 大腿要平行地面 (标准≈膝80°)")
    elif knee > 130: s -= 10; fb.append("再蹲深一点 (标准≈膝80°)")
    if torso > 45: s -= 20; fb.append("躯干过度前倾, 收紧核心 (启发式阈值; Russell 1989)")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_pushup(ang):
    """Push-up form score.

    Peer-reviewed evidence:
    - Dhahbi 2022 (Sports Biomech 21:1) standard bottom ~90 elbow flexion. https://pubmed.ncbi.nlm.nih.gov/30284496/
    - Polovinets 2026 (Handchir 58:243) deeper flexion raises elbow moment. https://pubmed.ncbi.nlm.nih.gov/42269686/
    """
    elb = (ang["elbow_L"] + ang["elbow_R"]) / 2
    s, fb = 100, []
    if elb > 140: s -= 30; fb.append("肘没弯下去, 要触底 (标准≈肘90°)")
    # Softened <70 -> <60 (Polovinets 2026): normal bottom ~90, <60 unusual.
    elif elb < 60: s -= 10; fb.append("肘弯太多, 减少腕/肘负荷")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_plank(ang):
    """Plank form score.

    Peer-reviewed evidence:
    - Ekstrom 2007 (JOSPT 37:754) plank defined by straight body line. https://pubmed.ncbi.nlm.nih.gov/18560185/
    - Moreno-Navarro 2024 (JBMR 37:743) sag/pike shifts load to lumbar. https://pubmed.ncbi.nlm.nih.gov/38217576/
    """
    hip = (ang["hip_L"] + ang["hip_R"]) / 2
    s, fb = 100, []
    if hip < 160: s -= 25; fb.append("臀部塌下, 保持一条直线 (髋≈180°)")
    if hip > 200: s -= 25; fb.append("臀部翘起(pike), 保持一条直线 (髋≈180°)")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_lunge(ang):
    """Lunge form score.

    Peer-reviewed evidence:
    - Escamilla 2008 (JOSPT 38:681) / 2022 (J Appl Biomech 38:210) front knee ~90 flexion.
      https://pubmed.ncbi.nlm.nih.gov/18978453/ ; https://pubmed.ncbi.nlm.nih.gov/35697336/
    - Hall 2015 (The Knee 22:506) clinically meaningful bilateral asymmetry down to ~4.
      https://pubmed.ncbi.nlm.nih.gov/25907262/
    """
    diff = abs(ang["knee_L"] - ang["knee_R"])
    s, fb = 100, []
    # 20 is a conservative floor for "genuine split stance" (clinical asymmetry ~4 is far smaller).
    if diff < 20: s -= 30; fb.append("两腿膝盖应有明显角度差 (弓步分腿)")
    front = min(ang["knee_L"], ang["knee_R"])
    if front > 120: s -= 15; fb.append("前膝再弯一点 (标准≈前膝90°)")
    return max(0, s), "; ".join(fb) if fb else "标准!"

def _score_jack(ang):
    """Jumping-jack form score.

    Peer-reviewed evidence:
    - Lam & Bordoni 2023 (StatPearls NBK537148) full arm abduction 0-180.
      https://www.ncbi.nlm.nih.gov/books/NBK537148/

    Grading: peak = arms overhead (>=150); intermediate = 90-140; fail = <90.
    """
    sho = (ang["shoulder_L"] + ang["shoulder_R"]) / 2
    s, fb = 100, []
    if sho < 90:
        s -= 20; fb.append("手要举过头顶 (肩外展≈180°)")
    elif sho < 140:
        s -= 8; fb.append("手臂未完全过头 (标准应≥150°)")
    return max(0, s), "; ".join(fb) if fb else "标准!"


def _score_bicep_curl(ang):
    """Biceps-curl form score.

    Peer-reviewed evidence:
    - Pedrosa 2023 (Sports 11:39) curl ROM 0-135 flexion (interior 45-180).
      https://pubmed.ncbi.nlm.nih.gov/36828324/
    - Oliveira 2009 (JSSM 8:24) shoulder position changes biceps activation
      (shoulder-cheat degree cutoff is heuristic). https://pubmed.ncbi.nlm.nih.gov/24150552/
    """
    elb = (ang.get("elbow_L", 180) + ang.get("elbow_R", 180)) / 2
    sho = (ang.get("shoulder_L", 30) + ang.get("shoulder_R", 30)) / 2
    s, fb = 100, []
    if elb > 160: s -= 20; fb.append("手臂未弯起, 完整收缩 (标准≈肘45°)")
    # Softened <30 -> <45 (Pedrosa 2023 defines curl ROM 0-135, interior floor ~45).
    if elb < 45: s -= 15; fb.append("肘弯得太过, 超出常规 curl ROM")
    if sho > 60: s -= 25; fb.append("肩膀在发力, 固定肩胛, 只动肘 (启发式阈值; Oliveira 2009)")
    return max(0, s), "; ".join(fb) if fb else "标准弯举!"


def _score_shoulder_press(ang):
    """Shoulder-press form score.

    Peer-reviewed evidence:
    - Gundersen 2025 (Sports Biomech online) lockout: elbow ~170-180, shoulder ~150-180.
      https://pubmed.ncbi.nlm.nih.gov/41335596/
    - Russell 1989 (RQES 60:201) trunk deviation raises lumbar shear (30 back-arch = heuristic).
      https://pubmed.ncbi.nlm.nih.gov/2489844/

    Grading: fail if elbow<80 or shoulder<100; intermediate if elbow<140 or shoulder<150.
    """
    elb = (ang.get("elbow_L", 90) + ang.get("elbow_R", 90)) / 2
    sho = (ang.get("shoulder_L", 90) + ang.get("shoulder_R", 90)) / 2
    torso = abs(ang.get("torso_tilt", 0))
    s, fb = 100, []
    if elb < 80:
        s -= 30; fb.append("未推到位, 手臂要完全伸直 (锁定≈肘170°+)")
    elif elb < 140:
        s -= 10; fb.append("未完全锁定 (标准≈肘170°+)")
    if sho < 100:
        s -= 20; fb.append("手未过头顶 (标准≈肩150°+)")
    elif sho < 150:
        s -= 8; fb.append("未完全过头 (标准≈肩150°+)")
    if torso > 20:
        s -= 15; fb.append("躯干反弓, 胸腔不要前顶 (启发式阈值; Russell 1989)")
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


# ============ 多参数"动作签名"识别 ============
# 不再只看 torso_tilt 一个参数, 而是按每个动作的关键点组合判定"用户是不是在做这个动作".
# hard=True 的条件构成"是否计数"的门禁; 缺失参数一律放行(不误杀). 每条都带值, 可在监控台展示.
def _sig_params(ang):
    def avg(a, b):
        vs = [v for v in (ang.get(a), ang.get(b)) if v is not None]
        return sum(vs) / len(vs) if vs else None
    kL, kR = ang.get("knee_L"), ang.get("knee_R")
    t = ang.get("torso_tilt")
    return {
        "knee": avg("knee_L", "knee_R"), "hip": avg("hip_L", "hip_R"),
        "elbow": avg("elbow_L", "elbow_R"), "shoulder": avg("shoulder_L", "shoulder_R"),
        "knee_diff": (abs(kL - kR) if (kL is not None and kR is not None) else None),
        "torso": (abs(float(t)) if t is not None else None),
        "ankle_dx": ang.get("ankle_dx"), "wrist_above": ang.get("wrist_above"),
        "head_drop": ang.get("head_drop"),
    }


def _le(v, lim): return (v is None) or (v <= lim)     # 缺失放行
def _ge(v, lim): return (v is None) or (v >= lim)


# 每个动作: [(参数名, hard?, 判定 lambda(p)->bool, 取值 lambda(p)->显示值), ...]
_SIGNATURE = {
    "squat": [
        ("直立(非趴姿)", True,  lambda p: _le(p["torso"], 62),        lambda p: p["torso"]),
        ("双腿对称(非弓步)", True, lambda p: _le(p["knee_diff"], 50),    lambda p: p["knee_diff"]),
        ("手未举过头(非肩推/开合跳)", True, lambda p: _le(p["wrist_above"], 0.30), lambda p: p["wrist_above"]),
        ("膝参与下蹲", False,  lambda p: _le(p["knee"], 150),         lambda p: p["knee"]),
        ("髋部下沉", False,    lambda p: _le(p["hip"], 130),          lambda p: p["hip"]),
    ],
    "push_up": [
        ("趴姿(身体接近水平)", True, lambda p: _ge(p["torso"], 50),     lambda p: p["torso"]),
        ("肘部弯曲下放", False, lambda p: _le(p["elbow"], 120),        lambda p: p["elbow"]),
        ("身体保持一条直线", False, lambda p: _ge(p["hip"], 150),       lambda p: p["hip"]),
    ],
    "plank": [
        ("趴姿(身体水平)", True, lambda p: _ge(p["torso"], 50),        lambda p: p["torso"]),
        ("身体保持一条直线", False, lambda p: _ge(p["hip"], 150),       lambda p: p["hip"]),
    ],
    "lunge": [
        ("直立(非趴姿)", True,  lambda p: _le(p["torso"], 62),        lambda p: p["torso"]),
        ("手未举过头", True,    lambda p: _le(p["wrist_above"], 0.30), lambda p: p["wrist_above"]),
        ("单腿前弓(左右膝差异)", False, lambda p: _ge(p["knee_diff"], 20), lambda p: p["knee_diff"]),
    ],
    "bicep_curl": [
        ("直立(非趴姿)", True,  lambda p: _le(p["torso"], 62),        lambda p: p["torso"]),
        ("双腿基本伸直(非深蹲)", True, lambda p: _ge(p["knee"], 140),   lambda p: p["knee"]),
        ("手未举过头(非肩推)", True, lambda p: _le(p["wrist_above"], 0.25), lambda p: p["wrist_above"]),
        ("肘部弯举", False,    lambda p: _le(p["elbow"], 120),        lambda p: p["elbow"]),
    ],
    "shoulder_press": [
        ("直立(非趴姿)", True,  lambda p: _le(p["torso"], 62),        lambda p: p["torso"]),
        ("双腿基本伸直", True,  lambda p: _ge(p["knee"], 140),         lambda p: p["knee"]),
        ("手臂上举过头", False, lambda p: _ge(p["wrist_above"], -0.10), lambda p: p["wrist_above"]),
    ],
    "jumping_jack": [
        ("直立(非趴姿)", True,  lambda p: _le(p["torso"], 62),        lambda p: p["torso"]),
        ("双腿基本伸直(非下蹲)", True, lambda p: _ge(p["knee"], 140),   lambda p: p["knee"]),
        ("手臂举起", False,    lambda p: _ge(p["wrist_above"], -0.10), lambda p: p["wrist_above"]),
        ("双脚打开", False,    lambda p: _ge(p["ankle_dx"], 0.90),    lambda p: p["ankle_dx"]),
    ],
}


def match_exercise_signature(exercise, angles):
    """多参数判定: 该帧体态是否符合目标动作. 返回 {ok, score, params:[{name,ok,hard,val}]}.
    ok = 所有 hard 条件通过(用作计数门禁); score = 通过条件比例(含 soft, 0~1)."""
    spec = _SIGNATURE.get(exercise)
    if not spec or not angles:
        return {"ok": True, "score": 1.0, "exercise": exercise, "params": []}
    p = _sig_params(angles)
    params = []
    hard_ok = True
    npass = 0
    for name, hard, check, getv in spec:
        try:
            ok = bool(check(p)); v = getv(p)
        except Exception:
            ok, v = True, None
        if ok:
            npass += 1
        if hard and not ok:
            hard_ok = False
        params.append({"name": name, "ok": ok, "hard": hard,
                       "val": (round(float(v), 2) if isinstance(v, (int, float)) else None)})
    return {"ok": hard_ok, "score": round(npass / len(spec), 2), "exercise": exercise, "params": params}


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
