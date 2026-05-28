"""synth_dataset.py - 合成 MediaPipe 关键点序列, 用于训练动作分类器.

每种动作的 33 个 BlazePose 关键点 (x, y, z, visibility) 都有显著数学规律:
  - squat: 髋 Y 周期升降, 膝盖角度 180->90->180
  - push_up: 肩 Y 周期升降, 肘角度 180->90->180
  - plank: 全部关节相对静止
  - lunge: 一腿前一腿后, 膝盖角度异步变化
  - jumping_jack: 双手双脚周期外展

生成 (T, 33, 4) numpy 数组, 写入 datasets/landmarks/synth_*.npz.
"""
import os, json
import numpy as np

OUT_DIR = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\landmarks"
os.makedirs(OUT_DIR, exist_ok=True)

LABELS = ["squat", "push_up", "plank", "lunge", "jumping_jack", "bicep_curl", "shoulder_press"]
LABEL2ID = {n: i for i, n in enumerate(LABELS)}

# BlazePose 33 个关节索引
NOSE = 0
L_SHO, R_SHO = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_FOOT, R_FOOT = 31, 32

# 基础站立 pose (归一化坐标 0~1, y 向下增加)
def base_standing_pose():
    """生成一个基础站立姿势, 33 个点."""
    pose = np.zeros((33, 4), dtype=np.float32)
    # 头部
    pose[NOSE]  = [0.5, 0.10, 0, 1.0]
    pose[1]     = [0.48, 0.09, 0, 1.0]  # 左眼内
    pose[2]     = [0.46, 0.09, 0, 1.0]  # 左眼
    pose[3]     = [0.45, 0.09, 0, 1.0]  # 左眼外
    pose[4]     = [0.52, 0.09, 0, 1.0]
    pose[5]     = [0.54, 0.09, 0, 1.0]
    pose[6]     = [0.55, 0.09, 0, 1.0]
    pose[7]     = [0.44, 0.10, 0, 1.0]  # 左耳
    pose[8]     = [0.56, 0.10, 0, 1.0]
    pose[9]     = [0.48, 0.13, 0, 1.0]  # 嘴
    pose[10]    = [0.52, 0.13, 0, 1.0]
    # 肩
    pose[L_SHO] = [0.40, 0.25, 0, 1.0]
    pose[R_SHO] = [0.60, 0.25, 0, 1.0]
    # 肘
    pose[L_ELB] = [0.38, 0.40, 0, 1.0]
    pose[R_ELB] = [0.62, 0.40, 0, 1.0]
    # 手腕
    pose[L_WRI] = [0.36, 0.55, 0, 1.0]
    pose[R_WRI] = [0.64, 0.55, 0, 1.0]
    # 手
    for i in [17, 18, 19, 20, 21, 22]:
        pose[i] = [0.36 if i % 2 == 1 else 0.64, 0.57, 0, 1.0]
    # 髋
    pose[L_HIP] = [0.43, 0.55, 0, 1.0]
    pose[R_HIP] = [0.57, 0.55, 0, 1.0]
    # 膝
    pose[L_KNEE] = [0.43, 0.75, 0, 1.0]
    pose[R_KNEE] = [0.57, 0.75, 0, 1.0]
    # 踝
    pose[L_ANKLE] = [0.43, 0.92, 0, 1.0]
    pose[R_ANKLE] = [0.57, 0.92, 0, 1.0]
    # 脚
    pose[29] = [0.41, 0.95, 0, 1.0]
    pose[30] = [0.59, 0.95, 0, 1.0]
    pose[L_FOOT] = [0.45, 0.97, 0, 1.0]
    pose[R_FOOT] = [0.55, 0.97, 0, 1.0]
    return pose


def gen_squat(T=120, fps=10, noise=0.01):
    """深蹲: 髋/膝 Y 周期升降, 膝角度 180->90."""
    pose0 = base_standing_pose()
    out = np.tile(pose0[None], (T, 1, 1))  # (T, 33, 4)
    # 周期 t=0..T, 一个周期 2-3 秒
    period_frames = int(fps * 2.5)
    for t in range(T):
        phase = (t % period_frames) / period_frames  # 0..1
        # 下蹲深度 0~0.15 (Y 增加)
        depth = 0.15 * (1 - np.cos(2 * np.pi * phase)) / 2
        # 髋下降
        out[t, L_HIP, 1] = pose0[L_HIP, 1] + depth
        out[t, R_HIP, 1] = pose0[R_HIP, 1] + depth
        # 膝盖前推 + 微抬
        out[t, L_KNEE, 0] = pose0[L_KNEE, 0] - 0.02
        out[t, R_KNEE, 0] = pose0[R_KNEE, 0] + 0.02
        out[t, L_KNEE, 1] = pose0[L_KNEE, 1] - depth * 0.3
        out[t, R_KNEE, 1] = pose0[R_KNEE, 1] - depth * 0.3
        # 躯干前倾
        for i in [L_SHO, R_SHO, NOSE]:
            out[t, i, 1] = pose0[i, 1] + depth * 0.4
            out[t, i, 0] = pose0[i, 0] + 0.03 * depth / 0.15
        # 手臂前伸保持平衡
        for i in [L_ELB, R_ELB, L_WRI, R_WRI]:
            out[t, i, 1] = pose0[i, 1] - depth * 0.3
    # 噪声
    out[..., :3] += np.random.normal(0, noise, out[..., :3].shape).astype(np.float32)
    out[..., 3]  = np.clip(out[..., 3] - np.random.uniform(0, 0.1, out[..., 3].shape).astype(np.float32), 0.5, 1.0)
    return out


def gen_pushup(T=120, fps=10, noise=0.01):
    """俯卧撑: 视角换成侧面, 整体水平, 肘 Y 周期升降."""
    pose0 = base_standing_pose()
    # 把人放倒: 把所有 Y 坐标转成 X 增长方向 (横躺)
    out = np.tile(pose0[None], (T, 1, 1))
    # 重写一个水平躺的 base
    h_base = pose0.copy()
    # 头在右, 脚在左; y=0.5 中线; x 表示水平位置
    h_base[NOSE]    = [0.85, 0.40, 0, 1.0]
    h_base[L_SHO]   = [0.70, 0.45, 0, 1.0]
    h_base[R_SHO]   = [0.70, 0.55, 0, 1.0]
    h_base[L_ELB]   = [0.65, 0.55, 0, 1.0]
    h_base[R_ELB]   = [0.65, 0.45, 0, 1.0]
    h_base[L_WRI]   = [0.62, 0.65, 0, 1.0]
    h_base[R_WRI]   = [0.62, 0.35, 0, 1.0]
    h_base[L_HIP]   = [0.45, 0.48, 0, 1.0]
    h_base[R_HIP]   = [0.45, 0.52, 0, 1.0]
    h_base[L_KNEE]  = [0.25, 0.48, 0, 1.0]
    h_base[R_KNEE]  = [0.25, 0.52, 0, 1.0]
    h_base[L_ANKLE] = [0.10, 0.48, 0, 1.0]
    h_base[R_ANKLE] = [0.10, 0.52, 0, 1.0]
    h_base[L_FOOT]  = [0.05, 0.49, 0, 1.0]
    h_base[R_FOOT]  = [0.05, 0.51, 0, 1.0]
    out = np.tile(h_base[None], (T, 1, 1))
    period = int(fps * 2.0)
    for t in range(T):
        phase = (t % period) / period
        dy = 0.06 * (1 - np.cos(2 * np.pi * phase)) / 2  # 0~0.06 下沉
        # 整体身体下沉 (除了肘、手腕作支撑点不动)
        for i in [NOSE, L_SHO, R_SHO, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE, L_FOOT, R_FOOT]:
            out[t, i, 1] = h_base[i, 1] + dy
        # 肘角度变化通过肩相对肘 Y 变化体现
    out[..., :3] += np.random.normal(0, noise, out[..., :3].shape).astype(np.float32)
    out[..., 3]  = np.clip(out[..., 3] - np.random.uniform(0, 0.1, out[..., 3].shape).astype(np.float32), 0.5, 1.0)
    return out


def gen_plank(T=120, fps=10, noise=0.005):
    """平板支撑: 类俯卧撑姿势但完全静止, 微抖."""
    out = gen_pushup(T=T, fps=fps, noise=0)
    # 把周期变化清掉 (拍均值)
    mean_pose = out.mean(axis=0)
    out = np.tile(mean_pose[None], (T, 1, 1))
    out[..., :3] += np.random.normal(0, noise, out[..., :3].shape).astype(np.float32)  # 仅微抖
    out[..., 3]  = np.clip(out[..., 3] - np.random.uniform(0, 0.05, out[..., 3].shape).astype(np.float32), 0.5, 1.0)
    return out


def gen_lunge(T=120, fps=10, noise=0.01):
    """弓步: 一腿前一腿后, 前膝弯曲, 后膝下沉."""
    pose0 = base_standing_pose()
    out = np.tile(pose0[None], (T, 1, 1))
    period = int(fps * 3.0)
    for t in range(T):
        phase = (t % period) / period
        depth = 0.12 * (1 - np.cos(2 * np.pi * phase)) / 2
        # 假设左腿在前 (X 减少, 即往前/相机近)
        # 左膝弯曲 + 髋下沉
        out[t, L_HIP, 1] = pose0[L_HIP, 1] + depth * 0.5
        out[t, R_HIP, 1] = pose0[R_HIP, 1] + depth * 0.5
        # 前腿: 左大腿压低, 膝盖前推
        out[t, L_KNEE, 0] = pose0[L_KNEE, 0] - 0.05
        out[t, L_KNEE, 1] = pose0[L_KNEE, 1] - depth * 0.2
        out[t, L_ANKLE, 0] = pose0[L_ANKLE, 0] - 0.08
        # 后腿: 右脚后撤, 膝盖下沉接近地面
        out[t, R_KNEE, 0] = pose0[R_KNEE, 0] + 0.08
        out[t, R_KNEE, 1] = pose0[R_KNEE, 1] + depth * 0.3
        out[t, R_ANKLE, 0] = pose0[R_ANKLE, 0] + 0.15
        out[t, R_ANKLE, 1] = pose0[R_ANKLE, 1] + 0.02
        # 躯干保持直立
    out[..., :3] += np.random.normal(0, noise, out[..., :3].shape).astype(np.float32)
    out[..., 3]  = np.clip(out[..., 3] - np.random.uniform(0, 0.1, out[..., 3].shape).astype(np.float32), 0.5, 1.0)
    return out


def gen_jumping_jack(T=120, fps=10, noise=0.01):
    """开合跳: 双臂双脚周期外展/收回."""
    pose0 = base_standing_pose()
    out = np.tile(pose0[None], (T, 1, 1))
    period = int(fps * 1.0)  # 快
    for t in range(T):
        phase = (t % period) / period
        spread = 0.5 * (1 - np.cos(2 * np.pi * phase)) / 2  # 0~0.5
        # 手臂上举外展
        out[t, L_SHO, 0] = pose0[L_SHO, 0]  # 肩不动
        out[t, L_ELB, 0] = pose0[L_ELB, 0] - 0.15 * spread
        out[t, L_ELB, 1] = pose0[L_ELB, 1] - 0.20 * spread
        out[t, L_WRI, 0] = pose0[L_WRI, 0] - 0.30 * spread
        out[t, L_WRI, 1] = pose0[L_WRI, 1] - 0.45 * spread  # 手到头顶
        out[t, R_ELB, 0] = pose0[R_ELB, 0] + 0.15 * spread
        out[t, R_ELB, 1] = pose0[R_ELB, 1] - 0.20 * spread
        out[t, R_WRI, 0] = pose0[R_WRI, 0] + 0.30 * spread
        out[t, R_WRI, 1] = pose0[R_WRI, 1] - 0.45 * spread
        # 腿外展
        out[t, L_KNEE, 0] = pose0[L_KNEE, 0] - 0.05 * spread
        out[t, L_ANKLE, 0] = pose0[L_ANKLE, 0] - 0.10 * spread
        out[t, R_KNEE, 0] = pose0[R_KNEE, 0] + 0.05 * spread
        out[t, R_ANKLE, 0] = pose0[R_ANKLE, 0] + 0.10 * spread
        # 整体小幅上下跳跃
        for i in range(33):
            out[t, i, 1] = out[t, i, 1] - 0.02 * spread
    out[..., :3] += np.random.normal(0, noise, out[..., :3].shape).astype(np.float32)
    out[..., 3]  = np.clip(out[..., 3] - np.random.uniform(0, 0.1, out[..., 3].shape).astype(np.float32), 0.5, 1.0)
    return out






def gen_bicep_curl(T=120, fps=10, noise=0.01):
    """生成弯举: 站立 + 双肘从伸直 (180°) 到弯曲 (40°) 周期变化, 肩膀和身体几乎不动."""
    import numpy as np
    base = base_standing_pose()
    out = np.zeros((T, 33, 4), dtype=np.float32)
    period_frames = int(fps * 2.5)
    for t in range(T):
        pose = base.copy()
        phase = (t % period_frames) / period_frames
        # 0->1->0 三角波: 伸直 -> 弯曲 -> 伸直
        if phase < 0.5:
            angle_progress = phase * 2  # 0 -> 1
        else:
            angle_progress = (1 - phase) * 2  # 1 -> 0
        # 肘从下方移到肩前 (Y 上升, X 略向内)
        for side, sho, elb, wri in [
            ("L", L_SHO, L_ELB, L_WRI),
            ("R", R_SHO, R_ELB, R_WRI),
        ]:
            sho_pt = pose[sho]
            # 弯举: 肘相对肩固定, 腕从 (elb_x, elb_y+0.18) 旋转到 (elb_x+0.02, elb_y-0.05)
            elb_y = sho_pt[1] + 0.18
            elb_x = sho_pt[0] + (0.04 if side == "L" else -0.04)
            pose[elb] = [elb_x, elb_y, 0, 1.0]
            # 腕: 当 progress=0 直立 (Y 大), progress=1 卷上 (Y 小)
            wri_y = elb_y + 0.18 - angle_progress * 0.40  # 范围 -0.22 ~ +0.18
            wri_x = elb_x + angle_progress * 0.05
            pose[wri] = [wri_x, wri_y, 0, 1.0]
        pose[:, :3] += np.random.normal(0, noise, (33, 3))
        out[t] = pose
    return out


def gen_shoulder_press(T=120, fps=10, noise=0.01):
    """生成肩推: 站立 + 双臂从肩两侧 (肘弯 90°, 腕在肩高) 推到头顶 (伸直)."""
    import numpy as np
    base = base_standing_pose()
    out = np.zeros((T, 33, 4), dtype=np.float32)
    period_frames = int(fps * 2.5)
    for t in range(T):
        pose = base.copy()
        phase = (t % period_frames) / period_frames
        if phase < 0.5:
            press_progress = phase * 2  # 0 (落下) -> 1 (推到顶)
        else:
            press_progress = (1 - phase) * 2
        for side, sho, elb, wri in [
            ("L", L_SHO, L_ELB, L_WRI),
            ("R", R_SHO, R_ELB, R_WRI),
        ]:
            sho_pt = pose[sho]
            # 起始位: 肘在肩外侧, 90 度, 腕在肩高水平
            # 终止位: 肘伸直, 腕在头顶
            elb_x_start = sho_pt[0] + (0.07 if side == "R" else -0.07)
            elb_y_start = sho_pt[1] + 0.04
            elb_x_end = sho_pt[0] + (0.03 if side == "R" else -0.03)
            elb_y_end = sho_pt[1] - 0.08  # 肘抬高
            elb_x = elb_x_start + (elb_x_end - elb_x_start) * press_progress
            elb_y = elb_y_start + (elb_y_end - elb_y_start) * press_progress
            pose[elb] = [elb_x, elb_y, 0, 1.0]
            # 腕推到头上
            wri_x_start = sho_pt[0] + (0.12 if side == "R" else -0.12)
            wri_y_start = sho_pt[1] - 0.02
            wri_x_end = sho_pt[0] + (0.03 if side == "R" else -0.03)
            wri_y_end = sho_pt[1] - 0.25  # 腕在头顶
            wri_x = wri_x_start + (wri_x_end - wri_x_start) * press_progress
            wri_y = wri_y_start + (wri_y_end - wri_y_start) * press_progress
            pose[wri] = [wri_x, wri_y, 0, 1.0]
        pose[:, :3] += np.random.normal(0, noise, (33, 3))
        out[t] = pose
    return out


GENERATORS = {
    "squat": gen_squat,
    "push_up": gen_pushup,
    "plank": gen_plank,
    "lunge": gen_lunge,
    "jumping_jack": gen_jumping_jack,
    "bicep_curl": gen_bicep_curl,
    "shoulder_press": gen_shoulder_press,
}

def main(n_per_class=20, T=120, fps=10):
    """每类生成 n_per_class 段, 每段 T 帧."""
    import json
    manifest = []
    for label, gen in GENERATORS.items():
        print(f"\n=== {label} x{n_per_class} ===")
        for i in range(n_per_class):
            # 加多样性: 不同噪声/不同周期长度
            T_var = T + np.random.randint(-20, 21)
            noise = np.random.uniform(0.005, 0.015)
            arr = gen(T=T_var, fps=fps, noise=noise)
            outp = os.path.join(OUT_DIR, f"synth_{label}_{i:03d}.npz")
            np.savez_compressed(
                outp,
                landmarks=arr.astype(np.float32),
                label=LABEL2ID[label],
                label_name=label,
                fps=float(fps),
                video_id=f"synth_{i:03d}",
            )
            manifest.append({
                "file": os.path.basename(outp),
                "label": label,
                "label_id": LABEL2ID[label],
                "T": int(arr.shape[0]),
                "fps": float(fps),
                "synth": True,
            })
        print(f"  wrote {n_per_class} clips")
    # update manifest
    mp_path = os.path.join(OUT_DIR, "manifest.json")
    if os.path.exists(mp_path):
        with open(mp_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = []
    # 去重
    keys = {m["file"] for m in existing}
    for m in manifest:
        if m["file"] not in keys:
            existing.append(m)
    with open(mp_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"\nTOTAL manifest size: {len(existing)}")


if __name__ == "__main__":
    main(n_per_class=30, T=120, fps=10)
