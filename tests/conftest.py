"""Shared fixtures for the World Stage test suite.

Requires a running PostgreSQL server with a ``worldstage`` database
whose schema will be copied (schema-only, no data) into a disposable
``worldstage_test`` database for each session.
"""

import hashlib
import os
import subprocess

import psycopg
import pytest
from psycopg.rows import dict_row

from world_stage import create_app

TEST_DB = "worldstage_test"
SOURCE_DB = os.environ.get("TEST_SOURCE_DB", "worldstage")

# Connection string pointing at the *maintenance* database so we can
# CREATE / DROP the test database itself.
_MAINTENANCE_DSN = os.environ.get("TEST_MAINTENANCE_DSN", "dbname=postgres")


# ── session-scoped: create & destroy the test database ──────────────

@pytest.fixture(scope="session")
def _test_db():
    """Create the test database from a schema-only dump of the source DB."""
    maint = psycopg.connect(_MAINTENANCE_DSN, autocommit=True)
    maint.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    maint.execute(f"CREATE DATABASE {TEST_DB}")
    maint.close()

    dump = subprocess.run(
        ["pg_dump", "--schema-only", SOURCE_DB],
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        ["psql", "-q", TEST_DB],
        input=dump.stdout, capture_output=True, text=True, check=True,
    )

    # Copy migration entries from source so migrate_db() only runs new ones.
    conn = psycopg.connect(f"dbname={TEST_DB}")
    source = psycopg.connect(f"dbname={SOURCE_DB}")
    with conn.cursor() as cur, source.cursor() as scur:
        scur.execute("SELECT name FROM migration ORDER BY id")
        for row in scur.fetchall():
            cur.execute(
                "INSERT INTO migration (name) VALUES (%s) ON CONFLICT DO NOTHING",
                (row[0],),
            )
    source.close()
    conn.commit()
    conn.close()

    # Run any pending migrations (e.g. new ones not yet applied to source).
    app = create_app({"TESTING": True, "DATABASE_URI": f"dbname={TEST_DB}"})
    with app.app_context():
        from world_stage.db import migrate_db
        migrate_db()

    yield f"dbname={TEST_DB}"

    # Terminate any lingering connections before dropping.
    maint = psycopg.connect(_MAINTENANCE_DSN, autocommit=True)
    maint.execute(f"""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = '{TEST_DB}' AND pid <> pg_backend_pid()
    """)
    maint.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    maint.close()


# ── session-scoped: seed minimal reference rows ─────────────────────

@pytest.fixture(scope="session")
def _seeded_db(_test_db):
    """Insert the minimum rows needed for song API tests."""
    conn = psycopg.connect(_test_db, row_factory=dict_row)

    with conn.cursor() as cur:
        # Countries
        cur.execute("""
            INSERT INTO country (id, name, is_participating, cc3)
            VALUES ('US', 'United States', true, 'USA'),
                   ('ES', 'Spain', true, 'ESP'),
                   ('FR', 'France', true, 'FRA')
            ON CONFLICT DO NOTHING
        """)

        # Year (open for submissions, closed = 0)
        cur.execute("""
            INSERT INTO year (id, closed, host_id)
            VALUES (2025, 0, 'US')
            ON CONFLICT DO NOTHING
        """)

        # A closed year (for deletion-restriction tests)
        cur.execute("""
            INSERT INTO year (id, closed, host_id)
            VALUES (2024, 1, 'ES')
            ON CONFLICT DO NOTHING
        """)

        # Languages
        cur.execute("""
            INSERT INTO language (id, name, tag)
            VALUES (20, 'English', 'en'),
                   (30, 'Spanish', 'es'),
                   (40, 'French', 'fr')
            ON CONFLICT DO NOTHING
        """)

        # Accounts – passwords are irrelevant; we authenticate via API tokens.
        cur.execute("""
            INSERT INTO account (id, username, email, password, salt, approved, role)
            VALUES (1, 'alice', 'alice@test', '\\x00', '\\x00', 1, 'admin'),
                   (2, 'bob',   'bob@test',   '\\x00', '\\x00', 1, 'user'),
                   (3, 'carol', 'carol@test', '\\x00', '\\x00', 1, 'user')
            ON CONFLICT DO NOTHING
        """)

        # API tokens – plain-text tokens hashed with SHA-256
        for uid, token in [(1, "token-alice"), (2, "token-bob"), (3, "token-carol")]:
            h = hashlib.sha256(token.encode()).digest()
            cur.execute(
                "INSERT INTO api_token (user_id, token_hash, label) VALUES (%s, %s, 'test') ON CONFLICT DO NOTHING",
                (uid, h),
            )

    conn.commit()
    conn.close()
    return _test_db


# ── function-scoped: Flask app & test client ────────────────────────

@pytest.fixture()
def app(_seeded_db):
    app = create_app({"TESTING": True, "DATABASE_URI": _seeded_db})
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


# ── convenience: DB connection for direct queries in tests ──────────

@pytest.fixture()
def db(_seeded_db):
    conn = psycopg.connect(_seeded_db, row_factory=dict_row)
    yield conn
    conn.rollback()
    conn.close()


# ── auth helpers ────────────────────────────────────────────────────

@pytest.fixture()
def alice_headers():
    """Authorization headers for alice (admin)."""
    return {"Authorization": "Bearer token-alice", "Content-Type": "application/json"}


@pytest.fixture()
def bob_headers():
    """Authorization headers for bob (regular user)."""
    return {"Authorization": "Bearer token-bob", "Content-Type": "application/json"}


@pytest.fixture()
def carol_headers():
    """Authorization headers for carol (regular user)."""
    return {"Authorization": "Bearer token-carol", "Content-Type": "application/json"}


# ── cleanup: remove songs between tests ─────────────────────────────

@pytest.fixture(autouse=True)
def _clean_songs(_seeded_db):
    """Delete all songs (and their languages) after each test."""
    yield
    conn = psycopg.connect(_seeded_db)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM song_language")
        cur.execute("DELETE FROM song_audit_log")
        cur.execute("DELETE FROM song")
    conn.commit()
    conn.close()
