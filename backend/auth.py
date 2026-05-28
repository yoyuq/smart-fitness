"""
auth.py — User Authentication Module (V2)
==========================================
JWT-based auth with bcrypt password hashing.
PostgreSQL/SQLite compatible.
"""
import os, time, json
from typing import Optional, Dict, Any
import sqlite3
import bcrypt
import jwt as pyjwt
from datetime import datetime, timedelta, timezone

_SECRET = os.getenv("JWT_SECRET", "smart-fitness-dev-secret-change-in-prod")
_JWT_ALGO = "HS256"
_TOKEN_EXPIRE_DAYS = 7
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fitness.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_auth_db():
    """Create users + workout_plans + devices + rate_limit tables."""
    conn = _get_conn()
    # Migrate old devices table if needed
    try:
        conn.execute("SELECT device_type FROM devices LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE devices ADD COLUMN device_type TEXT DEFAULT 'phone'")
        conn.execute("ALTER TABLE devices ADD COLUMN user_id INTEGER")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            created_at REAL NOT NULL DEFAULT (julianday('now')),
            last_login REAL
        );
        CREATE TABLE IF NOT EXISTS workout_plans (
            plan_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            exercises TEXT DEFAULT '[]',
            created_at REAL DEFAULT (julianday('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_type TEXT NOT NULL DEFAULT 'phone',
            name TEXT DEFAULT '',
            user_id INTEGER,
            last_seen REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            ip TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            window_start REAL NOT NULL,
            PRIMARY KEY (ip, endpoint)
        );
    """)
    conn.commit()
    conn.close()


# ── Password helpers ──

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT helpers ──

def generate_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return pyjwt.encode(payload, _SECRET, algorithm=_JWT_ALGO)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = pyjwt.decode(token, _SECRET, algorithms=[_JWT_ALGO])
        return {"user_id": payload["user_id"], "username": payload["username"]}
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, KeyError):
        return None


# ── Rate limiter (simple in-memory sliding window) ──

_rate_limit_store: Dict[str, list] = {}

def check_rate_limit(ip: str, endpoint: str, max_per_minute: int = 5) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    key = f"{ip}:{endpoint}"
    now = time.time()
    window = now - 60
    if key not in _rate_limit_store:
        _rate_limit_store[key] = []
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if t > window]
    if len(_rate_limit_store[key]) >= max_per_minute:
        return False
    _rate_limit_store[key].append(now)
    return True


# ── User CRUD ──

def register(username: str, password: str, display_name: str = "") -> Dict:
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return {"ok": False, "message": "用户名已存在"}
        pwhash = hash_password(password)
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, created_at, last_login) VALUES (?,?,?,?,?)",
            (username, pwhash, display_name, time.time(), time.time())
        )
        user_id = cursor.lastrowid
        conn.commit()
        token = generate_token(user_id, username)
        return {"ok": True, "message": "注册成功", "token": token, "user_id": user_id, "username": username}
    except Exception as e:
        return {"ok": False, "message": f"注册失败: {str(e)}"}
    finally:
        conn.close()


def login(username: str, password: str) -> Dict:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id, username, password_hash FROM users WHERE username=?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return {"ok": False, "message": "用户名或密码错误"}
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
        conn.commit()
        token = generate_token(row["id"], row["username"])
        return {"ok": True, "message": "登录成功", "token": token, "user_id": row["id"], "username": row["username"]}
    except Exception as e:
        return {"ok": False, "message": f"登录失败: {str(e)}"}
    finally:
        conn.close()


def get_user_profile(user_id: int) -> Optional[Dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, username, display_name, created_at, last_login FROM users WHERE id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["avatar"] = ""
    return d


# ── Device registration ──

def register_device(device_id: str, device_type: str = "phone", name: str = "", user_id: Optional[int] = None) -> Dict:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO devices (device_id, device_type, name, user_id, last_seen) VALUES (?,?,?,?,?)",
            (device_id, device_type, name, user_id, time.time())
        )
        conn.commit()
        return {"ok": True, "message": "设备注册成功", "device_id": device_id}
    except Exception as e:
        return {"ok": False, "message": f"设备注册失败: {str(e)}"}
    finally:
        conn.close()


# ── Workout plans ──

def create_plan(user_id: int, name: str, exercises: list) -> Dict:
    import uuid
    plan_id = str(uuid.uuid4())[:8]
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO workout_plans (plan_id, user_id, name, exercises, created_at) VALUES (?,?,?,?,?)",
            (plan_id, user_id, name, json.dumps(exercises), time.time())
        )
        conn.commit()
        return {"ok": True, "plan_id": plan_id, "name": name}
    except Exception as e:
        return {"ok": False, "message": str(e)}
    finally:
        conn.close()


def get_plans(user_id: int) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT plan_id, name, exercises, created_at FROM workout_plans WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_plan(plan_id: str, user_id: int) -> bool:
    conn = _get_conn()
    conn.execute("DELETE FROM workout_plans WHERE plan_id=? AND user_id=?", (plan_id, user_id))
    deleted = conn.total_changes > 0
    conn.commit()
    conn.close()
    return deleted


# ── Rate limiter for main.py ──
def require_auth(authorization: Optional[str]) -> Optional[Dict]:
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "").strip()
    return verify_token(token)
