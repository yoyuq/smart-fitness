"""F-02 压测: 并发调用 /api/v2/vision/infer，统计 p50 / p95 / p99 / qps / 错误率。

使用: python stress_test.py [--workers 10] [--fps 5] [--seconds 30]
要求 backend 在 8080 运行。
"""
import argparse, time, json, base64, threading, statistics
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error
import numpy as np
import cv2


def build_payload():
    img = np.full((240, 320, 3), 128, dtype=np.uint8)
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode()


B64 = build_payload()
URL = 'http://127.0.0.1:8080/api/v2/vision/infer'
_results: list = []
_lock = threading.Lock()


def one_request(session_id: str):
    body = json.dumps({'image': B64, 'session_id': session_id}).encode()
    req = urllib.request.Request(URL, data=body, headers={'Content-Type': 'application/json'})
    t0 = time.time()
    err = None
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
            ok = (r.status == 200)
    except Exception as e:
        ok = False
        err = type(e).__name__
    dt = (time.time() - t0) * 1000
    with _lock:
        _results.append((dt, ok, err))


def worker_loop(worker_id: int, end_time: float, fps: int):
    sid = f'stress-w{worker_id}'
    interval = 1.0 / fps
    while time.time() < end_time:
        t_start = time.time()
        one_request(sid)
        # tight schedule (sleep to next slot)
        slack = interval - (time.time() - t_start)
        if slack > 0:
            time.sleep(slack)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=10)
    ap.add_argument('--fps', type=int, default=5, help='per-worker request rate')
    ap.add_argument('--seconds', type=int, default=20)
    args = ap.parse_args()

    print(f'starting: {args.workers} workers × {args.fps} fps × {args.seconds}s')
    end = time.time() + args.seconds
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker_loop, i, end, args.fps) for i in range(args.workers)]
        for f in futures:
            f.result()

    total = len(_results)
    ok_n = sum(1 for _, ok, _ in _results if ok)
    err_n = total - ok_n
    latencies = sorted(dt for dt, _, _ in _results)
    if not latencies:
        print('no responses!')
        return
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    qps = total / args.seconds
    err_types = {}
    for _, ok, e in _results:
        if not ok and e:
            err_types[e] = err_types.get(e, 0) + 1
    print('\n=== F-02 STRESS RESULTS ===')
    print(f'duration:    {args.seconds}s')
    print(f'total:       {total}')
    print(f'success:     {ok_n}  ({ok_n/total*100:.1f}%)')
    print(f'errors:      {err_n}  {err_types if err_types else ""}')
    print(f'qps:         {qps:.1f}')
    print(f'p50 latency: {p50:.0f} ms')
    print(f'p95 latency: {p95:.0f} ms')
    print(f'p99 latency: {p99:.0f} ms')
    print(f'mean:        {statistics.mean(latencies):.0f} ms')
    print(f'max:         {latencies[-1]:.0f} ms')

    # PLAN.md target: 10 devices × 5 fps p99 < 500ms
    target_qps = args.workers * args.fps
    print(f'\nplan target: {args.workers} × {args.fps} fps = {target_qps} qps, p99 < 500ms')
    print(f'verdict: ', end='')
    if p99 < 500 and ok_n == total:
        print('PASS')
    elif p99 < 500:
        print(f'WARN latency OK but {err_n} errors')
    else:
        print(f'FAIL (p99 {p99:.0f}ms > 500ms)')


if __name__ == '__main__':
    main()
