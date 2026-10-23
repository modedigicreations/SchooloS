import contextlib
import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "schoolos.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def db_path() -> Path:
    return Path(os.environ.get("SCHOOLOS_DB", str(DEFAULT_DB_PATH)))


def get_connection() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextlib.contextmanager
def connect():
    """Context manager that opens a connection and always closes it on exit."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {db_path()}")
