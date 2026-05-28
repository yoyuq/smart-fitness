"""Test all V2 endpoints including stats and WS"""
import urllib.request, json, time, asyncio, sys

BASE = 'http://localhost:8080'

def req(method, path, data=None, token=None):
    url = f'{BASE}{path}'
    body = json.dumps(data).encode() if data else None
    headers = {'Content-Type': 'application/json'} if data else {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r)
        return resp.getcode(), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {'ok': False, 'error': 'parse_err'}

print('='*55)
print('A Module Full Test /api/v2/')
print('='*55)

uid = int(time.time()) % 10000
uname = f'testuser{uid}'

# 1. Register
code, r = req('POST', '/api/v2/auth/register', {'username': uname, 'password': 'p'})
print(f'[A-02] Register: {code} ok={r.get("ok")}')
tok = r.get('token', '')

# 2. Login
code, r2 = req('POST', '/api/v2/auth/login', {'username': uname, 'password': 'p'})
print(f'[A-02] Login:    {code} ok={r2.get("ok")}')
tok = r2.get('token', tok)

# 3. Profile
code, p = req('GET', '/api/v2/auth/profile', token=tok)
print(f'[A-02] Profile:  {code} user={p.get("user",{}).get("username","")}')

# 4. Create plan
code, pl = req('POST', '/api/v2/plans',
    {'name': 'test', 'exercises': [{'type': 'squat', 'sets': 3, 'reps': 10}]}, token=tok)
plan_id = pl.get('plan_id', '')
print(f'[A-05] Create plan:  {code} ok={pl.get("ok")} id={plan_id}')

# 5. List plans
code, pls = req('GET', '/api/v2/plans', token=tok)
print(f'[A-05] List plans:   {code} count={len(pls.get("plans",[]))}')

# 6. Delete plan
code, dl = req('DELETE', f'/api/v2/plans/{plan_id}', token=tok)
print(f'[A-05] Delete plan:  {code} ok={dl.get("ok")}')

# 7. Register device
code, dev = req('POST', '/api/v2/devices/register',
    {'device_id': f'test{uid}', 'device_type': 'phone', 'name': 'Test'}, token=tok)
print(f'[A-03] Register device: {code} ok={dev.get("ok")}')

# 8. List devices
code, devs = req('GET', '/api/v2/devices', token=tok)
print(f'[A-03] List devices:    {code} count={len(devs.get("devices",[]))}')

# 9. Session history
code, sh = req('GET', '/api/v2/sessions/history', token=tok)
print(f'[A-03] Session history: {code} count={len(sh.get("sessions",[]))}')

# 10. Stats daily (no sessions yet so empty)
code, sd = req('GET', '/api/v2/stats/daily', token=tok)
print(f'[A-06] Stats daily:  {code} ok={sd.get("ok")} sessions={sd.get("stats",{}).get("sessions_count")}')

# 11. Stats weekly
code, sw = req('GET', '/api/v2/stats/weekly', token=tok)
print(f'[A-06] Stats weekly: {code} ok={sw.get("ok")} days={len(sw.get("weekly",[]))}')

# 12. WS push endpoint (test without actual WS)
code, wp = req('POST', '/api/v2/ws/push',
    {'target': 'session:test-123', 'message': {'type': 'ping', 'data': 'hello'}})
print(f'[A-07] WS push:     {code} ok={wp.get("ok")}')

# 13. Health check
code, h = req('GET', '/health')
print(f'[OK] Health:    {code}')

print()
ok = all([
    r.get("ok"), r2.get("ok"),
    code == 200 and 'user' in p,
    pl.get("ok"), len(pls.get("plans",[])) > 0, dl.get("ok"),
    dev.get("ok"), len(devs.get("devices",[])) > 0,
    sd.get("ok"), sw.get("ok"), wp.get("ok")
])
print(f'{'ALL PASSED' if ok else 'SOME FAILED'}')
