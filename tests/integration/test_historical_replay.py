from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from sopara.adapters.database.schema import decision, replay_checkpoint, replay_run, sim_fill

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "replay" / "golden-v1" / "manifest.json"


def command(object_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and local test arguments
        [
            sys.executable,
            "-m",
            "sopara.cli",
            "--database-url",
            os.environ["SOPARA_DATABASE_URL"],
            "--object-root",
            str(object_root),
            *args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed


def output(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(completed.stdout))


@pytest.mark.asyncio
async def test_cli_historical_workflow_resumes_without_duplicates_and_reconciles(
    database_engine: AsyncEngine, tmp_path: Path
) -> None:
    object_root = tmp_path / "objects"
    registered = output(command(object_root, "dataset", "register", "--fixture", str(FIXTURE)))
    experiment = output(
        command(
            object_root,
            "experiment",
            "create",
            "--dataset-manifest-hash",
            cast("str", registered["manifest_hash"]),
        )
    )
    interrupted = output(
        command(
            object_root,
            "replay",
            "start",
            "--experiment-hash",
            cast("str", experiment["experiment_hash"]),
            "--stop-after",
            "3",
        )
    )
    run_id = UUID(cast("str", interrupted["run_id"]))
    assert interrupted["state"] == "INTERRUPTED"
    assert interrupted["last_input_index"] == 2

    status = output(command(object_root, "replay", "status", "--run-id", str(run_id)))
    assert status["checkpoint_hash"] == interrupted["checkpoint_hash"]
    interrupted_check = output(command(object_root, "replay", "reconcile", "--run-id", str(run_id)))
    assert interrupted_check["state"] == "INTERRUPTED"
    checkpoint_reconciliation = interrupted_check["reconciliation"]
    assert isinstance(checkpoint_reconciliation, dict)
    assert checkpoint_reconciliation["scope"] == "INTERRUPTED_CHECKPOINT"
    assert checkpoint_reconciliation["accepted"] is True
    with pytest.raises(DBAPIError, match="append-only"):
        async with database_engine.begin() as connection:
            await connection.execute(
                sa.update(replay_checkpoint)
                .where(replay_checkpoint.c.run_id == run_id)
                .values(engine_state_hash="f" * 64)
            )
    resumed = output(command(object_root, "replay", "resume", "--run-id", str(run_id)))
    assert resumed["state"] == "COMPLETED"
    assert resumed["last_input_index"] == 5

    async with database_engine.connect() as connection:
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(replay_checkpoint)
                .where(replay_checkpoint.c.run_id == run_id)
            )
            == 6
        )
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(decision)
                .where(decision.c.aggregate_id == run_id)
            )
            == 3
        )
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(sim_fill)
                .where(sim_fill.c.aggregate_id == run_id)
            )
            == 2
        )

    reconciled = output(command(object_root, "replay", "reconcile", "--run-id", str(run_id)))
    assert reconciled["state"] == "RECONCILED"
    reconciliation = reconciled["reconciliation"]
    assert isinstance(reconciliation, dict)
    assert reconciliation["accepted"] is True
    assert reconciliation["mismatches"] == []

    report_path = tmp_path / "report.md"
    report = output(
        command(
            object_root,
            "replay",
            "report",
            "--run-id",
            str(run_id),
            "--output",
            str(report_path),
        )
    )
    assert report_path.is_file()
    assert "| latency | 0.025 |" in report_path.read_text(encoding="utf-8")
    assert report["evidence_class"] == "SYNTHETIC_NES_PROXY"

    comparison = output(command(object_root, "replay", "compare", "--run-id", str(run_id)))
    assert comparison["delta"] == "0.490"
    assert comparison["canonical_beats_no_trade"] is True

    forbidden = command(
        object_root,
        "replay",
        "report",
        "--run-id",
        str(run_id),
        "--output",
        str(tmp_path / "forbidden.md"),
        "--as-observed",
        check=False,
    )
    assert forbidden.returncode == 2
    assert "cannot enter an observed-NES report" in forbidden.stderr

    async with database_engine.connect() as connection:
        state = await connection.scalar(
            sa.select(replay_run.c.state).where(replay_run.c.run_id == run_id)
        )
    assert state == "RECONCILED"


def test_fixture_rejection_is_quarantined(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version":"broken"}', encoding="utf-8")
    object_root = tmp_path / "objects"
    rejected = command(object_root, "dataset", "register", "--fixture", str(invalid), check=False)
    assert rejected.returncode == 2
    quarantined = list((object_root / "quarantine" / "replay-fixtures").glob("*.json"))
    assert len(quarantined) == 1


def test_golden_replay_matches_across_two_clean_processes(tmp_path: Path) -> None:
    outputs: list[str] = []
    for index in range(2):
        completed = subprocess.run(  # noqa: S603 - fixed local proof script
            [
                sys.executable,
                str(ROOT / "scripts" / "prove_golden_replay.py"),
                "--fixture",
                str(FIXTURE),
                "--object-root",
                str(tmp_path / f"clean-{index}"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
