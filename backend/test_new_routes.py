# coding: utf-8
"""新接口冒烟测试: workout/summary + stats/calendar"""
import urllib.request, json, time
BASE = "http://127.0.0.1:8080"

def req(method, path, data=None, token=None):
    url = BASE + path
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token: headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=5)
        return resp.getcode(), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {"err": "parse"}

u = f"smoke{int(time.time())%100000}"
code, r = req("POST", "/api/v2/auth/register", {"username": u, "password": "pw123456"})
tok = r.get("token")
print(f"register {code} ok={r.get('ok')}")

# workout/summary
code, ws = req("POST", "/api/v2/workout/summary",
    {"device_id": "smoke_dev_001", "exercise": "squat", "reps": 35, "duration_s": 180.5, "avg_form_score": 88.2},
    token=tok)
print(f"workout/summary {code} ok={ws.get('ok')}")
print(f"  totals: {ws.get('totals')}")
print(f"  coach_remark: {ws.get('coach_remark')}")
print(f"  badges: {ws.get('badges')}")
print(f"  kcal_est: {ws.get('kcal_est')}")

# 再来一条低 form, 看不同点评
code, ws2 = req("POST", "/api/v2/workout/summary",
    {"device_id": "smoke_dev_001", "exercise": "push_up", "reps": 8, "duration_s": 60, "avg_form_score": 55},
    token=tok)
print(f"workout/summary [low form] {code}: {ws2.get('coach_remark')}")

# stats/calendar
code, cal = req("GET", "/api/v2/stats/calendar?days=84", token=tok)
print(f"stats/calendar {code} days_count={len(cal.get('days',[]))}")
if cal.get('days'):
    print(f"  sample: {cal['days'][0]}")
print("DONE")
