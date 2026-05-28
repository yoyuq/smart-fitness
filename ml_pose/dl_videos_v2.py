"""dl_videos.py - 通过代理下 5 类动作的 YouTube 短视频做训练素材.

每个动作 2~3 条 30s~2min 的 high-quality 标准动作教程.
进度直接 print, 不用 capture_output (避免 buffer 阻塞).
"""
import os, sys, subprocess

PROXY = "http://127.0.0.1:7897"
OUT_ROOT = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\videos"

# 每类挑 3 个 high-quality 视频, 优先选有完整正侧视角的标准动作
VIDEOS = {
    "squat": [
        # 短视频 30~60s 标准深蹲
        "https://www.youtube.com/watch?v=YaXPRqUwItQ",  # How To Squat Properly
        "https://www.youtube.com/watch?v=aclHkVaku9U",  # Bodyweight Squat
        "https://www.youtube.com/watch?v=Dy28eq2PjcM",  # Perfect Squat Form
    ],
    "push_up": [
        "https://www.youtube.com/watch?v=IODxDxX7oi4",  # Perfect Push Up
        "https://www.youtube.com/watch?v=Eh00_rniF8E",  # Push-up Form
        "https://www.youtube.com/watch?v=WDIpL0pjun0",  # How to do push up
    ],
    "plank": [
        "https://www.youtube.com/watch?v=ASdvN_XEl_c",  # Plank tutorial
        "https://www.youtube.com/watch?v=pSHjTRCQxIw",  # Perfect plank
        "https://www.youtube.com/watch?v=B296mZDhrP4",  # Plank form
    ],
    "lunge": [
        "https://www.youtube.com/watch?v=QOVaHwm-Q6U",  # Lunge tutorial
        "https://www.youtube.com/watch?v=3XDriUn0udo",  # Forward lunge
        "https://www.youtube.com/watch?v=QF0BQS-W8qE",  # Lunge form
    ],
    "jumping_jack": [
        "https://www.youtube.com/watch?v=c4DAnQ6DtF8",  # Jumping Jack
        "https://www.youtube.com/watch?v=iSSAk4XCsRA",  # Jumping Jacks proper form
        "https://www.youtube.com/watch?v=UpH7rm0cYbM",  # Standard Jumping Jacks
    ],
}


def dl_one(url, out_dir):
    """下载单个视频, 最高 720p, 最长 2 分钟, 直接打印 yt-dlp 输出."""
    cmd = [
        "yt-dlp",
        "--proxy", PROXY,
        "--no-playlist",
        "--match-filter", "duration<=180",  # <= 3min
        "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
        "-o", os.path.join(out_dir, "%(id)s.%(ext)s"),
        "--no-warnings",
        "--socket-timeout", "30",
        "--retries", "2",
        url,
    ]
    print(f"  >>> {url}")
    r = subprocess.run(cmd)  # 不 capture, 实时输出
    return r.returncode == 0


def main():
    total_ok = 0
    total_fail = 0
    for label, urls in VIDEOS.items():
        out = os.path.join(OUT_ROOT, label)
        os.makedirs(out, exist_ok=True)
        print(f"\n=== {label} ===")
        for u in urls:
            try:
                if dl_one(u, out):
                    total_ok += 1
                else:
                    total_fail += 1
            except Exception as e:
                print(f"  EXC {u}: {e}")
                total_fail += 1
            sys.stdout.flush()
    print(f"\n========== DONE: ok={total_ok} fail={total_fail} ==========")
    # 列出已下文件
    import glob
    for label in VIDEOS:
        files = glob.glob(os.path.join(OUT_ROOT, label, "*"))
        sizes = sum(os.path.getsize(f) for f in files) / 1024 / 1024
        print(f"  {label}: {len(files)} files, {sizes:.1f} MB")


if __name__ == "__main__":
    main()
