"""poc_yolo26_multiperson.py - YOLO26m-pose 多人同框 PoC

验证三件事:
1. YOLO26m-pose 能否在一帧里检出多个人的骨架 (MediaPipe 做不到)
2. 主体锁定策略: 中心距离 + bbox 面积加权, 选出"正在锻炼的那个人"
3. 单帧延迟是否满足健身房 2fps 推理预算 (<500ms/帧, CPU)

用法: python ml_pose/poc_yolo26_multiperson.py [image ...]
不带参数时用默认测试图 (含 ultralytics 自带多人图 bus.jpg)
"""
import sys, os, time, json
import numpy as np
import cv2
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "ml_pose", "poc_out")
os.makedirs(OUT_DIR, exist_ok=True)

# YOLO26m-pose; 下载失败时退回仓库根目录现成的 yolov8n-pose.pt
def load_model():
    try:
        m = YOLO("yolo26m-pose.pt")
        return m, "yolo26m-pose"
    except Exception as e:
        print(f"[warn] yolo26m-pose 加载失败 ({e}), 退回 yolov8n-pose")
        return YOLO(os.path.join(ROOT, "yolov8n-pose.pt")), "yolov8n-pose"


def pick_primary(boxes_xyxy, img_w, img_h):
    """主体锁定: score = 0.6*面积占比 + 0.4*(1-中心距离归一化)"""
    if len(boxes_xyxy) == 0:
        return -1
    cx, cy = img_w / 2, img_h / 2
    best_i, best_s = -1, -1.0
    for i, (x1, y1, x2, y2) in enumerate(boxes_xyxy):
        area = (x2 - x1) * (y2 - y1) / (img_w * img_h)
        bx, by = (x1 + x2) / 2, (y1 + y2) / 2
        dist = np.hypot(bx - cx, by - cy) / np.hypot(cx, cy)
        s = 0.6 * area + 0.4 * (1 - dist)
        if s > best_s:
            best_s, best_i = s, i
    return best_i


def run(model, name, path):
    img = cv2.imread(path)
    if img is None:
        print(f"[skip] 读不到 {path}")
        return None
    h, w = img.shape[:2]
    t0 = time.perf_counter()
    res = model(img, verbose=False)[0]
    dt = (time.perf_counter() - t0) * 1000
    n = len(res.boxes) if res.boxes is not None else 0
    boxes = res.boxes.xyxy.cpu().numpy() if n else np.zeros((0, 4))
    confs = res.boxes.conf.cpu().numpy() if n else np.zeros(0)
    kpts = res.keypoints.xy.cpu().numpy() if (n and res.keypoints is not None) else np.zeros((0, 17, 2))
    prim = pick_primary(boxes, w, h)

    vis = img.copy()
    for i in range(n):
        x1, y1, x2, y2 = boxes[i].astype(int)
        is_p = (i == prim)
        color = (0, 255, 0) if is_p else (128, 128, 128)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 3 if is_p else 1)
        cv2.putText(vis, f"{'PRIMARY' if is_p else 'other'} {confs[i]:.2f}",
                    (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        for (kx, ky) in kpts[i]:
            if kx > 0 or ky > 0:
                cv2.circle(vis, (int(kx), int(ky)), 3, color, -1)
    out = os.path.join(OUT_DIR, f"{name}_{os.path.splitext(os.path.basename(path))[0]}.jpg")
    cv2.imwrite(out, vis)
    info = {"image": os.path.basename(path), "size": [w, h], "persons": n,
            "primary_idx": int(prim), "latency_ms": round(dt, 1), "out": out}
    print(json.dumps(info, ensure_ascii=False))
    return info


if __name__ == "__main__":
    imgs = sys.argv[1:]
    if not imgs:
        imgs = []
        for cand in [
            os.path.join(ROOT, "_archive", "screenshots", "debug_esp32_frame_2x.jpg"),
            os.path.join(ROOT, "_archive", "screenshots", "debug_current_frame.jpg"),
        ]:
            if os.path.exists(cand):
                imgs.append(cand)
        # ultralytics 自带多人测试图 (首次自动下载)
        try:
            from ultralytics.utils.downloads import download
            bus = os.path.join(OUT_DIR, "bus.jpg")
            if not os.path.exists(bus):
                download("https://ultralytics.com/images/bus.jpg", dir=OUT_DIR)
            imgs.append(bus)
        except Exception as e:
            print(f"[warn] 多人样例图下载失败: {e}")

    model, name = load_model()
    print(f"=== model: {name} ===")
    results = [r for p in imgs if (r := run(model, name, p))]
    # 预热后再测一次稳定延迟
    if results:
        lat = [run(model, name, imgs[-1])["latency_ms"] for _ in range(3)]
        print(f"warm latency (3 runs): {lat} ms")
