"""直接量 ESP32 原始 MJPEG 流的真实帧率: 数所有帧 vs 不同帧(内容哈希)。"""
import sys, time, hashlib
import requests

URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.129.20:81/stream"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

s = requests.Session()
s.trust_env = False
print(f"[measure] {URL}  {DUR}s ...", flush=True)
try:
    r = s.get(URL, stream=True, timeout=(5, 8))
except Exception as e:
    print("连接失败:", e); sys.exit(1)

buf = b""
raw = 0
seen = set()
t0 = time.time()
for chunk in r.iter_content(chunk_size=8192):
    if not chunk:
        continue
    buf += chunk
    while True:
        start = buf.find(b"\xff\xd8")
        end = buf.find(b"\xff\xd9", start + 2) if start != -1 else -1
        if start == -1 or end == -1:
            break
        jpg = buf[start:end + 2]
        buf = buf[end + 2:]
        raw += 1
        seen.add(hashlib.md5(jpg).hexdigest())
    if time.time() - t0 >= DUR:
        break
el = time.time() - t0
print(f"[measure] {el:.1f}s 内: 总帧 {raw} (原始 {raw/el:.1f} fps), 不同帧 {len(seen)} (有效 {len(seen)/el:.1f} fps)", flush=True)
