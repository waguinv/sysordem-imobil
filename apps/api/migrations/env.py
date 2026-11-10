import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base

config = context.config
url = os.environ.get("MIGRATION_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("Defina MIGRATION_DATABASE_URL (papel dono das tabelas) para rodar migrations")
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
