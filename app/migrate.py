from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import get_settings
from app.database import engine


def run_migrations() -> None:
    """Apply Alembic migrations once, even when API and worker deploy together."""
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

    if engine.dialect.name != "postgresql":
        command.upgrade(config, "head")
        return

    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(918273645)"))
        try:
            command.upgrade(config, "head")
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(918273645)"))

