"""Version 1 declarations. Keep immutable: later changes get a new Alembic revision.

Legacy SQLite did not enable FK enforcement. This first migration preserves that
behavior; relationship audits must pass before an operator switches production.
"""
import json
from pathlib import Path
import sqlalchemy as sa

SPEC = json.loads(Path(__file__).with_name('schema_v1.json').read_text(encoding='utf-8'))
EXTRA_REP_COLUMNS = [('landmarks','TEXT'),('model_quality','REAL'),('evidence_score','REAL'),('evidence_deficits','TEXT'),('evidence_used','TEXT')]

MEDIA_SPEC = dict(columns=[dict(name=name,type=typ,nullable=nullable,default=default,pk=pk) for name,typ,nullable,default,pk in [
    ('resource_key','TEXT',False,None,1),('user_id','INTEGER',False,None,0),('session_id','TEXT',True,None,0),('backend','TEXT',False,None,0),('status','TEXT',False,None,0),('attempts','INTEGER',False,'0',0),('sha256','TEXT',False,None,0),('byte_size','INTEGER',False,None,0),('error_type','TEXT',True,None,0),('created_at','REAL',False,None,0),('updated_at','REAL',False,None,0)]],indexes=[dict(name='idx_media_pending',unique=False,origin='c',columns=['status','updated_at'])],autoincrement=False,relations=[dict(column='user_id',table='users',target='id'),dict(column='session_id',table='sessions',target='session_id')])

def current_spec(version=3):
    spec=json.loads(json.dumps(SPEC))
    spec['rep_scores']['columns'].extend(dict(name=name,type=typ,nullable=True,default=None,pk=0) for name,typ in EXTRA_REP_COLUMNS)
    if version>=3:
        spec['media_objects']=MEDIA_SPEC
        spec['devices']['columns'].extend(dict(name=name,type=typ,nullable=True,default=None,pk=0) for name,typ in [('token','TEXT'),('registered_at','REAL')])
    return spec


def metadata(dialect='postgresql', version=1):
    meta = sa.MetaData()
    for name, spec in (SPEC if version==1 else current_spec(version)).items():
        columns = []
        for c in spec['columns']:
            typ = sa.Integer if c['type'] == 'INTEGER' else sa.Double if c['type'] == 'REAL' else sa.Text
            default = c['default']
            if default and 'julianday' in default:
                default = "(EXTRACT(EPOCH FROM CURRENT_TIMESTAMP))" if dialect == 'postgresql' else "(unixepoch())"
            columns.append(sa.Column(c['name'], typ(), primary_key=bool(c['pk']),
                nullable=False if c['pk'] else c['nullable'],
                autoincrement=True if spec['autoincrement'] and c['pk'] else 'auto',
                server_default=sa.text(default) if default is not None else None))
        table = sa.Table(name, meta, *columns, sqlite_autoincrement=spec['autoincrement'])
        for idx in spec['indexes']:
            if idx['origin'] == 'pk':
                continue
            fields = [table.c[x] for x in idx['columns']]
            if idx['origin'] == 'u':
                table.append_constraint(sa.UniqueConstraint(*fields))
            else:
                sa.Index(idx['name'], *fields, unique=idx['unique'])
    return meta


def initialize_sqlite(engine):
    meta = metadata('sqlite', version=3)
    with engine.begin() as conn:
        # SQLite serializes DDL across processes before any check/create race.
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        meta.create_all(conn)
        for name, spec in current_spec().items():
            have = {c['name'] for c in sa.inspect(conn).get_columns(name)}
            for col in spec['columns']:
                if col['name'] not in have:
                    # Additive evolution only. Never rebuild/drop a user's table.
                    sql = f'ALTER TABLE "{name}" ADD COLUMN "{col["name"]}" {col["type"]}'
                    if col['default'] is not None and 'julianday' not in col['default']:
                        sql += ' DEFAULT ' + col['default']
                    conn.exec_driver_sql(sql)
        for table in meta.tables.values():
            for index in table.indexes:
                index.create(conn, checkfirst=True)
