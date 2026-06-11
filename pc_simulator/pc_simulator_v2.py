"""pc_simulator_v2.py - 模拟 ESP32-CAM 用电脑摄像头或视频文件作输入.

模式:
  --source camera         (默认) 用电脑摄像头
  --source video --path xxx.mp4   用视频文件
  --source synth --label squat   用合成数据 (无需任何设备)

每帧:
  1. 取一帧 BGR 图像
  2. 调用 pose_engine.infer_from_image() 拿到 exercise/score/feedback
  3. POST 到 backend (8081 ai_app 或 8080 main)
  4. 实时在 OpenCV 窗口显示关键点 + 推理结果
"""
import os, sys, time, argparse, json, threading
import cv2
import numpy as np
import requests

# 加 ml_pose 到 path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml_pose"))
from pose_engine import PoseEngine

DEFAULT_BACKEND = "http://127.0.0.1:8081"


def draw_overlay(frame, res):
    """在画面上叠加推理结果."""
    h, w = frame.shape[:2]
    # 半透明黑色顶栏
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 110), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    if not res.get("detected"):
        cv2.putText(frame, "NO POSE DETECTED", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return

    ex = res.get("exercise", "?")
    conf = res.get("confidence", 0.0)
    score = res.get("form_score", "-")
    fb = res.get("feedback", "")
    ms = res.get("infer_ms", 0)

    cv2.putText(frame, f"{ex.upper()}  conf={conf:.2f}",
                (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    color = (0, 255, 0) if isinstance(score, int) and score >= 80 else \
            (0, 165, 255) if isinstance(score, int) and score >= 60 else (0, 0, 255)
    cv2.putText(frame, f"score: {score}", (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.putText(frame, f"{fb[:60]}", (10, 95),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    cv2.putText(frame, f"infer {ms}ms", (w - 130, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # 画关键点
    lms = res.get("landmarks") or []
    if lms:
        # 连接线 (BlazePose connections, 简版)
        conn = [
            (11,12), (11,13), (13,15), (12,14), (14,16),
            (11,23), (12,24), (23,24),
            (23,25), (25,27), (24,26), (26,28),
        ]
        pts = [(int(p["x"]*w), int(p["y"]*h)) for p in lms]
        for a, b in conn:
            if a < len(pts) and b < len(pts):
                cv2.line(frame, pts[a], pts[b], (0, 255, 255), 2)
        for x, y in pts:
            cv2.circle(frame, (x, y), 3, (0, 0, 255), -1)


def post_to_backend(backend, payload):
    """异步 POST, 不阻塞主循环."""
    def _send():
        try:
            requests.post(f"{backend}/api/sim/frame", json=payload, timeout=2)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


def run_camera(eng, backend, cam_index=0, send_every=5):
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f"cannot open camera {cam_index}")
        return
    n = 0
    print("press q to quit, s to toggle send to backend")
    send_on = True
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        res = eng.infer_from_image(frame)
        draw_overlay(frame, res)
        if send_on and n % send_every == 0 and res.get("detected"):
            post_to_backend(backend, {
                "source": "camera",
                "exercise": res.get("exercise"),
                "confidence": res.get("confidence"),
                "form_score": res.get("form_score"),
                "angles": res.get("angles"),
                "ts": time.time(),
            })
        cv2.imshow("Smart Fitness Simulator", frame)
        n += 1
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        elif k == ord("s"):
            send_on = not send_on
            print(f"send={send_on}")
    cap.release()
    cv2.destroyAllWindows()


def run_video(eng, backend, video_path, send_every=3, speed=1.0):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"cannot open video {video_path}")
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    delay = max(1, int(1000 / fps / speed))
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        res = eng.infer_from_image(frame)
        draw_overlay(frame, res)
        if n % send_every == 0 and res.get("detected"):
            post_to_backend(backend, {
                "source": "video", "video": os.path.basename(video_path),
                "exercise": res.get("exercise"),
                "confidence": res.get("confidence"),
                "form_score": res.get("form_score"),
                "angles": res.get("angles"),
                "ts": time.time(),
            })
        cv2.imshow("Smart Fitness Simulator [Video]", frame)
        n += 1
        if (cv2.waitKey(delay) & 0xFF) == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


def run_synth(eng, backend, label, n_clips=10, send_every=1):
    """合成数据模式: 不开摄像头, 模拟训练过程."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml_pose"))
    from synth_dataset import GENERATORS
    gen = GENERATORS.get(label)
    if gen is None:
        print(f"unknown label {label}, choices: {list(GENERATORS.keys())}")
        return
    print(f"=== synth mode: {label} x{n_clips} ===")
    total_correct = 0
    total = 0
    for clip_i in range(n_clips):
        arr = gen(T=60, fps=10, noise=0.012)
        for t, frame_lm in enumerate(arr):
            if t % send_every != 0: continue
            res = eng.infer_from_landmarks(frame_lm)
            total += 1
            if res.get("exercise") == label: total_correct += 1
            if t % 10 == 0:
                print(f"  clip{clip_i} t{t} pred={res.get('exercise')} conf={res.get('confidence',0):.2f} score={res.get('form_score','-')}")
            post_to_backend(backend, {
                "source": "synth",
                "exercise": res.get("exercise"),
                "confidence": res.get("confidence"),
                "form_score": res.get("form_score"),
                "angles": res.get("angles"),
                "ts": time.time(),
            })
            time.sleep(0.05)
    print(f"\n=== ACC: {total_correct}/{total} = {total_correct/max(1,total)*100:.1f}% ===")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["camera", "video", "synth"], default="camera")
    p.add_argument("--path", help="video path (when source=video)")
    p.add_argument("--label", default="squat", help="synth label")
    p.add_argument("--n", type=int, default=5, help="synth n clips")
    p.add_argument("--backend", default=DEFAULT_BACKEND)
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--speed", type=float, default=1.0, help="video playback speed")
    args = p.parse_args()

    eng = PoseEngine()
    print(f"backend={args.backend} source={args.source}")
    if args.source == "camera":
        run_camera(eng, args.backend, args.cam)
    elif args.source == "video":
        if not args.path or not os.path.exists(args.path):
            print(f"video not found: {args.path}")
            return
        run_video(eng, args.backend, args.path, speed=args.speed)
    elif args.source == "synth":
        run_synth(eng, args.backend, args.label, args.n)


if __name__ == "__main__":
    main()
