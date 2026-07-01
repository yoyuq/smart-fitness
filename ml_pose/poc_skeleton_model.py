"""poc_skeleton_model.py — A 层 PoC: 学习式骨架动作识别 vs 写死/RandomForest

目的: 验证"让模型从骨架判动作"这条路, 并给出诚实评测(对比现有 RF):
  1) RF + 帧级划分     —— 复现现有 ~98%(暴露数据泄露)
  2) RF + 片段级划分   —— 同一 clip 不跨训练/测试(诚实基线, clip 多数投票)
  3) 时序小模型(TCN) + 片段级划分 —— "模型判"的诚实数字
  4) 合成->真实 跨域   —— 在 210 合成上训, 在 14 真实上测(最关键: 合成98%到底虚不虚)

仅用现有 datasets/landmarks/*.npz, 不依赖任何外部数据集。CPU 即可。
"""
import os, glob, json
import numpy as np

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_classifier import make_features, LABELS  # 复用与 RF 完全相同的特征, 保证公平

LM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "landmarks")
T_FIX = 48          # 每个 clip 统一采样帧数
SEED = 42
rng = np.random.default_rng(SEED)


def load_clips():
    """返回 list[dict]: feats(T',F), label, is_real, name"""
    clips = []
    for f in sorted(glob.glob(os.path.join(LM_DIR, "*.npz"))):
        d = np.load(f, allow_pickle=True)
        lm = d["landmarks"]
        if lm.shape[0] < 5:
            continue
        vis_mean = lm[..., 3].mean(axis=-1)
        keep = vis_mean > 0.3
        if keep.sum() < 5:
            continue
        feats = make_features(lm[keep])            # (T', F) 与 RF 同特征
        clips.append({
            "feats": feats.astype(np.float32),
            "label": int(d["label"]),
            "is_real": os.path.basename(f).startswith("real_"),
            "name": os.path.basename(f),
        })
    return clips


def sample_fixed(feats, T=T_FIX):
    """均匀采样/重复到固定 T 帧 -> (T, F)"""
    n = feats.shape[0]
    idx = np.linspace(0, n - 1, T).round().astype(int)
    return feats[idx]


def majority_vote(preds):
    vals, counts = np.unique(preds, return_counts=True)
    return int(vals[counts.argmax()])


# ---------------- RandomForest 基线 ----------------
def rf_frame_level(clips):
    """复现现有做法: 所有帧汇总 + 帧级 stratified 划分(有泄露)"""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    X = np.concatenate([c["feats"] for c in clips], 0)
    y = np.concatenate([np.full(c["feats"].shape[0], c["label"]) for c in clips], 0)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=SEED, stratify=y)
    clf = RandomForestClassifier(n_estimators=200, max_depth=20, n_jobs=-1,
                                 random_state=SEED, class_weight="balanced")
    clf.fit(Xtr, ytr)
    return clf.score(Xte, yte)


def rf_clip_level(train_clips, test_clips):
    """诚实: clip 不跨集; 训练用训练 clip 的全部帧, 测试用每个 clip 的帧多数投票"""
    from sklearn.ensemble import RandomForestClassifier
    Xtr = np.concatenate([c["feats"] for c in train_clips], 0)
    ytr = np.concatenate([np.full(c["feats"].shape[0], c["label"]) for c in train_clips], 0)
    clf = RandomForestClassifier(n_estimators=200, max_depth=20, n_jobs=-1,
                                 random_state=SEED, class_weight="balanced")
    clf.fit(Xtr, ytr)
    correct = 0
    per_pred = []
    for c in test_clips:
        pred = majority_vote(clf.predict(c["feats"]))
        per_pred.append((c["label"], pred, c["name"]))
        correct += int(pred == c["label"])
    return correct / max(len(test_clips), 1), per_pred


# ---------------- 时序小模型 (TCN) ----------------
def build_tcn(F, n_cls):
    import torch.nn as nn
    return nn.Sequential(
        nn.Conv1d(F, 64, 3, padding=1), nn.ReLU(), nn.BatchNorm1d(64),
        nn.Conv1d(64, 64, 3, padding=2, dilation=2), nn.ReLU(), nn.BatchNorm1d(64),
        nn.Conv1d(64, 64, 3, padding=4, dilation=4), nn.ReLU(),
        nn.AdaptiveAvgPool1d(1), nn.Flatten(),
        nn.Dropout(0.3), nn.Linear(64, n_cls),
    )


def tcn_eval(train_clips, test_clips, F, epochs=60, tag=""):
    import torch, torch.nn as nn
    torch.manual_seed(SEED)

    def to_tensor(clips):
        X = np.stack([sample_fixed(c["feats"]) for c in clips])      # (N,T,F)
        X = np.transpose(X, (0, 2, 1))                               # (N,F,T)
        y = np.array([c["label"] for c in clips])
        # 标准化(按训练集统计)
        return X.astype(np.float32), y

    Xtr, ytr = to_tensor(train_clips)
    Xte, yte = to_tensor(test_clips)
    mu, sd = Xtr.mean((0, 2), keepdims=True), Xtr.std((0, 2), keepdims=True) + 1e-6
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd

    Xtr_t = torch.tensor(Xtr); ytr_t = torch.tensor(ytr)
    Xte_t = torch.tensor(Xte)
    model = build_tcn(F, len(LABELS))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss()
    n = len(Xtr_t); bs = 32
    model.train()
    for ep in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            out = model(Xtr_t[b])
            loss = lossf(out, ytr_t[b])
            loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        pred = model(Xte_t).argmax(1).numpy()
    acc = float((pred == yte).mean())
    return acc, list(zip(yte.tolist(), pred.tolist(), [c["name"] for c in test_clips]))


def main():
    clips = load_clips()
    F = clips[0]["feats"].shape[1]
    n_real = sum(c["is_real"] for c in clips)
    print(f"clips={len(clips)}  feature_dim={F}  real={n_real}  synth={len(clips)-n_real}\n")

    # 片段级划分(分层, 不跨集)
    from sklearn.model_selection import train_test_split
    idx = np.arange(len(clips)); labels = [c["label"] for c in clips]
    tr_i, te_i = train_test_split(idx, test_size=0.25, random_state=SEED, stratify=labels)
    train_clips = [clips[i] for i in tr_i]; test_clips = [clips[i] for i in te_i]

    print("=" * 56)
    print("实验 1  RF + 帧级划分 (现有做法, 有数据泄露)")
    acc1 = rf_frame_level(clips)
    print(f"        frame-level test acc = {acc1*100:.1f}%   <- 这就是'合成98%'的来源\n")

    print("实验 2  RF + 片段级划分 (诚实基线, clip 多数投票)")
    acc2, _ = rf_clip_level(train_clips, test_clips)
    print(f"        clip-level  test acc = {acc2*100:.1f}%\n")

    print("实验 3  时序小模型 TCN + 片段级划分 ('模型判'诚实数字)")
    acc3, _ = tcn_eval(train_clips, test_clips, F)
    print(f"        clip-level  test acc = {acc3*100:.1f}%\n")

    print("=" * 56)
    print("实验 4  跨域: 在 210 合成上训 -> 在 14 真实上测 (最关键)")
    synth = [c for c in clips if not c["is_real"]]
    real = [c for c in clips if c["is_real"]]
    if real:
        accx_rf, pr_rf = rf_clip_level(synth, real)
        accx_tcn, pr_tcn = tcn_eval(synth, real, F, tag="xdom")
        print(f"        RF  合成->真实 acc = {accx_rf*100:.1f}%  ({len(real)} 真实 clip)")
        print(f"        TCN 合成->真实 acc = {accx_tcn*100:.1f}%")
        print("\n        真实样本逐条(label -> RF / TCN):")
        idn = {i: n for i, n in enumerate(LABELS)}
        prt = {n: (l, p) for (l, p, n) in pr_tcn}
        for (l, p, nm) in pr_rf:
            tl, tp = prt[nm]
            mk_rf = "OK " if p == l else "X  "; mk_tcn = "OK " if tp == l else "X  "
            print(f"          {nm:34s} {idn[l]:14s} RF {idn[p]:14s}{mk_rf}  TCN {idn[tp]:14s}{mk_tcn}")

    print("\n" + "=" * 56)
    print("小结")
    print(f"  泄露虚高:   RF 帧级 {acc1*100:.1f}%  ->  诚实 clip 级 RF {acc2*100:.1f}% / TCN {acc3*100:.1f}%")
    if real:
        print(f"  合成->真实: RF {accx_rf*100:.1f}%  TCN {accx_tcn*100:.1f}%  <- 真实泛化才是底牌")
    res = {"rf_frame_leaky": acc1, "rf_clip_honest": acc2, "tcn_clip_honest": acc3}
    if real:
        res["rf_synth2real"] = accx_rf; res["tcn_synth2real"] = accx_tcn
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poc_skeleton_results.json")
    json.dump(res, open(out, "w"), indent=2)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
