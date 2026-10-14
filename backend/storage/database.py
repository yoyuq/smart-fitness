"""One database configuration for API, jobs, MCP and training tools."""
import os
import sqlite3
import re
from functools import lru_cache
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]

def default_sqlite_path():
    return str(Path(os.environ.get("DB_PATH") or os.environ.get("FITNESS_DB_PATH") or BACKEND_ROOT / "fitness.db").expanduser().resolve())

def database_source(source=None):
    return os.environ.get("DATABASE_URL") or source or default_sqlite_path()

def connect(source=None, timeout=10, check_same_thread=True):
    source=database_source(source)
    if str(source).startswith(('postgresql://', 'postgresql+psycopg://')):
        return Connection(source)
    if "://" in str(source):
        if not str(source).startswith("sqlite:///"):
            raise ValueError("This database URL is not supported")
        source=str(source)[10:]
    if str(source) != ":memory:":
        Path(source).expanduser().resolve().parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(source), timeout=timeout, check_same_thread=check_same_thread)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=" + str(int(timeout*1000)))
    return conn


def database_url(source=None):
    source = str(database_source(source))
    if source.startswith('postgresql://'):
        source = source.replace('postgresql://', 'postgresql+psycopg://', 1)
    if '://' not in source:
        return 'sqlite:///' + Path(source).expanduser().resolve().as_posix()
    url = make_url(source)
    if url.drivername not in ('sqlite', 'postgresql+psycopg'):
        raise ValueError('Only SQLite and PostgreSQL are supported')
    return source


@lru_cache(maxsize=8)
def engine_for(url):
    kwargs = dict(pool_pre_ping=True, hide_parameters=True)
    if url.startswith('postgresql'):
        kwargs.update(pool_size=3, max_overflow=2, connect_args={'connect_timeout': 10})
    return sa.create_engine(url, **kwargs)


def initialize(source=None):
    engine = engine_for(database_url(source))
    if engine.dialect.name == 'sqlite':
        from .schema import initialize_sqlite
        initialize_sqlite(engine)
    else:
        # Deployment owns DDL. Request paths never auto-upgrade PostgreSQL.
        with engine.connect() as conn:
            if conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() != 'storage_0001':
                raise RuntimeError('Run python -m storage upgrade before starting the backend')


class Row:
    def __init__(self, values, keys):
        self.values, self.names = tuple(values), tuple(keys)
    def __getitem__(self, key):
        return self.values[self.names.index(key)] if isinstance(key, str) else self.values[key]
    def __iter__(self):
        return iter(self.values)
    def __len__(self):
        return len(self.values)
    def keys(self):
        return list(self.names)


class Cursor:
    def __init__(self, conn, result=None, lastrowid=None):
        self.conn, self.result, self.lastrowid = conn, result, lastrowid
        self.rowcount = result.rowcount if result is not None else 0
    def execute(self, sql, parameters=()):
        other = self.conn.execute(sql, parameters)
        self.result, self.lastrowid, self.rowcount = other.result, other.lastrowid, other.rowcount
        return self
    def fetchone(self):
        if self.result is None or not self.result.returns_rows:
            return None
        row = self.result.fetchone()
        return Row(row, self.result.keys()) if row is not None else None
    def fetchall(self):
        if self.result is None or not self.result.returns_rows:
            return []
        keys = self.result.keys()
        return [Row(r, keys) for r in self.result.fetchall()]
    def __iter__(self):
        return iter(self.fetchall())
    def close(self):
        if self.result is not None:
            self.result.close()


def bind_qmarks(sql, parameters):
    """Lex placeholders outside SQL literals/comments; never interpolate values."""
    out, i, index, quote = [], 0, 0, None
    while i < len(sql):
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(sql[i + 1]); i += 1
                else:
                    quote = None
        elif ch in "'\"":
            quote = ch; out.append(ch)
        elif sql[i:i+2] == '--':
            end = sql.find('\n', i)
            end = len(sql) if end < 0 else end
            out.append(sql[i:end]); i = end; continue
        elif sql[i:i+2] == '/*':
            end = sql.find('*/', i+2)
            if end < 0:
                raise ValueError('Unterminated SQL comment')
            out.append(sql[i:end+2]); i = end+2; continue
        elif ch == '?':
            out.append(f':p{index}'); index += 1
        else:
            out.append(ch)
        i += 1
    if index != len(parameters):
        raise ValueError('SQL parameter count mismatch')
    return ''.join(out), {f'p{i}': value for i, value in enumerate(parameters)}


class Connection:
    """Bounded legacy DB-API bridge over SQLAlchemy Core, PostgreSQL only.

    Keeps qmark/positional-row call sites; versioned schema owns all legacy DDL.
    Savepoints preserve SQLite's statement-error behavior without hiding errors.
    """
    def __init__(self, source):
        self.source = source
        self.raw = engine_for(database_url(source)).connect()
        self.total_changes = 0
        self.row_factory = None
    def cursor(self):
        return Cursor(self)
    def execute(self, sql, parameters=()):
        from .schema import SPEC
        ddl = re.match(r'\s*CREATE TABLE IF NOT EXISTS\s+(\w+)', sql, re.I)
        if ddl and ddl[1] in SPEC:
            if not sa.inspect(self.raw).has_table(ddl[1]):
                raise RuntimeError('Database schema is missing; run storage upgrade')
            return Cursor(self)
        alteration = re.match(r'\s*ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)', sql, re.I)
        if alteration and alteration[1] in SPEC:
            if alteration[2] in {c['name'] for c in sa.inspect(self.raw).get_columns(alteration[1])}:
                return Cursor(self)
            raise RuntimeError('Column is missing; run storage upgrade')
        if re.match(r'\s*PRAGMA journal_mode', sql, re.I):
            return Cursor(self)
        if 'PRAGMA' in sql.upper() or 'sqlite_master' in sql.lower() or re.search(r'INSERT\s+OR\s+', sql, re.I):
            raise ValueError('SQLite-only statement must use a storage helper')
        # Explicit legacy calendar expressions: preserve configured local timezone.
        for fn, fmt in [('date', 'YYYY-MM-DD'), ('datetime', 'YYYY-MM-DD HH24:MI:SS')]:
            sql = re.sub(r"\b"+fn+r"\(created_at,\s*'unixepoch',\s*'localtime'\)",
                         "to_char(to_timestamp(created_at) AT TIME ZONE :calendar_tz, '"+fmt+"')", sql)
        sql = sql.replace("julianday('now')", 'EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)')
        bound, args = bind_qmarks(sql, parameters)
        if ':calendar_tz' in bound:
            args['calendar_tz'] = os.getenv('APP_TIMEZONE', 'Asia/Shanghai')
        inserted = re.match(r'\s*INSERT INTO\s+(\w+)\s*\(', bound, re.I)
        pk = None
        if inserted and inserted[1] in SPEC and SPEC[inserted[1]]['autoincrement'] and not re.search(r'\bRETURNING\b', bound, re.I):
            pk = next(c['name'] for c in SPEC[inserted[1]]['columns'] if c['pk'])
            bound = bound.rstrip().rstrip(';') + f' RETURNING "{pk}"'
        with self.raw.begin_nested():
            result = self.raw.execute(sa.text(bound), args)
            row = result.fetchone() if pk else None
        self.total_changes += max(0, result.rowcount)
        return Cursor(self, result, row[0] if row else None)
    def executemany(self, sql, rows):
        cur = None
        for row in rows:
            cur = self.execute(sql, row)
        return cur or Cursor(self)
    def executescript(self, sql):
        # Repository DDL only; SQLite's script API never binds user content.
        statement = ''
        for char in sql:
            statement += char
            if char == ';' and sqlite3.complete_statement(statement):
                self.execute(statement); statement = ''
        if statement.strip():
            self.execute(statement)
    def commit(self):
        self.raw.commit()
    def rollback(self):
        self.raw.rollback()
    def close(self):
        self.raw.close()
    def __enter__(self):
        return self
    def __exit__(self, typ, value, traceback):
        self.rollback() if typ else self.commit()


def table_exists(conn, name):
    if isinstance(conn, Connection):
        return sa.inspect(conn.raw).has_table(name)
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def column_names(conn, name):
    if not re.fullmatch(r'[A-Za-z_]\w*', name):
        raise ValueError('Invalid table identifier')
    if isinstance(conn, Connection):
        return {col['name'] for col in sa.inspect(conn.raw).get_columns(name)}
    return {r[1] for r in conn.execute(f'PRAGMA table_info({name})')}

def connection_source(conn):
    source=getattr(conn,"source",None)
    if source:
        return source
    row=conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row and len(row)>2 and row[2] else None
