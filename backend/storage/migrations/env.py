from alembic import context
from sqlalchemy import create_engine, pool
from storage.database import database_url

engine = create_engine(context.config.attributes.get('database_url') or database_url(), poolclass=pool.NullPool, hide_parameters=True)
with engine.begin() as conn:
    if conn.dialect.name == 'sqlite':
        conn.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        conn.exec_driver_sql('SELECT pg_advisory_xact_lock(736510024)')
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()
