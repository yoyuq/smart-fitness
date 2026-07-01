"""测 /vision/infer/full 单帧耗时: 抓一帧真实画面, 连发 N 次计时。
判断瓶颈到底是 ESP32 硬件, 还是推理接口本身慢。"""
import sys, time, base64
import requests

STREAM = "http://192.168.129.20:81/stream"
BACKEND = "http://localhost:8080/api/v2/vision/infer/full"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8

s = requests.Session(); s.trust_env = False
# 抓一帧
r = s.get(STREAM, stream=True, timeout=(5, 8))
buf = b""
jpg = None
for chunk in r.iter_content(chunk_size=8192):
    buf += chunk
    e = buf.find(b"\xff\xd9")
    st = buf.find(b"\xff\xd8")
    if st != -1 and e != -1 and e > st:
        jpg = buf[st:e + 2]; break
r.close()
print(f"[lat] 抓到一帧 {len(jpg)} 字节, 连发 {N} 次到推理接口...", flush=True)
b64 = base64.b64encode(jpg).decode()
payload = {"device_id": "esp32cam-001", "image_base64": b64,
           "user_id": "31", "exercise": "squat", "source": "esp32cam"}
lats = []
for i in range(N):
    t0 = time.time()
    try:
        resp = s.post(BACKEND, json=payload, timeout=10)
        dt = (time.time() - t0) * 1000
        lats.append(dt)
        print(f"  #{i+1}: {dt:.0f} ms  (HTTP {resp.status_code})", flush=True)
    except Exception as ex:
        print(f"  #{i+1}: 失败 {ex}", flush=True)
if lats:
    lats_sorted = sorted(lats)
    avg = sum(lats) / len(lats)
    print(f"[lat] 平均 {avg:.0f} ms, 中位 {lats_sorted[len(lats)//2]:.0f} ms, 最快 {min(lats):.0f} ms -> 接口上限约 {1000/avg:.1f} fps", flush=True)
