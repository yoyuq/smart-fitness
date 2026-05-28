"""train_classifier.py - 在提取好的 landmark 序列上训练动作分类器.

方法 1 (MVP): 在每一帧上跑 RandomForest, 输入是 33*4=132 维特征 + 衍生角度.
方法 2 (升级): 滑窗 (T=30 帧) + 简单 1D-CNN 或 LSTM.

先 MVP, 跑通后再升级.
"""
import os, glob, json, pickle
import numpy as np
from collections import Counter

LM_DIR = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\landmarks"
MODEL_DIR = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\models"
os.makedirs(MODEL_DIR, exist_ok=True)

LABELS = ["squat", "push_up", "plank", "lunge", "jumping_jack", "bicep_curl", "shoulder_press"]
LABEL2ID = {n: i for i, n in enumerate(LABELS)}

# MediaPipe BlazePose landmark 索引
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_SHO, R_SHO = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16
NOSE = 0


def angle(a, b, c):
    """三点角度 (顶点 b)."""
    ba = a - b
    bc = c - b
    cos = np.sum(ba * bc, axis=-1) / (np.linalg.norm(ba, axis=-1) * np.linalg.norm(bc, axis=-1) + 1e-8)
    cos = np.clip(cos, -1, 1)
    return np.arccos(cos) * 180.0 / np.pi


def make_features(landmarks_TJD):
    """landmarks: (T, 33, 4) -> features: (T, F)"""
    lm = landmarks_TJD  # (T, 33, 4)
    xyz = lm[..., :3]  # (T, 33, 3)
    vis = lm[..., 3]   # (T, 33)

    # 归一化: 以髋中点为原点, 髋宽为单位
    hip_mid = (xyz[:, L_HIP] + xyz[:, R_HIP]) / 2  # (T, 3)
    sho_mid = (xyz[:, L_SHO] + xyz[:, R_SHO]) / 2
    torso_len = np.linalg.norm(sho_mid - hip_mid, axis=-1, keepdims=True) + 1e-6  # (T,1)
    xyz_n = (xyz - hip_mid[:, None, :]) / torso_len[:, None, :]  # (T, 33, 3)

    # 角度特征
    a_knee_L = angle(xyz[:, L_HIP], xyz[:, L_KNEE], xyz[:, L_ANKLE])
    a_knee_R = angle(xyz[:, R_HIP], xyz[:, R_KNEE], xyz[:, R_ANKLE])
    a_hip_L  = angle(xyz[:, L_SHO], xyz[:, L_HIP], xyz[:, L_KNEE])
    a_hip_R  = angle(xyz[:, R_SHO], xyz[:, R_HIP], xyz[:, R_KNEE])
    a_elb_L  = angle(xyz[:, L_SHO], xyz[:, L_ELB], xyz[:, L_WRI])
    a_elb_R  = angle(xyz[:, R_SHO], xyz[:, R_ELB], xyz[:, R_WRI])
    a_sho_L  = angle(xyz[:, L_ELB], xyz[:, L_SHO], xyz[:, L_HIP])
    a_sho_R  = angle(xyz[:, R_ELB], xyz[:, R_SHO], xyz[:, R_HIP])

    # 整体姿态指标
    # 躯干倾角 (相对于垂直)
    torso_vec = sho_mid - hip_mid  # (T, 3)
    torso_tilt = np.arctan2(torso_vec[:, 0], -torso_vec[:, 1]) * 180.0 / np.pi  # 0=直立
    # 髋高度 (Y 反向, 越小越蹲)
    hip_y = hip_mid[:, 1]
    # 肩高 - 髋高 = 躯干水平度 (用 abs Y 差)
    sho_hip_y = abs(sho_mid[:, 1] - hip_mid[:, 1])

    # 平均可见度 (检测置信)
    vis_mean = vis.mean(axis=-1)

    # 关键关节 z (深度)
    hip_z = hip_mid[:, 2]

    # 拼起来: 132 (raw xyz_n flat) + 12 (各角度+元特征) = 144
    flat = xyz_n.reshape(xyz_n.shape[0], -1)  # (T, 99)
    extras = np.stack([a_knee_L, a_knee_R, a_hip_L, a_hip_R, a_elb_L, a_elb_R,
                       a_sho_L, a_sho_R, torso_tilt, hip_y, sho_hip_y, vis_mean], axis=-1)  # (T, 12)
    return np.concatenate([flat, extras], axis=-1)  # (T, 111)


def load_all():
    X_all, y_all, meta = [], [], []
    for f in sorted(glob.glob(os.path.join(LM_DIR, "*.npz"))):
        d = np.load(f, allow_pickle=True)
        lm = d["landmarks"]  # (T, 33, 4)
        if lm.shape[0] < 5:
            continue
        # 过滤掉全零(未检测)帧, 至少需 vis 平均 > 0.3
        vis_mean = lm[..., 3].mean(axis=-1)
        keep = vis_mean > 0.3
        if keep.sum() < 5:
            print(f"  drop {f} (too few detected)")
            continue
        lm = lm[keep]
        feats = make_features(lm)  # (T', F)
        label = int(d["label"])
        X_all.append(feats)
        y_all.append(np.full(feats.shape[0], label, dtype=np.int64))
        meta.append({"file": os.path.basename(f), "T": int(feats.shape[0]), "label": int(label)})
    X = np.concatenate(X_all, axis=0)
    y = np.concatenate(y_all, axis=0)
    return X, y, meta


def main():
    print("loading...")
    X, y, meta = load_all()
    print(f"X={X.shape} y={y.shape}")
    print("label distribution:", Counter(y.tolist()))
    if len(set(y.tolist())) < 2:
        print("ERROR: 需要 >=2 类才能训练")
        return

    # split
    from sklearn.model_selection import train_test_split
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import classification_report, confusion_matrix

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print(f"train={X_tr.shape} test={X_te.shape}")

    clf = RandomForestClassifier(n_estimators=200, max_depth=20, n_jobs=-1, random_state=42, class_weight="balanced")
    clf.fit(X_tr, y_tr)

    train_acc = clf.score(X_tr, y_tr)
    test_acc  = clf.score(X_te, y_te)
    print(f"\ntrain acc = {train_acc:.3f}")
    print(f"test  acc = {test_acc:.3f}")

    print("\n=== classification report ===")
    label_names = [LABELS[i] for i in sorted(set(y.tolist()))]
    print(classification_report(y_te, clf.predict(X_te), target_names=label_names))

    # save
    out = os.path.join(MODEL_DIR, "pose_classifier.pkl")
    with open(out, "wb") as f:
        pickle.dump({"model": clf, "labels": LABELS, "feature_dim": X.shape[1]}, f)
    print(f"\nsaved {out}")

    # 同时存一份 meta
    with open(os.path.join(MODEL_DIR, "train_meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "train_acc": float(train_acc),
            "test_acc": float(test_acc),
            "n_train": int(len(y_tr)),
            "n_test": int(len(y_te)),
            "labels": LABELS,
            "clips_used": meta,
        }, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
