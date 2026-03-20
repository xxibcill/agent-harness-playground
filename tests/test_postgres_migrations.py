from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg
import pytest
from agent_harness_contracts import CreateRunRequest
from agent_harness_core import PostgresRunStore, RunStoreConfig

DATABASE_URL = os.getenv("AGENT_HARNESS_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="AGENT_HARNESS_TEST_DATABASE_URL is required for Postgres migration tests.",
)


def test_postgres_migrations_are_idempotent_and_support_run_policies() -> None:
    database_name = urlparse(DATABASE_URL).path.lstrip("/")
    assert "test" in database_name

    reset_public_schema(DATABASE_URL)
    store = PostgresRunStore(
        RunStoreConfig(
            database_url=DATABASE_URL,
            application_name="agent-harness-migration-test",
        )
    )

    store.apply_migrations()
    store.apply_migrations()

    run = store.create_run(
        CreateRunRequest(
            input="postgres migration",
            max_attempts=4,
            timeout_seconds=90,
        )
    )
    fetched = store.get_run(run.run_id)

    assert fetched is not None
    assert fetched.max_attempts == 4
    assert fetched.timeout_seconds == 90

    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'runtime_runs'
              and column_name in ('max_attempts', 'timeout_seconds')
            order by column_name
            """
        ).fetchall()

    assert [row[0] for row in rows] == ["max_attempts", "timeout_seconds"]


def reset_public_schema(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("drop schema if exists public cascade")
        connection.execute("create schema public")
