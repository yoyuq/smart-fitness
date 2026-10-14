from alembic import context
from sqlalchemy import create_engine, pool
from storage.database import database_url

engine = create_engine(context.config.attributes.get('database_url') or database_url(), poolclass=pool.NullPool, hide_parameters=True)
with engine.connect() as conn:
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()
