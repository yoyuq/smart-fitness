"""Version 1 declarations. Keep immutable: later changes get a new Alembic revision.

Legacy SQLite did not enable FK enforcement. This first migration preserves that
behavior; relationship audits must pass before an operator switches production.
"""
import json
from pathlib import Path
import sqlalchemy as sa

SPEC = json.loads(Path(__file__).with_name('schema_v1.json').read_text(encoding='utf-8'))


def metadata(dialect='postgresql'):
    meta = sa.MetaData()
    for name, spec in SPEC.items():
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
    meta = metadata('sqlite')
    with engine.begin() as conn:
        meta.create_all(conn)
        for name, spec in SPEC.items():
            have = {c['name'] for c in sa.inspect(conn).get_columns(name)}
            for col in spec['columns']:
                if col['name'] not in have:
                    # Additive evolution only. Never rebuild/drop a user's table.
                    sql = f'ALTER TABLE "{name}" ADD COLUMN "{col["name"]}" {col["type"]}'
                    if col['default'] is not None and 'julianday' not in col['default']:
                        sql += ' DEFAULT ' + col['default']
                    conn.exec_driver_sql(sql)
