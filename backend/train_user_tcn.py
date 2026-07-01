# -*- coding: utf-8 -*-
"""用用户标注训练 TCN 三分类：合格 / 太浅 / 太深。

当前阶段只识别 shallow/deep 两种错误；fast/other/lean/asym 先排除。
输入: rep_scores.angle_series (4 通道 x 32)
标签: true_label='合格' -> standard; true_label='不合格' 且 error_type in ('shallow','deep')
输出:
  datasets/models/rep_quality_userlabel.pt
  datasets/models/tcn_eval.json
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "ml_pose"))
from poc_skeleton_model import build_tcn  # noqa: E402

DB = os.path.join(HERE, "fitness.db")
MODEL_OUT = os.path.join(HERE, "..", "datasets", "models", "rep_quality_userlabel.pt")
EVAL_OUT = os.path.join(HERE, "..", "datasets", "models", "tcn_eval.json")
CHANNELS = ["primary", "torso", "lr_diff", "shoulder"]
SERIES = 32
CLASSES = ["standard", "shallow", "deep"]
CLASS_NAMES = {"standard": "合格", "shallow": "太浅", "deep": "太深"}


def _series_to_array(s):
    cols = []
    for ch in CHANNELS:
        v = s.get(ch) or [0.0] * SERIES
        v = [(x if x is not None else 0.0) for x in v]
        v = (v + [0.0] * SERIES)[:SERIES]
        cols.append(v)
    return np.array(cols, np.float32)


def load():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT angle_series s, true_label t, error_type et, exercise e FROM rep_scores "
        "WHERE angle_series IS NOT NULL AND ("
        "true_label='合格' OR (true_label='不合格' AND error_type IN ('shallow','deep'))"
        ")"
    ).fetchall()
    c.close()
    X, y, labels = [], [], []
    for r in rows:
        try:
            s = json.loads(r["s"] or "{}")
        except Exception:
            continue
        if not s or "primary" not in s:
            continue
        label = "standard" if r["t"] == "合格" else r["et"]
        if label not in CLASSES:
            continue
        X.append(_series_to_array(s))
        y.append(CLASSES.index(label))
        labels.append(label)
    if not X:
        return np.empty((0, len(CHANNELS), SERIES), np.float32), np.array([], dtype=np.int64), []
    return np.stack(X), np.array(y, dtype=np.int64), labels


def _train(Xtr, ytr, seed, mu=None, sd=None, ret_model=False):
    import torch
    import torch.nn as nn

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    if mu is None:
        mu = Xtr.mean((0, 2), keepdims=True)
        sd = Xtr.std((0, 2), keepdims=True) + 1e-6
    Xn = (Xtr - mu) / sd

    counts = np.bincount(ytr, minlength=len(CLASSES))
    max_count = max(int(counts.max()), 1)
    Xa, ya = [], []
    for i in range(len(Xn)):
        # 少数类多增强；当前只有 211 条，控制增强上限，避免交叉验证太慢。
        reps = max(3, min(8, int(round(max_count / max(int(counts[ytr[i]]), 1) * 4))))
        Xa.append(Xn[i]); ya.append(ytr[i])
        for _ in range(reps):
            xx = Xn[i] + rng.normal(0, 0.07, Xn[i].shape).astype(np.float32)
            xx = xx * np.float32(0.9 + 0.2 * rng.random())
            if rng.random() < 0.35:
                xx = np.roll(xx, int(rng.integers(-2, 3)), axis=1)
            Xa.append(xx); ya.append(ytr[i])

    Xa = torch.tensor(np.stack(Xa))
    ya = torch.tensor(np.array(ya, dtype=np.int64))
    model = build_tcn(len(CHANNELS), len(CLASSES))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss()
    n = len(Xa)
    bs = 64
    model.train()
    for _ep in range(50):
        perm = torch.randperm(n)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            lossf(model(Xa[b]), ya[b]).backward()
            opt.step()
    model.eval()
    if ret_model:
        return model, mu, sd
    return model, mu, sd


def _predict(model, X, mu, sd):
    import torch
    with torch.no_grad():
        return model(torch.tensor((X - mu) / sd)).argmax(1).numpy()


def _metrics(y_true, y_pred, labels):
    n_cls = len(labels)
    cm = [[int(((y_true == a) & (y_pred == b)).sum()) for b in range(n_cls)] for a in range(n_cls)]
    acc = float((y_true == y_pred).mean()) if len(y_true) else 0.0
    recalls = []
    precisions = []
    for k in range(n_cls):
        tp = int(((y_true == k) & (y_pred == k)).sum())
        actual = int((y_true == k).sum())
        pred = int((y_pred == k).sum())
        recalls.append(float(tp / max(actual, 1)))
        precisions.append(float(tp / max(pred, 1)))
    return cm, acc, recalls, precisions, float(sum(recalls) / len(recalls))


def main():
    X, y, label_names = load()
    n = len(y)
    counts = {CLASSES[i]: int((y == i).sum()) for i in range(len(CLASSES))}
    print("训练范围: 合格 standard / 太浅 shallow / 太深 deep ；已排除 fast/other/lean/asym")
    print(f"样本 {n}: " + " / ".join(f"{CLASS_NAMES[k]} {counts[k]}" for k in CLASSES))
    if n < 15 or any(counts[k] < 5 for k in CLASSES):
        print("!! 某一类样本 <5 或总量太少，暂不训练")
        return

    from sklearn.model_selection import RepeatedStratifiedKFold
    rskf = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=0)
    yt_all, yp_all = [], []
    for k, (tr, te) in enumerate(rskf.split(X, y)):
        m, mu, sd = _train(X[tr], y[tr], seed=k)
        yp = _predict(m, X[te], mu, sd)
        yt_all.extend(y[te].tolist())
        yp_all.extend(yp.tolist())

    yt = np.array(yt_all)
    yp = np.array(yp_all)
    cm, acc, recalls, precisions, bal = _metrics(yt, yp, CLASSES)
    base = float(max((y == i).mean() for i in range(len(CLASSES))))

    print("\n==== 诚实交叉验证 (5x3 折, 三分类) ====")
    print(f"准确率        : {acc*100:.1f}%")
    print(f"多数类基线    : {base*100:.1f}%")
    print(f"平衡准确率    : {bal*100:.1f}%")
    for i, cls in enumerate(CLASSES):
        print(f"{CLASS_NAMES[cls]}召回/精确率: {recalls[i]*100:.1f}% / {precisions[i]*100:.1f}%")
    print(f"混淆矩阵 [真][预测] {CLASSES}: {cm}")

    import torch
    model, mu, sd = _train(X, y, seed=123, ret_model=True)
    os.makedirs(os.path.dirname(MODEL_OUT), exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "mu": mu,
        "sd": sd,
        "channels": CHANNELS,
        "series": SERIES,
        "classes": CLASSES,
        "class_names": CLASS_NAMES,
        "task": "standard_shallow_deep",
        "n_train": n,
        "cv_acc": round(acc, 3),
        "balanced_acc": round(bal, 3),
    }, MODEL_OUT)

    eval_json = {
        "available": True,
        "task": "standard_shallow_deep",
        "n": n,
        "n_pass": counts["standard"],
        "n_shallow": counts["shallow"],
        "n_deep": counts["deep"],
        "n_fail": counts["shallow"] + counts["deep"],
        "accuracy": round(acc, 3),
        "baseline_majority": round(base, 3),
        "balanced_acc": round(bal, 3),
        "recall_pass": round(recalls[0], 3),
        "recall_shallow": round(recalls[1], 3),
        "recall_deep": round(recalls[2], 3),
        "precision_pass": round(precisions[0], 3),
        "precision_shallow": round(precisions[1], 3),
        "precision_deep": round(precisions[2], 3),
        "confusion": cm,
        "classes": CLASSES,
        "class_names": CLASS_NAMES,
        "cv": "RepeatedStratifiedKFold 5x3",
        "label_source": "用户手工标注；仅 standard/shallow/deep；已排除 fast/other",
        "model_path": os.path.abspath(MODEL_OUT),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "当前阶段只识别太浅/太深两个错误；其他错误类型暂不参与训练和评估。",
    }
    with open(EVAL_OUT, "w", encoding="utf-8") as f:
        json.dump(eval_json, f, ensure_ascii=False, indent=2)

    print(f"\n最终模型已保存: {MODEL_OUT}  (在全部 {n} 个三分类样本上训练)")
    print(f"监控台评估已更新: {EVAL_OUT}")


if __name__ == "__main__":
    main()
