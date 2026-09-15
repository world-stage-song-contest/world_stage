"""Shared fixtures for the World Stage test suite.

Requires a running PostgreSQL server with a ``worldstage`` database
whose schema will be copied (schema-only, no data) into a disposable
``worldstage_test`` database for each session.
"""

import hashlib
import os
import subprocess
import uuid
from contextlib import contextmanager

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
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["psql", "-q", TEST_DB],
        input=dump.stdout,
        capture_output=True,
        text=True,
        check=True,
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
    app = create_app({"TESTING": True, "LOCAL_ASSETS": True, "DATABASE_URI": f"dbname={TEST_DB}"})
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

        # Year statuses (reference rows; the schema-only dump skips them)
        cur.execute("""
            INSERT INTO year_status (name)
            VALUES ('open'), ('closed'), ('ongoing')
            ON CONFLICT DO NOTHING
        """)

        cur.execute("""
            INSERT INTO song_approval_status (name)
            VALUES ('pending'), ('pending-second-opinion'),
                   ('accepted'), ('rejected'), ('more-info')
            ON CONFLICT DO NOTHING
        """)

        cur.execute("""
            INSERT INTO show_types (id, name, sort_order)
            VALUES ('sf', 'Semi-Final', 1),
                   ('sc', 'Repechage', 2),
                   ('f', 'Final', 3)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order
        """)

        cur.execute("""
            INSERT INTO national_final_status (name)
            VALUES ('draft'), ('submissions'), ('voting'), ('finished'), ('cancelled')
            ON CONFLICT DO NOTHING
        """)

        # Test seeds that bypass the song API still create normalized artist
        # credits rather than relying on the removed song_data.artist cache.
        cur.execute("""
            CREATE OR REPLACE FUNCTION test_artist_credit(p_name text)
            RETURNS bigint
            LANGUAGE plpgsql
            AS $$
            DECLARE
                artist_id bigint;
                credit_set_id bigint;
            BEGIN
                SELECT id INTO artist_id
                FROM artist
                WHERE LOWER(full_name) = LOWER(p_name) AND number = 1;

                IF artist_id IS NULL THEN
                    INSERT INTO artist (full_name)
                    VALUES (p_name)
                    RETURNING id INTO artist_id;
                END IF;

                INSERT INTO artist_credit_set DEFAULT VALUES
                RETURNING id INTO credit_set_id;
                INSERT INTO artist_credit (
                    artist_credit_set_id, position, artist_id
                ) VALUES (credit_set_id, 1, artist_id);
                RETURN credit_set_id;
            END;
            $$;
        """)

        # Versioned voting rules are reference data. A schema-only copy of a
        # migrated source database contains their table and triggers but not
        # these rows, while its copied migration ledger marks the seed
        # migration as already applied.
        cur.execute("""
            INSERT INTO voting_ruleset (
                version, description, is_current, is_current_revote,
                penalizes_non_voters
            )
            VALUES
                ('v1', 'Ballot-flag entry is forced to receive 1 point',
                    false, false, false),
                ('v2', 'Ballot-flag entry is forbidden; other owned entries are allowed',
                    false, false, false),
                ('v3', 'Ballot-flag entry and all voter-owned entries are forbidden',
                    false, false, false),
                ('v4', 'Voter-owned entries are forbidden without a failure-to-vote penalty',
                    false, false, false),
                ('v5', 'Voter-owned entries are forbidden with a failure-to-vote penalty',
                    true, false, true),
                ('v6', 'Revote ownership exclusion with a ballot-capacity exception',
                    false, true, true)
            ON CONFLICT (version) DO UPDATE
            SET description = EXCLUDED.description,
                is_current = EXCLUDED.is_current,
                is_current_revote = EXCLUDED.is_current_revote,
                penalizes_non_voters = EXCLUDED.penalizes_non_voters
        """)

        # Year (open for submissions)
        cur.execute("""
            INSERT INTO year (id, status, submissions_open, host_id)
            VALUES (2025, 'open', true, 'US')
            ON CONFLICT DO NOTHING
        """)

        # A closed year (for deletion-restriction tests)
        cur.execute("""
            INSERT INTO year (id, status, host_id)
            VALUES (2024, 'closed', 'ES')
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

        # Account roles (reference rows; the schema-only dump skips them)
        cur.execute("""
            INSERT INTO account_role (name, can_edit, can_view_restricted)
            VALUES ('user', false, false),
                   ('editor', true, false),
                   ('admin', true, true),
                   ('owner', true, true)
            ON CONFLICT DO NOTHING
        """)

        # Accounts – passwords are irrelevant; we authenticate via API tokens.
        cur.execute("""
            INSERT INTO account (id, username, email, password, salt, approved, role)
            VALUES (1, 'alice', 'alice@test', '\\x00', '\\x00', true, 'admin'),
                   (2, 'bob',   'bob@test',   '\\x00', '\\x00', true, 'user'),
                   (3, 'carol', 'carol@test', '\\x00', '\\x00', true, 'user')
            ON CONFLICT DO NOTHING
        """)

        # API tokens – plain-text tokens hashed with SHA-256
        for uid, token in [(1, "token-alice"), (2, "token-bob"), (3, "token-carol")]:
            h = hashlib.sha256(token.encode()).digest()
            cur.execute(
                "INSERT INTO api_token (user_id, token_hash, label) VALUES (%s, %s, 'test')"
                "ON CONFLICT DO NOTHING",
                (uid, h),
            )

    conn.commit()
    conn.close()
    return _test_db


# ── function-scoped: Flask app & test client ────────────────────────


@pytest.fixture()
def app(_seeded_db):
    app = create_app({"TESTING": True, "LOCAL_ASSETS": True, "DATABASE_URI": _seeded_db})
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


@pytest.fixture()
def login(client, db):
    """Return a helper that authenticates the browser client as an account."""

    def authenticate(user_id: int = 2) -> str:
        session_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO session (user_id, session_id, expires_at)
               VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')""",
            (user_id, session_id),
        )
        db.commit()
        client.set_cookie("session", session_id)
        return session_id

    return authenticate


@pytest.fixture()
def configured_email(app):
    """Configure captured email delivery for a test."""
    app.config.update(
        MAIL_SERVER="smtp.test",
        MAIL_DEFAULT_SENDER="noreply@example.test",
        MAIL_SUPPRESS_SEND=True,
        SITE_URL="https://worldstage.example",
    )
    app.extensions["mail_outbox"].clear()


@pytest.fixture()
def isolated_example(db):
    """Return a savepoint context for generated database examples."""

    @contextmanager
    def isolate():
        db.execute("SAVEPOINT hypothesis_example")
        try:
            yield
        finally:
            db.execute("ROLLBACK TO SAVEPOINT hypothesis_example")
            db.execute("RELEASE SAVEPOINT hypothesis_example")

    return isolate


# ── cleanup: remove songs between tests ─────────────────────────────


@pytest.fixture(autouse=True)
def _clean_songs(request):
    """Reset shared database state after tests which use database-backed fixtures."""
    database_fixtures = {"_seeded_db", "app", "client", "db"}
    if database_fixtures.isdisjoint(request.fixturenames):
        yield
        return

    seeded_db = request.getfixturevalue("_seeded_db")
    yield
    conn = psycopg.connect(seeded_db)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM song_play")
        cur.execute("DELETE FROM account_avatar")
        cur.execute("DELETE FROM custom_playlist_song")
        cur.execute("DELETE FROM custom_playlist")
        cur.execute("DELETE FROM year_spot_watch")
        cur.execute("DELETE FROM message")
        cur.execute("DELETE FROM conversation_participant")
        cur.execute("DELETE FROM conversation")
        cur.execute("DELETE FROM radio_slot")
        cur.execute("DELETE FROM prediction")
        cur.execute("DELETE FROM prediction_set")
        cur.execute("DELETE FROM vote")
        cur.execute("DELETE FROM vote_set")
        cur.execute("DELETE FROM country_show_results")
        cur.execute("DELETE FROM country_year_results")
        cur.execute("DELETE FROM national_final_song")
        cur.execute("DELETE FROM song_show")
        cur.execute("DELETE FROM song_verification_comment")
        cur.execute("DELETE FROM song_status")
        cur.execute("DELETE FROM song_revision_merge")
        cur.execute("DELETE FROM song_verification_hidden_revision")
        cur.execute("DELETE FROM song_data")
        cur.execute("DELETE FROM artist_credit_set")
        cur.execute("DELETE FROM artist")
        cur.execute("DELETE FROM genre_set_subgenre")
        cur.execute("DELETE FROM genre_set")
        cur.execute("DELETE FROM key_signature_set_key_signature")
        cur.execute("DELETE FROM key_signature_set")
        cur.execute("DELETE FROM time_signature_set_time_signature")
        cur.execute("DELETE FROM time_signature_set")
        cur.execute("DELETE FROM language_set_language")
        cur.execute("DELETE FROM language_set")
        cur.execute("DELETE FROM song")
        cur.execute("DELETE FROM show_result_refresh_queue")
        cur.execute("DELETE FROM show_qualifier")
        cur.execute("DELETE FROM show_progression")
        cur.execute("DELETE FROM show")
        cur.execute("DELETE FROM national_final")
        cur.execute("DELETE FROM point")
        cur.execute("DELETE FROM point_system")
        cur.execute("DELETE FROM session")
        cur.execute("DELETE FROM api_token WHERE user_id > 3")
        cur.execute("DELETE FROM account WHERE id > 3")
        cur.execute(
            """
            UPDATE account
            SET approved = true,
                role = CASE WHEN id = 1 THEN 'admin' ELSE 'user' END
            WHERE id IN (1, 2, 3)
            """
        )
    conn.commit()
    conn.close()
