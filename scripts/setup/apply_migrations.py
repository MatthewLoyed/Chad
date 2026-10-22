"""
Apply every SQL migration in src/infrastructure/database/migrations/, in order,
against SUPABASE_DB_URL. Used by scripts/setup/bootstrap_supabase.py (cold-start,
issue #403) but also safe to run standalone/repeatedly — every migration file is
written to be idempotent (CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
ADD COLUMN IF NOT EXISTS).

Requires SUPABASE_DB_URL in .env — the direct Postgres connection string from
Supabase dashboard → Settings → Database → Connection string (URI, "Session"
mode). This is NOT the same as SUPABASE_URL (the REST API host) or the anon/
service API keys; it's a real postgres:// connection with the DB password.

Usage:
    $env:PYTHONPATH="."; .\\.venv\\Scripts\\python.exe scripts\\setup\\apply_migrations.py
"""

import logging
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ApplyMigrations")

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "infrastructure" / "database" / "migrations"


def apply_all(conn_string: str) -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No .sql files found under {MIGRATIONS_DIR}")

    with psycopg.connect(conn_string, autocommit=False) as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            logger.info(f"Applying {path.name}...")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration {path.name} failed: {e}") from e
    logger.info(f"Applied {len(files)} migration file(s) successfully.")


if __name__ == "__main__":
    load_dotenv()
    conn_string = os.getenv("SUPABASE_DB_URL")
    if not conn_string:
        logger.error(
            "SUPABASE_DB_URL is not set. Add it to .env — Supabase dashboard → "
            "Settings → Database → Connection string (URI, 'Session' pooler mode)."
        )
        sys.exit(1)

    try:
        apply_all(conn_string)
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)
