"""从 YouTube 下载每类标准动作视频用于训练数据.
- 走 socks5/http 代理 (用户 MEMORY.md 提到 7897)
- 每类 5-8 段, 优先 < 60s 短视频
"""
import os, subprocess, sys, shutil

PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7897")
ROOT = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\videos"

# 每类: 搜索关键词 + 候选视频 ID
PRESETS = {
    "squat": [
        "https://www.youtube.com/watch?v=YaXPRqUwItQ",  # standard squat tutorial
        "https://www.youtube.com/watch?v=aclHkVaku9U",  # squat form
        "https://www.youtube.com/watch?v=Dy28eq2PjcM",  # bodyweight squat
    ],
    "push_up": [
        "https://www.youtube.com/watch?v=IODxDxX7oi4",  # push up form
        "https://www.youtube.com/watch?v=Eh00_rniF8E",  # standard push up
    ],
    "plank": [
        "https://www.youtube.com/watch?v=ASdvN_XEl_c",
    ],
    "lunge": [
        "https://www.youtube.com/watch?v=QOVaHwm-Q6U",  # standard lunge
    ],
    "jumping_jack": [
        "https://www.youtube.com/watch?v=c4DAnQ6DtF8",  # jumping jacks
    ],
}

def dl_one(url, outdir):
    """下载短视频, 限制 720p, 转 mp4."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--proxy", PROXY,
        "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
        "--max-filesize", "50M",
        "--no-playlist",
        "-o", os.path.join(outdir, "%(id)s.%(ext)s"),
        "--quiet", "--no-warnings",
        url,
    ]
    try:
        r = subprocess.run(cmd, timeout=180, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  OK {url}")
            return True
        else:
            print(f"  FAIL {url}: {r.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT {url}")
        return False

ok, fail = 0, 0
for cat, urls in PRESETS.items():
    outdir = os.path.join(ROOT, cat)
    os.makedirs(outdir, exist_ok=True)
    print(f"\n=== {cat} ===")
    for u in urls:
        if dl_one(u, outdir):
            ok += 1
        else:
            fail += 1

print(f"\nTOTAL: ok={ok} fail={fail}")
print("文件:")
for cat in PRESETS:
    files = os.listdir(os.path.join(ROOT, cat))
    if files:
        print(f"  {cat}: {len(files)} 个 -> {files}")
