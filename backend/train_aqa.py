"""train_aqa.py - 动作质量评估(AQA)模型训练脚手架 (评分V2 第四阶段)

把 AI 评审团积累的标签 (ai_reviews.errors_json) 当训练目标, 把每个 rep 的
多关节特征当输入, 训练"每类错误一个轻量分类器"。当前数据量小, 用带正则的
逻辑回归 + 交叉验证, 数据足够后可替换为时序 TCN/GRU (见 SCORING_V2_PLAN)。

特征来源: rep_scores 行 (peak_angle/depth/control/symmetry/duration) +
该 rep 的多关节生物力学量 (需 rep_features 表; 暂从 rep_scores 可得字段起步)。

用法:
  python train_aqa.py status     # 看可用标注数据量 (按动作×错误)
  python train_aqa.py train      # 数据够则训练并保存 models/aqa_*.pkl
"""
import argparse
import json
import os
import sqlite3
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "fitness.db")
MODEL_DIR = os.path.join(BACKEND_DIR, "..", "datasets", "models")
MIN_PER_CLASS = 30   # 每个(动作,错误)正负样本各需多少才训练, 低于此只报告

# 与 ai_review.CHECKLISTS 对齐的错误键
from ai_review import CHECKLISTS  # noqa: E402


def _load_labeled(conn):
    """取既有评审标签又有评分特征的 rep. 返回 [(exercise, feat_dict, errors_dict), ...]."""
    rows = conn.execute(
        "SELECT r.exercise, r.depth, r.control, r.symmetry, r.peak_angle, r.duration_s, "
        "       a.errors_json "
        "FROM rep_scores r JOIN ai_reviews a ON a.rep_id = r.id "
        "WHERE a.ai_score IS NOT NULL").fetchall()
    out = []
    for ex, depth, control, sym, peak, dur, ej in rows:
        try:
            errors = json.loads(ej)
        except Exception:
            continue
        feat = {"depth": depth, "control": control, "symmetry": sym,
                "peak_angle": peak, "duration_s": dur}
        out.append((ex, feat, errors))
    return out


def status():
    conn = sqlite3.connect(DB_PATH)
    data = _load_labeled(conn)
    conn.close()
    print(f"已标注 rep 总数: {len(data)}")
    by_ex = {}
    for ex, _f, errors in data:
        d = by_ex.setdefault(ex, {"n": 0, "errors": {}})
        d["n"] += 1
        for k, v in errors.items():
            if v:
                d["errors"][k] = d["errors"].get(k, 0) + 1
    for ex, d in sorted(by_ex.items()):
        checklist = [k for k, _q, _w in CHECKLISTS.get(ex, [])]
        print(f"\n== {ex}: {d['n']} 个标注 ==")
        for k in checklist:
            pos = d["errors"].get(k, 0)
            ready = "✓可训练" if (pos >= MIN_PER_CLASS and d["n"] - pos >= MIN_PER_CLASS) else "数据不足"
            print(f"  {k}: 正样本 {pos}/{d['n']}  [{ready}]")
    print(f"\n(每个错误需正/负各 >= {MIN_PER_CLASS} 才训练; 否则继续用第四阶段多关节规则)")


def train():
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
    except ImportError:
        print("需要 scikit-learn: pip install scikit-learn")
        return
    conn = sqlite3.connect(DB_PATH)
    data = _load_labeled(conn)
    conn.close()
    os.makedirs(MODEL_DIR, exist_ok=True)
    feat_keys = ["depth", "control", "symmetry", "peak_angle", "duration_s"]
    trained = 0
    import pickle
    for ex in sorted(set(d[0] for d in data)):
        rows = [(f, e) for x, f, e in data if x == ex]
        for err_key, _q, _w in CHECKLISTS.get(ex, []):
            y = [1 if e.get(err_key) else 0 for _f, e in rows]
            pos, neg = sum(y), len(y) - sum(y)
            if pos < MIN_PER_CLASS or neg < MIN_PER_CLASS:
                continue
            X = np.array([[f.get(k) or 0 for k in feat_keys] for f, _e in rows], dtype=float)
            yv = np.array(y)
            clf = LogisticRegression(max_iter=1000, class_weight="balanced")
            scores = cross_val_score(clf, X, yv, cv=5, scoring="f1")
            clf.fit(X, yv)
            path = os.path.join(MODEL_DIR, f"aqa_{ex}_{err_key}.pkl")
            with open(path, "wb") as fh:
                pickle.dump({"model": clf, "feat_keys": feat_keys}, fh)
            print(f"  {ex}/{err_key}: f1(cv)={scores.mean():.2f} 已存 {os.path.basename(path)}")
            trained += 1
    if trained == 0:
        print("当前没有任何(动作,错误)达到训练门槛。先用 video_sim + ai_review 积累更多标注。")
        print("运行 `python train_aqa.py status` 查看缺口。")
    else:
        print(f"\n训练完成 {trained} 个错误分类器 -> {MODEL_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "train"], nargs="?", default="status")
    args = ap.parse_args()
    (status if args.cmd == "status" else train)()
