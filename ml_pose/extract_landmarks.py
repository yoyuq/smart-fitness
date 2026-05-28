"""extract_landmarks.py - 从视频提取 MediaPipe 关键点序列, 存 .npz (新版 Tasks API)."""
import os, sys, glob, json
import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode

ROOT = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets"
VIDEO_DIR = os.path.join(ROOT, "videos")
OUT_DIR   = os.path.join(ROOT, "landmarks")
MODEL_TASK = os.path.join(ROOT, "models", "pose_landmarker_lite.task")
os.makedirs(OUT_DIR, exist_ok=True)

LABELS = ["squat", "push_up", "plank", "lunge", "jumping_jack"]
LABEL2ID = {n: i for i, n in enumerate(LABELS)}


def extract(video_path: str, landmarker, sample_fps: float = 10.0):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"  cannot open {video_path}")
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / sample_fps)))
    out = []
    detected = 0
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = landmarker.detect(mp_image)
            if res.pose_landmarks:
                lm = res.pose_landmarks[0]
                arr = np.array([[p.x, p.y, p.z, p.visibility] for p in lm], dtype=np.float32)
                out.append(arr)
                detected += 1
            else:
                # 用 0 填充以保持长度信息 (vis=0 会被训练时过滤)
                pass
        frame_idx += 1
    cap.release()
    print(f"  fps={fps:.1f} frames={frame_idx} sampled={frame_idx // step} detected={detected}")
    if detected < 5:
        return None
    return np.stack(out, axis=0), float(sample_fps)


def main():
    opts = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_TASK),
        running_mode=RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = PoseLandmarker.create_from_options(opts)

    manifest = []
    for label in LABELS:
        label_dir = os.path.join(VIDEO_DIR, label)
        if not os.path.isdir(label_dir):
            print(f"skip {label_dir} (not exists)")
            continue
        videos = sorted(glob.glob(os.path.join(label_dir, "*.mp4")) +
                        glob.glob(os.path.join(label_dir, "*.webm")) +
                        glob.glob(os.path.join(label_dir, "*.mkv")))
        print(f"\n=== {label} ({len(videos)} videos) ===")
        for vp in videos:
            video_id = os.path.splitext(os.path.basename(vp))[0]
            out_path = os.path.join(OUT_DIR, f"real_{label}_{video_id}.npz")
            if os.path.exists(out_path):
                print(f"  skip {video_id} (already extracted)")
                continue
            print(f"  {video_id}")
            res = extract(vp, landmarker)
            if res is None:
                print(f"    SKIP (no pose detected)")
                continue
            arr, sample_fps = res
            np.savez_compressed(
                out_path,
                landmarks=arr.astype(np.float32),
                label=LABEL2ID[label],
                label_name=label,
                fps=sample_fps,
                video_id=video_id,
                source="real_youtube",
            )
            manifest.append({
                "file": os.path.basename(out_path),
                "label": label,
                "label_id": LABEL2ID[label],
                "T": int(arr.shape[0]),
                "fps": sample_fps,
                "video_id": video_id,
                "source": "real_youtube",
            })
            print(f"    saved (T={arr.shape[0]})")

    # update manifest
    mp_path = os.path.join(OUT_DIR, "manifest.json")
    if os.path.exists(mp_path):
        try:
            existing = json.load(open(mp_path, "r", encoding="utf-8"))
        except Exception:
            existing = []
    else:
        existing = []
    keys = {m.get("file") for m in existing}
    for m in manifest:
        if m["file"] not in keys:
            existing.append(m)
    json.dump(existing, open(mp_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== TOTAL ===")
    print(f"  added {len(manifest)} real clips, manifest size: {len(existing)}")
    # 按 label 统计
    from collections import Counter
    by_label = Counter([m.get("label") for m in existing])
    by_source = Counter([m.get("source", "synth") for m in existing])
    print(f"  by_label: {dict(by_label)}")
    print(f"  by_source: {dict(by_source)}")


if __name__ == "__main__":
    main()
