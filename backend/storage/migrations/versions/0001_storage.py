"""Initial unified legacy-compatible business schema."""
from alembic import op
from storage.schema import metadata, initialize_sqlite
revision = 'storage_0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'sqlite':
        # Existing SQLite schemas need additive columns before their indexes.
        from storage.schema import SPEC
        import sqlalchemy as sa
        for name, spec in SPEC.items():
            if sa.inspect(conn).has_table(name):
                have = {c['name'] for c in sa.inspect(conn).get_columns(name)}
                for c in spec['columns']:
                    if c['name'] not in have:
                        sql=f'ALTER TABLE "{name}" ADD COLUMN "{c["name"]}" {c["type"]}'
                        if c['default'] is not None and 'julianday' not in c['default']:
                            sql+=' DEFAULT '+c['default']
                        conn.exec_driver_sql(sql)
    metadata(conn.dialect.name).create_all(conn)

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; use a verified data recovery snapshot')
