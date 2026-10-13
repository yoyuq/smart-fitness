"""
auth.py — User Authentication Module (V2)
==========================================
JWT-based auth with bcrypt password hashing.
PostgreSQL/SQLite compatible.
"""
import os, time, json
import re
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


def _device_schema_parts(sql):
    """Split SQLite's stored CREATE TABLE body without splitting quoted defaults."""
    start = sql.index("(") + 1
    parts, depth, quote = [], 1, None
    i = start
    while i < len(sql):
        char = sql[i]
        if quote:
            if char == quote:
                if quote != "]" and i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                break
            i = end + 2
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return parts + [sql[start:i].strip()], sql[i + 1:]
        elif char == "," and depth == 1:
            parts.append(sql[start:i].strip())
            start = i + 1
        i += 1
    raise sqlite3.OperationalError("Cannot read devices table definition")


def _init_devices(conn):
    # main's legacy SELECT * routes use positions, so these first six columns
    # must stay in exactly this order. Keep auth's columns after them.
    required = {
        "device_id": "device_id TEXT PRIMARY KEY",
        "name": "name TEXT DEFAULT ''",
        "firmware_version": "firmware_version TEXT",
        "last_seen": "last_seen REAL",
        "wifi_rssi": "wifi_rssi REAL",
        "status": "status TEXT DEFAULT 'offline'",
        "device_type": "device_type TEXT NOT NULL DEFAULT 'phone'",
        "user_id": "user_id INTEGER REFERENCES users(id)",
        "registered_at": "registered_at REAL DEFAULT (strftime('%s', 'now'))",
    }
    original = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='devices'"
    ).fetchone()
    if original is None:
        conn.execute("CREATE TABLE devices (" + ", ".join(required.values()) + ")")
        return
    info = list(conn.execute("PRAGMA table_xinfo(devices)"))
    names = [row[1] for row in info]
    if names[:6] == list(required)[:6] and required.keys() <= set(names):
        return

    parts, suffix = _device_schema_parts(original[0])
    columns, constraints = [], []
    for part in parts:
        # Preserve original column declarations, including defaults, CHECKs,
        # generated columns and any table-level constraints.
        head = re.sub(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", part, flags=re.S)
        target = constraints if re.match(
            r"(?:CONSTRAINT\b|PRIMARY\s+KEY\b|UNIQUE\b|CHECK\b|FOREIGN\s+KEY\b)",
            head, re.I,
        ) else columns
        target.append(part)
    if len(columns) != len(names):
        raise sqlite3.OperationalError("Cannot safely migrate devices table definition")
    existing = dict(zip(names, columns))
    definitions = [existing.get(name, spec) for name, spec in required.items()]
    definitions.extend(spec for name, spec in existing.items() if name not in required)
    definitions.extend(constraints)
    schema_objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='devices' "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
    ).fetchall()
    conn.execute("CREATE TABLE devices_bootstrap_new (" + ", ".join(definitions) + ")" + suffix)
    # Generated columns compute themselves; copy every other existing value by
    # name. The new timestamp default applies to migrated and future rows alike.
    copied = ", ".join('"' + row[1].replace('"', '""') + '"' for row in info if row[6] == 0)
    conn.execute(f"INSERT INTO devices_bootstrap_new ({copied}) SELECT {copied} FROM devices")
    conn.execute("DROP TABLE devices")
    conn.execute("ALTER TABLE devices_bootstrap_new RENAME TO devices")
    for row in schema_objects:
        conn.execute(row[0])


def init_auth_db():
    """Create users + workout_plans + devices + rate_limit tables."""
    conn = _get_conn()
    try:
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
        CREATE TABLE IF NOT EXISTS rate_limits (
            ip TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            window_start REAL NOT NULL,
            PRIMARY KEY (ip, endpoint)
        );
        """)
        # Rebuild atomically when an old schema has incompatible column order.
        # Do not rename the old table: dependent foreign keys must keep pointing
        # to devices, and dropping it must not cascade into existing sessions.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        _init_devices(conn)
        conn.commit()
    finally:
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
