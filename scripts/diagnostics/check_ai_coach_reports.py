"""Check AI coach report archive counts to verify 6-report state."""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "backend" / "fitness.db"

print(f"DB path: {DB}  exists={DB.exists()}")
if not DB.exists():
    raise SystemExit("DB missing")

conn = sqlite3.connect(str(DB))
c = conn.cursor()

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in c.fetchall()]
print("\nAll tables:")
for t in tables:
    print(f"  - {t}")

# Look for reports/coach/ai tables
candidates = [t for t in tables if any(k in t.lower() for k in ("report", "coach", "analysis", "ai"))]
print(f"\nCandidate tables: {candidates}")

for t in candidates:
    c.execute(f"SELECT COUNT(*) FROM {t}")
    n = c.fetchone()[0]
    print(f"\n== {t} (rows={n}) ==")
    c.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in c.fetchall()]
    print(f"cols: {cols}")
    if n > 0:
        # Show latest
        try:
            c.execute(f"SELECT * FROM {t} ORDER BY id DESC LIMIT 10")
        except sqlite3.OperationalError:
            c.execute(f"SELECT * FROM {t} LIMIT 10")
        for row in c.fetchall():
            print(row)

# Also dump collage/report file counts
for sub in ("collages", "reports", "rep_frames", "rep_clips", "monitor"):
    d = ROOT / "backend" / "data" / sub
    if d.exists():
        try:
            files = list(d.rglob("*"))
        except Exception:
            files = []
        files_only = [f for f in files if f.is_file()]
        print(f"\nfs backend/data/{sub}: total_files={len(files_only)}")
        for f in sorted(files_only)[-6:]:
            print(f"  {f.relative_to(ROOT)}")

conn.close()
