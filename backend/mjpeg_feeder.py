"""mjpeg_feeder.py — 从 ESP32 MJPEG 流拉帧, 本地高速 POST 给后端推理接口.

解决: ESP32 自己的 HTTP POST 通道只有 ~2fps, 识别一顿一顿。
做法: 本机直接拉 ESP32 已有的 MJPEG 流(实测 ~6fps 源头上限), 逐帧 POST 给
      /vision/infer/full(推理仅 ~32ms / 31fps 余量, 不是瓶颈), 跟上源头帧率。
      用同一 device_id + user_id + exercise 接入训练 session, 后端/固件/App 都不用改。

关键点:
- requests 手动解析 multipart MJPEG (按 JPEG FFD8/FFD9 切帧), OpenCV 读不了 ESP32 的流。
- trust_env=False 绕过系统 HTTP 代理(127.0.0.1:7897)。
- 单线程: 每个完整帧都发(推理 31fps 余量足够跟上源头 6fps, 不会越积越多);
  仅当一次性堆积 >3 帧(说明真落后了)才丢旧只发最新, 保证低延迟。
  避免多线程抢 GIL 反而拖慢。

用法: python mjpeg_feeder.py [stream_url] [device_id] [user_id] [exercise] [fps_cap]
"""
import sys, time, base64
import requests

STREAM   = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.129.20:81/stream"
DEVICE   = sys.argv[2] if len(sys.argv) > 2 else "esp32cam-001"
USER     = sys.argv[3] if len(sys.argv) > 3 else "31"          # hjl
EXERCISE = sys.argv[4] if len(sys.argv) > 4 else "squat"
FPS_CAP  = float(sys.argv[5]) if len(sys.argv) > 5 else 15.0    # 安全上限, 高于源头则不限速
BACKEND  = "http://localhost:8080/api/v2/vision/infer/full"
MIN_GAP  = 1.0 / FPS_CAP

print(f"[feeder] stream={STREAM} device={DEVICE} user={USER} ex={EXERCISE} fps_cap={FPS_CAP}", flush=True)


def post_frame(s, jpg):
    b64 = base64.b64encode(jpg).decode()
    return s.post(BACKEND, json={
        "device_id": DEVICE, "image_base64": b64,
        "user_id": USER, "exercise": EXERCISE,
        "source": "esp32cam", "frame_ts": time.time(),
    }, timeout=5)


def run_once():
    s = requests.Session(); s.trust_env = False
    r = s.get(STREAM, stream=True, timeout=(5, 8))
    buf = b""
    sent = ok = 0
    t0 = time.time(); last_report = t0; last_post = 0.0
    for chunk in r.iter_content(chunk_size=8192):
        if not chunk:
            continue
        buf += chunk
        # 取出缓冲区里所有完整帧
        frames = []
        while True:
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2) if start != -1 else -1
            if start == -1 or end == -1:
                break
            frames.append(buf[start:end + 2])
            buf = buf[end + 2:]
        if not frames:
            if len(buf) > 2_000_000:
                buf = buf[-500_000:]
            continue
        # 正常情况一批 1~2 帧, 全发; 堆积 >3 帧说明落后了, 只发最新一帧丢旧帧 -> 低延迟
        send_list = [frames[-1]] if len(frames) > 3 else frames
        for jpg in send_list:
            now = time.time()
            if now - last_post < MIN_GAP:        # 安全上限(默认15fps, 一般不触发)
                continue
            last_post = now
            try:
                resp = post_frame(s, jpg)
                sent += 1
                if resp.status_code == 200:
                    ok += 1
            except Exception as e:
                print("[feeder] POST 失败:", e, flush=True)
            if now - last_report >= 3.0:
                el = now - t0
                print(f"[feeder] 转发 {sent} (200 OK {ok}), 转发 ~{sent/el:.1f} fps", flush=True)
                last_report = now


def main():
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[feeder] 流断开/出错 ({e}), 1.2s 重连…", flush=True)
            time.sleep(1.2)


if __name__ == "__main__":
    main()
