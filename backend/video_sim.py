"""video_sim.py - 用真实训练视频模拟完整训练会话 (评分V2 数据采集)

把 datasets/videos/<exercise>/*.mp4 按 ~2fps 抽帧, 模拟 APP 摄像头流:
  training/start → 逐帧 vision/infer/full → training/stop
全管线生效: YOLO26 推理 → 可见度门禁 → rep 计数 → 按 rep 评分 → 关键帧留存。
之后跑 `python ai_review.py run` 即可让 AI 评审团审这些真实 rep。

用法:
  python video_sim.py                 # 全部视频
  python video_sim.py squat          # 只跑某类
  python video_sim.py squat --fps 2 --max-frames 240
"""
import argparse
import base64
import glob
import os
import sys
import time

import cv2
import requests

BASE = os.environ.get("SIM_BASE", "http://127.0.0.1:8080")
VIDEO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "videos")
SIM_USER = ("simbot", "simbot123")


def login():
    r = requests.post(f"{BASE}/api/v2/auth/login", json={"username": SIM_USER[0], "password": SIM_USER[1]})
    if r.status_code != 200 or not r.json().get("token"):
        r = requests.post(f"{BASE}/api/v2/auth/register", json={"username": SIM_USER[0], "password": SIM_USER[1]})
    return r.json()["token"]


def sim_video(tok, exercise, path, fps=2.0, max_frames=240, jpeg_q=80):
    h = {"Authorization": f"Bearer {tok}"}
    device = f"sim-{os.path.splitext(os.path.basename(path))[0]}"
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(src_fps / fps)))

    st = requests.post(f"{BASE}/api/v2/training/start", headers=h,
                       json={"device_id": device, "exercise": exercise}).json()
    sid = st.get("session_id")
    print(f"  ▶ {os.path.basename(path)} (src {src_fps:.0f}fps, step {step}) sid={sid}")

    sent = reps = valid = 0
    last_rep_score = None
    i = -1
    t0 = time.time()
    while sent < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        if i % step:
            continue
        # 模拟 ESP32/手机分辨率
        if frame.shape[1] > 640:
            scale = 640.0 / frame.shape[1]
            frame = cv2.resize(frame, (640, int(frame.shape[0] * scale)))
        ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
        if not ok2:
            continue
        b64 = base64.b64encode(buf.tobytes()).decode()
        # 用视频自身时间轴做帧时间戳, 节奏(控制分)才真实
        video_ts = t0 + (cap.get(cv2.CAP_PROP_POS_MSEC) or (i / src_fps * 1000)) / 1000.0
        try:
            r = requests.post(f"{BASE}/api/v2/vision/infer/full", headers=h,
                              json={"device_id": device, "image": b64, "exercise": exercise,
                                    "frame_ts": video_ts},
                              timeout=30).json()
        except Exception as e:
            print(f"    [warn] infer failed: {e}")
            continue
        sent += 1
        reps = r.get("rep_count", reps)
        if r.get("form_score") is not None:
            valid += 1
        if r.get("rep_score"):
            last_rep_score = r["rep_score"]
    cap.release()

    requests.post(f"{BASE}/api/v2/training/stop", headers=h, json={"device_id": device})
    dt = time.time() - t0
    print(f"    帧 {sent} (有效评分帧 {valid}) reps={reps} 用时 {dt:.0f}s "
          f"最后rep分={last_rep_score['total'] if last_rep_score else '-'}")
    return sid, reps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exercise", nargs="?", default=None)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--max-frames", type=int, default=240)
    args = ap.parse_args()

    tok = login()
    cats = [args.exercise] if args.exercise else sorted(os.listdir(VIDEO_ROOT))
    total_reps = 0
    for cat in cats:
        cat_dir = os.path.join(VIDEO_ROOT, cat)
        if not os.path.isdir(cat_dir) or cat == "plank":
            continue  # plank 静态动作不走 rep 管线
        vids = sorted(glob.glob(os.path.join(cat_dir, "*.mp4")))
        print(f"== {cat}: {len(vids)} 段 ==")
        for v in vids:
            _, reps = sim_video(tok, cat, v, fps=args.fps, max_frames=args.max_frames)
            total_reps += reps
    print(f"\n总计数到 reps: {total_reps}")
    print("下一步: python ai_review.py run --limit 100 && python ai_review.py report")


if __name__ == "__main__":
    main()
