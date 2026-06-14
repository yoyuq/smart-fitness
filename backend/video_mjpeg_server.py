"""把训练视频循环成 MJPEG 流, 让 APP 把它当 ESP32 摄像头消费 (模拟器端到端演示).

APP 的 ESP32 源请求 http://<ip>:81/stream; 用 adb reverse tcp:81 tcp:8181 把
模拟器的 127.0.0.1:81 映射到本机 8181, 即可零改动喂视频。

用法: python video_mjpeg_server.py [视频路径] [--fps 12] [--port 8181]
"""
import argparse
import glob
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

VIDEO_ROOT = os.path.join(os.path.dirname(__file__), "..", "datasets", "videos")
_cfg = {"path": None, "fps": 12.0}


class MjpegHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_GET(self):
        if not self.path.startswith("/stream"):
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        delay = 1.0 / _cfg["fps"]
        try:
            while True:  # 循环播放
                cap = cv2.VideoCapture(_cfg["path"])
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if frame.shape[1] > 640:
                        s = 640.0 / frame.shape[1]
                        frame = cv2.resize(frame, (640, int(frame.shape[0] * s)))
                    ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ok2:
                        continue
                    data = buf.tobytes()
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode())
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
                    time.sleep(delay)
                cap.release()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", default=None)
    ap.add_argument("--fps", type=float, default=12.0)
    ap.add_argument("--port", type=int, default=8181)
    args = ap.parse_args()
    path = args.video or sorted(glob.glob(os.path.join(VIDEO_ROOT, "squat", "*.mp4")))[0]
    _cfg["path"] = path
    _cfg["fps"] = args.fps
    print(f"serving {os.path.basename(path)} as MJPEG @ 0.0.0.0:{args.port}/stream  (fps={args.fps})")
    ThreadingHTTPServer(("0.0.0.0", args.port), MjpegHandler).serve_forever()


if __name__ == "__main__":
    main()
