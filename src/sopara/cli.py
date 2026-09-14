from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sopara.adapters.database.engine import REPLAY_POOL, Database, create_database_engine
from sopara.adapters.database.historical import HistoricalRepository
from sopara.adapters.evidence.stores import SyntheticFilesystemObjectStore
from sopara.application.historical import HistoricalReplayService
from sopara.domain.evidence import EvidenceClass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sopara",
        description="Temporary synthetic-only operator CLI for Sopara application services.",
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--object-root", required=True, type=Path)
    groups = parser.add_subparsers(dest="group", required=True)

    dataset = groups.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="command", required=True)
    register = dataset_commands.add_parser("register")
    register.add_argument("--fixture", required=True, type=Path)

    experiment = groups.add_parser("experiment")
    experiment_commands = experiment.add_subparsers(dest="command", required=True)
    create = experiment_commands.add_parser("create")
    create.add_argument("--dataset-manifest-hash", required=True)

    replay = groups.add_parser("replay")
    replay_commands = replay.add_subparsers(dest="command", required=True)
    start = replay_commands.add_parser("start")
    start.add_argument("--experiment-hash", required=True)
    start.add_argument("--stop-after", type=int)
    resume = replay_commands.add_parser("resume")
    resume.add_argument("--run-id", required=True, type=UUID)
    status = replay_commands.add_parser("status")
    status.add_argument("--run-id", required=True, type=UUID)
    reconcile = replay_commands.add_parser("reconcile")
    reconcile.add_argument("--run-id", required=True, type=UUID)
    report = replay_commands.add_parser("report")
    report.add_argument("--run-id", required=True, type=UUID)
    report.add_argument("--output", required=True, type=Path)
    report.add_argument("--as-observed", action="store_true")
    compare = replay_commands.add_parser("compare")
    compare.add_argument("--run-id", required=True, type=UUID)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    engine = create_database_engine(args.database_url, REPLAY_POOL)
    database = Database(engine)
    store = SyntheticFilesystemObjectStore(args.object_root, EvidenceClass.SYNTHETIC_NES_PROXY)
    service = HistoricalReplayService(HistoricalRepository(database), store)
    try:
        if args.group == "dataset" and args.command == "register":
            return await service.register_fixture(args.fixture)
        if args.group == "experiment" and args.command == "create":
            return await service.create_experiment(args.dataset_manifest_hash)
        if args.group == "replay" and args.command == "start":
            return await service.start(args.experiment_hash, stop_after=args.stop_after)
        if args.group == "replay" and args.command == "resume":
            return await service.resume(args.run_id)
        if args.group == "replay" and args.command == "status":
            return await service.status(args.run_id)
        if args.group == "replay" and args.command == "reconcile":
            return await service.reconcile(args.run_id)
        if args.group == "replay" and args.command == "report":
            return await service.report(
                args.run_id, output=args.output, as_observed=args.as_observed
            )
        if args.group == "replay" and args.command == "compare":
            return await service.compare_no_trade(args.run_id)
        raise RuntimeError("unhandled command")
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as error:  # CLI boundary renders domain/application failures.
        print(
            json.dumps(
                {"error": type(error).__name__, "message": str(error)},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
