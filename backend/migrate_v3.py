#!/usr/bin/env python3
"""
migrate_v3.py — Smart Fitness V3 数据库迁移脚本

新增/确认以下表与索引（与 main.py 启动建表保持等价，便于离线迁移）：
  - user_body_metrics (D-03)
  - user_exercise_log (D-04)
  - device_user_binding (D-05)
  - daily_summary (D-06)
  - pose_data 上的 idx_pose_session_ts / idx_pose_exercise_ts (B-09)
  - 各类用户/设备索引

用法:
  python migrate_v3.py [--db PATH] [--dry-run]
  默认 DB 路径: ../fitness_v2.db  (相对 backend/)
  环境变量 FITNESS_DB_PATH 优先于默认值。

行为:
  - 每个 CREATE 都用 IF NOT EXISTS，可重复执行
  - --dry-run 模式只列出将要执行的 DDL，不实际写库
  - 输出迁移前后 schema 中表的清单与行数
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


DDLS = [
    # D-03 用户身体指标
    """
    CREATE TABLE IF NOT EXISTS user_body_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        timestamp REAL NOT NULL,
        weight_kg REAL,
        height_cm REAL,
        body_fat_pct REAL,
        resting_hr INTEGER,
        notes TEXT DEFAULT '',
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_body_metrics_user_ts ON user_body_metrics(user_id, timestamp DESC)",

    # D-04 用户运动日志
    """
    CREATE TABLE IF NOT EXISTS user_exercise_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        session_id TEXT,
        exercise_type TEXT NOT NULL,
        reps INTEGER DEFAULT 0,
        sets INTEGER DEFAULT 1,
        duration_seconds REAL DEFAULT 0,
        avg_form_score REAL,
        calories_kcal REAL,
        performed_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exercise_log_user_ts ON user_exercise_log(user_id, performed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_exercise_log_session ON user_exercise_log(session_id)",

    # D-05 设备绑定
    """
    CREATE TABLE IF NOT EXISTS device_user_binding (
        device_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        token TEXT,
        bound_at REAL NOT NULL DEFAULT (julianday('now')),
        last_used_at REAL,
        active INTEGER DEFAULT 1,
        PRIMARY KEY (device_id, user_id),
        FOREIGN KEY (device_id) REFERENCES devices(device_id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_binding_user ON device_user_binding(user_id, active)",
    "CREATE INDEX IF NOT EXISTS idx_binding_token ON device_user_binding(token)",

    # B-09 pose_data 查询索引
    "CREATE INDEX IF NOT EXISTS idx_pose_session_ts ON pose_data(session_id, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pose_exercise_ts ON pose_data(exercise_type, timestamp DESC)",

    # D-06 每日训练汇总
    """
    CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        total_reps INTEGER DEFAULT 0,
        total_sets INTEGER DEFAULT 0,
        total_duration_sec REAL DEFAULT 0,
        avg_form_score REAL,
        total_calories REAL DEFAULT 0,
        exercises_done INTEGER DEFAULT 0,
        sessions_count INTEGER DEFAULT 0,
        updated_at REAL NOT NULL,
        UNIQUE(user_id, date),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_summary_user_date ON daily_summary(user_id, date DESC)",
]

# 与 V3 相关的表，用于报告
V3_TABLES = [
    "user_body_metrics",
    "user_exercise_log",
    "device_user_binding",
    "daily_summary",
]


def _resolve_db_path(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("FITNESS_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    # 默认: backend/migrate_v3.py 的上级目录下的 fitness_v2.db
    here = Path(__file__).resolve().parent
    return (here.parent / "fitness_v2.db").resolve()


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
    except sqlite3.Error:
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Fitness V3 DB migration")
    parser.add_argument("--db", help="SQLite DB path (default: ../fitness_v2.db)")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的 DDL")
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db)
    if not db_path.exists():
        print(f"[warn] DB 文件不存在，将创建空库: {db_path}")

    if args.dry_run:
        print(f"[dry-run] target DB: {db_path}")
        for ddl in DDLS:
            print(ddl.strip())
            print("---")
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        before = _list_tables(conn)
        print(f"[migrate_v3] DB: {db_path}")
        print(f"[migrate_v3] tables before: {len(before)}")

        applied = 0
        skipped = 0
        for ddl in DDLS:
            try:
                conn.execute(ddl)
                applied += 1
            except sqlite3.OperationalError as e:
                # 索引依赖的主表可能在空库/老库上不存在，跳过不退出
                if "no such table" in str(e).lower():
                    print(f"[skip] {e} — 底表不存在，延后当主表创建后再跳。")
                    skipped += 1
                    continue
                print(f"[error] DDL failed: {e}\n{ddl.strip()[:120]}")
                return 2
            except sqlite3.Error as e:
                print(f"[error] DDL failed: {e}\n{ddl.strip()[:120]}")
                return 2

        conn.commit()

        after = _list_tables(conn)
        added = sorted(set(after) - set(before))
        print(f"[migrate_v3] applied {applied} DDL statements, skipped {skipped}")
        if added:
            print(f"[migrate_v3] new tables: {added}")
        else:
            print("[migrate_v3] no new tables (all existed)")

        # V3 表统计
        print("\n=== V3 tables status ===")
        for t in V3_TABLES:
            if t in after:
                cnt = _row_count(conn, t)
                print(f"  [OK]  {t:25s}  rows={cnt}")
            else:
                print(f"  [--]  {t:25s}  MISSING")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
