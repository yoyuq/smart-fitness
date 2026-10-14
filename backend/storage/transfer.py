"""Consistent SQLite snapshots, loss-detecting import, and recovery export.

No credentials or row contents are included in reports. Production apply requires
stopped writers; online backup alone cannot synchronize two independent files.
"""
import ast
import hashlib
import json
import sqlite3
from pathlib import Path
import sqlalchemy as sa
from .schema import current_spec, metadata, initialize_sqlite
SPEC = current_spec()


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination:
        raise ValueError('Snapshot destination must be a new file')
    if not source.is_file():
        raise ValueError('SQLite source does not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Snapshot integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {'file':destination.name,'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}


def _value(value, column):
    if value is None:
        return None
    typ=column['type']
    if typ=='INTEGER':
        number=int(value)
        if float(value)!=number:
            raise ValueError('Non-integral value in INTEGER column')
        return number
    if typ=='REAL':
        return float(value)
    if isinstance(value, bytes):
        raise ValueError('BLOB content in a TEXT column; explicit conversion required')
    return str(value)


def _default(column):
    raw=column['default']
    if raw is None:
        if column['pk'] or not column['nullable']:
            raise ValueError('Missing required source column '+column['name'])
        return None
    if raw.upper()=='NULL':
        return None
    try:
        return _value(ast.literal_eval(raw), column)
    except (SyntaxError, ValueError, TypeError):
        raise ValueError('Missing dynamic-default column '+column['name']+'; explicit resolution required') from None


def _digest(rows):
    lines=sorted(json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False) for r in rows)
    return hashlib.sha256(('\n'.join(lines)).encode()).hexdigest()


def classify(sources):
    tables={}
    report={'sources':[],'tables':{},'relations':[]}
    for source in sources:
        source=Path(source).resolve()
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Source integrity failed')
            names=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'")]
            unknown=set(names)-set(SPEC)
            if unknown:
                raise ValueError('Unrecognized source tables: '+','.join(sorted(unknown)))
            source_report={'file':source.name,'tables':{}}
            for name in names:
                spec=SPEC[name]; columns={c['name']:c for c in spec['columns']}
                actual={r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
                extra=actual-set(columns)
                if extra:
                    raise ValueError('Unrecognized columns in '+name+': '+','.join(sorted(extra)))
                pk=[c['name'] for c in spec['columns'] if c['pk']]
                dest=tables.setdefault(name,{})
                added=duplicates=0
                for row in conn.execute(f'SELECT * FROM "{name}"'):
                    normalized={key:_value(row[key],c) if key in actual else _default(c) for key,c in columns.items()}
                    key=tuple(normalized[k] for k in pk)
                    if any(v is None for v in key):
                        raise ValueError('NULL primary key in '+name)
                    if key in dest:
                        if dest[key]!=normalized:
                            raise ValueError('Conflicting same-ID rows in '+name+'; explicit reconciliation required')
                        duplicates+=1
                    else:
                        dest[key]=normalized;added+=1
                source_report['tables'][name]={'unique_added':added,'identical_duplicates':duplicates}
            report['sources'].append(source_report)
    for name, rows in tables.items():
        rows=list(rows.values());tables[name]=rows
        report['tables'][name]={'count':len(rows),'sha256':_digest(rows)}
    for name,spec in SPEC.items():
        relations=list(spec['relations'])
        if name in ('sessions','exercise_log'):
            relations.append({'column':'user_id','table':'users','target':'id'})
        for rel in relations:
            refs={str(r[rel['target']]) for r in tables.get(rel['table'],[]) if r[rel['target']] is not None}
            orphan=sum(r[rel['column']] is not None and str(r[rel['column']]) not in refs for r in tables.get(name,[]))
            report['relations'].append({'table':name,'column':rel['column'],'parent':rel['table'],'orphans':orphan})
    return tables,report


def import_sqlite(sources, target_url, *, allow_legacy_orphans=False, batch_size=500):
    if not 1<=batch_size<=5000:
        raise ValueError('batch_size must be between 1 and 5000')
    tables,report=classify(sources)
    orphan_count=sum(r['orphans'] for r in report['relations'])
    if orphan_count and not allow_legacy_orphans:
        raise ValueError('Existing orphan relationships detected; inspect classify report before explicit preservation')
    engine=sa.create_engine(target_url,hide_parameters=True)
    meta=metadata(engine.dialect.name,version=2)
    try:
        with engine.begin() as conn:
            actual=set(sa.inspect(conn).get_table_names())
            if not set(SPEC)<=actual:
                raise ValueError('Target schema not ready; run storage upgrade')
            if engine.dialect.name=='postgresql':
                conn.exec_driver_sql('LOCK TABLE '+','.join('"'+n+'"' for n in SPEC)+' IN ACCESS EXCLUSIVE MODE')
            elif engine.dialect.name=='sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:raise ValueError('Unsupported migration target')
            for table in meta.tables.values():
                if conn.execute(sa.select(sa.func.count()).select_from(table)).scalar():
                    raise ValueError('Target business tables must be empty; refusing a live merge')
            for name,rows in tables.items():
                table=meta.tables[name]
                for offset in range(0,len(rows),batch_size):
                    conn.execute(table.insert(),rows[offset:offset+batch_size])
                restored=[dict(r) for r in conn.execute(sa.select(table)).mappings()]
                if len(restored)!=len(rows) or _digest(restored)!=_digest(rows):
                    raise ValueError('Content verification failed for '+name)
            if engine.dialect.name=='postgresql':
                for name,spec in SPEC.items():
                    if not spec['autoincrement']:continue
                    pk=next(c['name'] for c in spec['columns'] if c['pk'])
                    maximum=conn.exec_driver_sql(f'SELECT max("{pk}") FROM "{name}"').scalar()
                    conn.execute(sa.text("SELECT setval(pg_get_serial_sequence(:table,:column), :maximum, :called)"),
                        {'table':name,'column':pk,'maximum':max(1,maximum or 1),'called':bool(maximum and maximum>=1)})
        report.update(verified=True,legacy_orphans_preserved=orphan_count)
        return report
    finally:engine.dispose()


def export_recovery(source_url, destination):
    """Full committed-state export preserves writes made after PostgreSQL cutover.

    The old SQLite backup is never overwritten. Writers must remain stopped from
    export through backend switch; otherwise later writes are outside this snapshot.
    """
    destination=Path(destination).resolve()
    if destination.exists():
        raise ValueError('Recovery destination must be new')
    destination.parent.mkdir(parents=True,exist_ok=True)
    source=sa.create_engine(source_url,hide_parameters=True)
    target=sa.create_engine('sqlite:///'+destination.as_posix())
    meta=metadata('sqlite',version=2)
    report={'tables':{}}
    try:
        initialize_sqlite(target)
        with source.connect().execution_options(isolation_level='REPEATABLE READ' if source.dialect.name=='postgresql' else 'SERIALIZABLE') as src:
            with src.begin():
                if source.dialect.name=='postgresql':src.exec_driver_sql('SET TRANSACTION READ ONLY')
                unknown=set(sa.inspect(src).get_table_names())-set(SPEC)-{'alembic_version'}
                if unknown:raise ValueError('Unknown recovery tables require explicit preservation')
                with target.begin() as dst:
                    for name,table in meta.tables.items():
                        rows=[dict(r) for r in src.execute(sa.select(table)).mappings()]
                        for offset in range(0,len(rows),500):
                            dst.execute(table.insert(),rows[offset:offset+500])
                        restored=[dict(r) for r in dst.execute(sa.select(table)).mappings()]
                        if _digest(rows)!=_digest(restored):raise ValueError('Recovery content mismatch for '+name)
                        report['tables'][name]={'count':len(rows),'sha256':_digest(rows)}
        report['verified']=True
        return report
    except Exception:
        target.dispose()
        destination.unlink(missing_ok=True)
        raise
    finally:
        source.dispose();target.dispose()
