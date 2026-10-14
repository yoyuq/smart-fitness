"""Preserve optional score evidence present in existing local databases."""
from alembic import op
import sqlalchemy as sa
from storage.schema import EXTRA_REP_COLUMNS
revision='storage_0002'
down_revision='storage_0001'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind()
    have={c['name'] for c in sa.inspect(conn).get_columns('rep_scores')}
    for name,typ in EXTRA_REP_COLUMNS:
        if name not in have:
            op.add_column('rep_scores',sa.Column(name,sa.Text() if typ=='TEXT' else sa.Double(),nullable=True))

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; use verified data recovery')
