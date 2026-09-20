"""Database connection and helpers."""

import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

from . import config


@contextmanager
def get_connection():
    """Get a database connection. Use as context manager."""
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set in environment")
    conn = psycopg2.connect(config.DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_cursor(commit=False):
    """Get a cursor. Optionally commit on success."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def init_schema():
    """Create all tables from schema.sql. Idempotent."""
    schema_sql = config.SCHEMA_PATH.read_text()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()


def execute(sql, params=None, commit=False):
    """Execute a single SQL statement."""
    with get_cursor(commit=commit) as cur:
        cur.execute(sql, params)
        if cur.description:
            return cur.fetchall()
        return None


def execute_many(sql, params_list, commit=True):
    """Execute a SQL statement with multiple parameter sets."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
        if commit:
            conn.commit()


def fetch_one(sql, params=None):
    """Fetch a single row."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(sql, params=None):
    """Fetch all rows."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
