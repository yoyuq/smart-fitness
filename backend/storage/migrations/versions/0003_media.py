"""Durable private-media catalogue and retry state."""
from alembic import op
from storage.schema import metadata
import sqlalchemy as sa
revision='storage_0003'
down_revision='storage_0002'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind()
    metadata(conn.dialect.name,version=3).tables['media_objects'].create(conn,checkfirst=True)
    have={c['name'] for c in sa.inspect(conn).get_columns('devices')}
    for name,typ in [('token',sa.Text()),('registered_at',sa.Double())]:
        if name not in have:op.add_column('devices',sa.Column(name,typ,nullable=True))

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; preserve media catalogue and objects')
