"""dl_more.py - 补 push_up 和 lunge 的视频."""
import subprocess, os, sys

PROXY = "http://127.0.0.1:7897"
OUT_ROOT = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\videos"

# 改用更通用、更可能命中 <180s 的视频
MORE = {
    "push_up": [
        "https://www.youtube.com/watch?v=0pkjOk0EiAk",
        "https://www.youtube.com/watch?v=_l3ySVKYVJ8",
        "https://www.youtube.com/watch?v=esfLgvunC30",
        "https://www.youtube.com/watch?v=4dF1DOWzf20",
        "https://www.youtube.com/watch?v=8d4ehoaP6vw",
    ],
    "lunge": [
        "https://www.youtube.com/watch?v=L8fvypPrzzs",
        "https://www.youtube.com/watch?v=eFWCn5iEbTU",
        "https://www.youtube.com/watch?v=GuYyhU4t1iE",
        "https://www.youtube.com/watch?v=ZNs1ynU3FmA",
        "https://www.youtube.com/watch?v=wrwwXE_x-pQ",
    ],
}

ok = fail = 0
for label, urls in MORE.items():
    out = os.path.join(OUT_ROOT, label)
    for u in urls:
        # 已有 3 个就停
        if len([f for f in os.listdir(out) if f.endswith('.mp4')]) >= 4:
            print(f"  {label} already >= 4, stop")
            break
        print(f"\n=== {label}: {u} ===")
        cmd = ["yt-dlp", "--proxy", PROXY, "--no-playlist",
               "--match-filter", "duration<=240",  # 放宽到 4min
               "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
               "-o", os.path.join(out, "%(id)s.%(ext)s"),
               "--no-warnings", "--socket-timeout", "30", "--retries", "2", u]
        try:
            r = subprocess.run(cmd)
            if r.returncode == 0:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  EXC: {e}")
            fail += 1
        sys.stdout.flush()

print(f"\n========== DONE: ok={ok} fail={fail} ==========")
import glob
for label in ["squat", "push_up", "plank", "lunge", "jumping_jack"]:
    files = glob.glob(os.path.join(OUT_ROOT, label, "*"))
    sz = sum(os.path.getsize(f) for f in files) / 1024 / 1024
    print(f"  {label}: {len(files)} files, {sz:.1f} MB")
