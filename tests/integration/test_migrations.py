from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from psycopg import sql


def _database_url(database: str) -> str:
    base = os.environ["SOPARA_TEST_ADMIN_URL"]
    return f"{base.rsplit('/', 1)[0]}/{database}"


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    return config


def test_blank_database_has_one_head_and_named_constraints() -> None:
    config = _config(os.environ["SOPARA_DATABASE_URL"])
    assert command.current(config, check_heads=True) is None
    with psycopg.connect(os.environ["SOPARA_TEST_ADMIN_URL"]) as connection:
        heads = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        unnamed = connection.execute(
            """
            SELECT conrelid::regclass::text, contype
            FROM pg_constraint
            WHERE connamespace = 'sopara'::regnamespace AND conname IS NULL
            """
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'sopara'"
            ).fetchall()
        }
        roles = connection.execute(
            """
            SELECT rolname, rolcanlogin
            FROM pg_roles
            WHERE rolname = ANY(%s)
            ORDER BY rolname
            """,
            (
                [
                    "schema_owner",
                    "web_command",
                    "live_engine",
                    "replay_engine",
                    "reconciler",
                    "model_importer",
                    "readonly_owner",
                ],
            ),
        ).fetchall()
    assert heads == [("0003_historical_replay",)]
    assert unnamed == []
    assert {
        "trading_session",
        "session_window",
        "window_lease",
        "command_inbox",
        "domain_event",
        "audit_event",
        "outbox_event",
        "aggregate_projection",
        "dataset_manifest",
        "market_object",
        "replay_run",
        "replay_checkpoint",
    } <= tables
    assert len(roles) == 7
    assert all(can_login is False for _, can_login in roles)
    engine = sa.create_engine(os.environ["SOPARA_DATABASE_URL"])
    try:
        inspector = sa.inspect(engine)
        assert set(inspector.get_table_names(schema="sopara")) == tables
    finally:
        engine.dispose()


def test_previous_schema_fixture_upgrades_forward_to_same_head() -> None:
    database = f"sopara_previous_{uuid4().hex}"
    admin_url = os.environ["SOPARA_TEST_ADMIN_URL"]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    url = _database_url(database)
    try:
        config = _config(url.replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "0001_pre_wp2")
        with psycopg.connect(url) as connection:
            connection.execute(
                "UPDATE sopara.schema_metadata SET value = 'fixture-preserved' "
                "WHERE key = 'schema_fixture'"
            )
            connection.commit()
        command.upgrade(config, "head")
        with psycopg.connect(url) as connection:
            head = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            marker = connection.execute(
                "SELECT value FROM sopara.schema_metadata WHERE key = 'schema_fixture'"
            ).fetchone()
        assert head == ("0003_historical_replay",)
        assert marker == ("fixture-preserved",)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
