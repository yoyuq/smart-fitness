"""
pc_camera_agent.py — PC camera edge client for Smart Fitness
============================================================

Thin-client mode for a PC webcam:
  webcam -> resize/compress JPEG locally -> base64 -> POST /api/v2/vision/infer/full

This lets PC webcams behave like ESP32-CAM/phone camera sources while reusing
backend PoseEngine, rep counting, form scoring and WebSocket HUD updates.

Usage:
  python pc_simulator/pc_camera_agent.py --server http://127.0.0.1:8080 --device-id pc-camera-001 --exercise squat --preview

Notes:
  - The APP should select "PC Camera" and use the same device_id.
  - Compression is done on the PC before upload: resize max width + JPEG quality.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import time
from typing import Any, Dict, Optional

import cv2

try:
    import requests
except ImportError as e:  # pragma: no cover
    raise SystemExit("requests is required: pip install requests") from e


log = logging.getLogger("pc_camera_agent")


def encode_frame(frame, max_width: int, jpeg_quality: int) -> str:
    h, w = frame.shape[:2]
    if w > max_width:
        ratio = max_width / float(w)
        frame = cv2.resize(frame, (max_width, max(1, int(h * ratio))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def draw_status(frame, resp: Optional[Dict[str, Any]], device_id: str, exercise: str) -> None:
    if not resp:
        text = f"{device_id} {exercise}: waiting"
    else:
        detected = resp.get("detected")
        reps = resp.get("rep_count", 0)
        score = resp.get("form_score")
        ms = resp.get("infer_ms") or resp.get("inference_ms")
        text = f"{device_id} {exercise} detected={detected} reps={reps} score={score} infer={ms}ms"
    cv2.putText(frame, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2, cv2.LINE_AA)


def run(args: argparse.Namespace) -> None:
    infer_url = args.server.rstrip("/") + "/api/v2/vision/infer/full"
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open camera index {args.camera}")

    interval_ms = args.interval_ms
    last_resp: Optional[Dict[str, Any]] = None
    frame_count = 0
    log.info("PC camera agent started: url=%s device_id=%s exercise=%s", infer_url, args.device_id, args.exercise)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                log.warning("camera read failed")
                time.sleep(0.5)
                continue

            frame_count += 1
            t0 = time.time()
            try:
                image_b64 = encode_frame(frame, args.max_width, args.jpeg_quality)
                payload = {
                    "image": image_b64,
                    "device_id": args.device_id,
                    "exercise": args.exercise,
                    "source": "pc",
                    "backend": "mediapipe",
                }
                if args.user_id is not None:
                    payload["user_id"] = args.user_id
                r = requests.post(infer_url, json=payload, timeout=args.timeout)
                r.raise_for_status()
                last_resp = r.json()
                interval_ms = int(last_resp.get("next_interval_ms") or interval_ms)
                log.info(
                    "frame=%d detected=%s reps=%s score=%s infer=%sms next=%sms",
                    frame_count,
                    last_resp.get("detected"),
                    last_resp.get("rep_count"),
                    last_resp.get("form_score"),
                    last_resp.get("infer_ms") or last_resp.get("inference_ms"),
                    interval_ms,
                )
            except Exception as e:
                log.warning("upload/infer failed: %s", e)

            if args.preview:
                draw_status(frame, last_resp, args.device_id, args.exercise)
                cv2.imshow("Smart Fitness PC Camera", frame)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break

            elapsed_ms = int((time.time() - t0) * 1000)
            sleep_ms = max(1, interval_ms - elapsed_ms)
            time.sleep(sleep_ms / 1000.0)
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Smart Fitness PC webcam edge client")
    ap.add_argument("--server", default="http://127.0.0.1:8080", help="Backend base URL")
    ap.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    ap.add_argument("--device-id", default="pc-camera-001", help="Device id used by APP/backend")
    ap.add_argument("--exercise", default="squat", help="Target exercise key, e.g. squat")
    ap.add_argument("--user-id", type=int, default=None, help="Optional user id for WS broadcast when not training")
    ap.add_argument("--max-width", type=int, default=640, help="Resize frame before upload")
    ap.add_argument("--jpeg-quality", type=int, default=60, help="JPEG quality 1-100")
    ap.add_argument("--interval-ms", type=int, default=500, help="Initial upload interval")
    ap.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    ap.add_argument("--preview", action="store_true", help="Show local webcam preview")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    run(args)
