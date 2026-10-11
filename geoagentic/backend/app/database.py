"""
Database configuration.

Default: SQLite file (geoagentic.db) — zero setup, works out of the box.
Production: point DATABASE_URL at Postgres/MySQL, e.g.
    DATABASE_URL=postgresql://user:pass@localhost:5432/geoagentic
No other code needs to change; SQLAlchemy handles the dialect switch.
See schema.sql at the project root for the equivalent portable DDL.
"""
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

# Absolute path next to the backend folder, so it does not matter which
# directory uvicorn was started from (a relative path silently created a
# fresh empty DB per working directory).
_DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geoagentic.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DB_FILE}")

connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_sqlite_schema():
    """Add columns needed by newer app versions without replacing user data."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    additions = {
        "route_options": {
            "traffic_aware": "BOOLEAN NOT NULL DEFAULT 0",
            "traffic_status": "VARCHAR DEFAULT 'unknown'",
        },
        "incidents": {
            "source": "VARCHAR DEFAULT 'simulation'",
            "external_id": "VARCHAR",
            "geometry": "JSON",
            "description": "TEXT",
            "delay_s": "FLOAT",
            "icon_category": "INTEGER",
            "source_data": "JSON",
            "first_seen_at": "FLOAT",
            "last_seen_at": "FLOAT",
            "last_checked_at": "FLOAT",
            "freshness": "VARCHAR DEFAULT 'current'",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {ddl}'))
        if inspector.has_table("incidents"):
            connection.execute(text(
                "UPDATE incidents SET first_seen_at = created_at "
                "WHERE first_seen_at IS NULL"
            ))
            connection.execute(text(
                "UPDATE incidents SET last_seen_at = created_at "
                "WHERE last_seen_at IS NULL"
            ))
            connection.execute(text(
                "UPDATE incidents SET last_checked_at = created_at "
                "WHERE last_checked_at IS NULL"
            ))
