import sqlite3
import os
from contextlib import contextmanager
from config.settings import settings

def get_connection():
    """Returns a connection to the SQLite database with dictionary row factory."""
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

@contextmanager
def transaction():
    """Context manager for executing transactions with automatic commit/rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    """Reads schema.sql and initializes the database tables."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()
    
    with transaction() as conn:
        conn.executescript(schema_sql)
