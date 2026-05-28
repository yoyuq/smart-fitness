"""dl_bilibili.py - 从 B 站搜索并下载每类动作的真实视频做训练素材.

- 走直连 (代理 7897 已死)
- 用 search API 拿候选, 按时长 30~180s 优先, 然后 60~300s
- 每类下到至少 3 段
- 限速 + 单文件 50MB 上限
"""
import os, sys, time, json, subprocess, re
import requests

ROOT = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\datasets\videos"
os.makedirs(ROOT, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

QUERIES = {
    "squat":        ["标准深蹲教学", "徒手深蹲示范", "深蹲动作要领"],
    "push_up":      ["标准俯卧撑教学", "俯卧撑示范", "标准俯卧撑动作"],
    "plank":        ["平板支撑教程", "标准平板支撑", "plank教学"],
    "lunge":        ["弓步蹲教学", "箭步蹲示范", "弓步蹲动作"],
    "jumping_jack": ["开合跳教程", "开合跳示范", "标准开合跳"],
}

# 目标: 每类至少 N 段, 总时长 60~600s/段 (避免太短抓不到关键点, 太长浪费)
TARGET_PER_CAT = 4
DUR_MIN, DUR_MAX = 40, 360
HARD_DUR_MAX = 600  # 实在抓不到就退到 10min
MAX_FILESIZE = "50M"

session = requests.Session()
session.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com"})

def warm_cookies():
    session.get("https://www.bilibili.com", timeout=10)

def search_videos(keyword: str, limit: int = 40):
    """调 B 站 search API 返回 [{bvid, duration_s, title}, ...]."""
    r = session.get(
        "https://api.bilibili.com/x/web-interface/search/all/v2",
        params={"keyword": keyword},
        timeout=15,
    )
    try:
        j = r.json()
    except Exception:
        return []
    if j.get("code") != 0:
        return []
    out = []
    for blk in j.get("data", {}).get("result", []) or []:
        if blk.get("result_type") != "video":
            continue
        for v in blk.get("data", []) or []:
            bvid = v.get("bvid")
            dur = v.get("duration", "")
            # duration like "5:44" or "27:37" or "1:02:03"
            secs = 0
            if isinstance(dur, str) and ":" in dur:
                parts = [int(x) for x in dur.split(":") if x.isdigit()]
                for p in parts:
                    secs = secs * 60 + p
            title = re.sub(r"<[^>]+>", "", v.get("title", ""))
            if bvid:
                out.append({"bvid": bvid, "duration": secs, "title": title})
    return out[:limit]


def dl_one(bvid: str, outdir: str, max_dur: int = HARD_DUR_MAX) -> bool:
    """下载单个视频, mp4 优先, 限制 720p 以下."""
    url = f"https://www.bilibili.com/video/{bvid}"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "best[height<=480][ext=mp4]/best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "--max-filesize", MAX_FILESIZE,
        "--no-playlist",
        "--match-filter", f"duration<{max_dur}",
        "-o", os.path.join(outdir, f"{bvid}.%(ext)s"),
        "--no-warnings",
        "--retries", "2",
        "--socket-timeout", "20",
        url,
    ]
    try:
        r = subprocess.run(cmd, timeout=240, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT {bvid}")
        return False
    if r.returncode == 0:
        # 检查文件
        for ext in ("mp4", "mkv", "webm", "flv"):
            p = os.path.join(outdir, f"{bvid}.{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 100 * 1024:
                sz = os.path.getsize(p) / 1024 / 1024
                print(f"    OK  {bvid} ({sz:.1f}MB)")
                return True
        print(f"    EMPTY {bvid}")
        return False
    else:
        err = (r.stderr or "")[-300:].strip().replace("\n", " | ")
        print(f"    FAIL {bvid}: {err[:200]}")
        return False


def main():
    warm_cookies()
    stats = {}
    for cat, kws in QUERIES.items():
        outdir = os.path.join(ROOT, cat)
        os.makedirs(outdir, exist_ok=True)
        existing = [f for f in os.listdir(outdir) if f.endswith((".mp4", ".webm", ".mkv", ".flv"))]
        have = len(existing)
        print(f"\n=== {cat} (have={have}, target={TARGET_PER_CAT}) ===")

        # 收集候选
        cands = []
        seen = set(os.path.splitext(f)[0] for f in existing)
        for kw in kws:
            try:
                vs = search_videos(kw, limit=40)
                print(f"  search '{kw}' -> {len(vs)} hits")
            except Exception as e:
                print(f"  search '{kw}' err {e}")
                vs = []
            for v in vs:
                if v["bvid"] in seen:
                    continue
                seen.add(v["bvid"])
                cands.append(v)
            time.sleep(1.2)

        # 排序: 时长在 [DUR_MIN, DUR_MAX] 内优先, 再按时长靠近 90s
        def score(v):
            d = v["duration"]
            if DUR_MIN <= d <= DUR_MAX:
                return (0, abs(d - 90))
            elif 0 < d <= HARD_DUR_MAX:
                return (1, abs(d - 90))
            else:
                return (2, d)
        cands.sort(key=score)

        # 下载直到达到目标
        for v in cands:
            if have >= TARGET_PER_CAT:
                break
            print(f"  try BV{v['bvid'][2:] if v['bvid'].startswith('BV') else v['bvid']} dur={v['duration']}s  {v['title'][:50]}")
            if dl_one(v["bvid"], outdir):
                have += 1
            time.sleep(1.5)

        stats[cat] = have
        print(f"  --> {cat} now has {have} videos")
    print("\n=== SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
