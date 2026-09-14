from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from sopara.adapters.replay.artifacts import (
    quarantine_fixture,
    seal_replay_dataset_manifest,
    seal_replay_events,
    seal_replay_result,
)
from sopara.adapters.replay.serialization import decode_events, parse_fixture, state_from_payload
from sopara.application.identities import stable_uuid
from sopara.application.ports import (
    CheckpointRow,
    DatasetRow,
    HistoricalRepositoryPort,
    ObjectStore,
    ReplayArtifact,
    RunRow,
)
from sopara.domain.canonical import canonical_sha256, canonical_value
from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass
from sopara.domain.instruments import ContractId, Product
from sopara.domain.replay import (
    ReplayConfiguration,
    ReplayDataset,
    ReplayEvent,
    ReplayState,
    advance_replay,
    finish_replay,
)
from sopara.domain.versions import RuntimeManifest, VersionRef


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    raw = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label} keys must be strings")
    return cast("Mapping[str, object]", raw)


def _version(component: str, version: str) -> VersionRef:
    return VersionRef(
        component, version, canonical_sha256({"component": component, "version": version})
    )


def _version_from_payload(value: object) -> VersionRef:
    item = _mapping(value, "version")
    return VersionRef(
        component=cast("str", item["component"]),
        version=cast("str", item["version"]),
        sha256=cast("str", item["sha256"]),
    )


def default_configuration() -> ReplayConfiguration:
    return ReplayConfiguration(
        threshold_es_ticks=2,
        maximum_position=1,
        holding_ns=60_000_000_000,
        latency_ns=1_000_000,
        queue_ahead=0,
        slippage_ticks=0,
        passive_requires_trade_through=True,
        spread_cost_microdollars=125_000,
        latency_cost_microdollars=25_000,
        slippage_cost_microdollars=0,
        exchange_fee_microdollars=20_000,
        broker_fee_microdollars=10_000,
        data_allocation_microdollars=50_000,
        inference_allocation_microdollars=0,
        simulator_version=_version("simulator", "nes-top-of-book-v1"),
        cost_model_version=_version("cost-model", "decomposed-v1"),
    )


def _configuration_payload(value: ReplayConfiguration) -> dict[str, object]:
    payload = cast("dict[str, object]", canonical_value(value))
    return payload


def _configuration_from_payload(value: object) -> ReplayConfiguration:
    item = _mapping(value, "replay configuration")
    return ReplayConfiguration(
        threshold_es_ticks=cast("int", item["threshold_es_ticks"]),
        maximum_position=cast("int", item["maximum_position"]),
        holding_ns=cast("int", item["holding_ns"]),
        latency_ns=cast("int", item["latency_ns"]),
        queue_ahead=cast("int", item["queue_ahead"]),
        slippage_ticks=cast("int", item["slippage_ticks"]),
        passive_requires_trade_through=cast("bool", item["passive_requires_trade_through"]),
        spread_cost_microdollars=cast("int", item["spread_cost_microdollars"]),
        latency_cost_microdollars=cast("int", item["latency_cost_microdollars"]),
        slippage_cost_microdollars=cast("int", item["slippage_cost_microdollars"]),
        exchange_fee_microdollars=cast("int", item["exchange_fee_microdollars"]),
        broker_fee_microdollars=cast("int", item["broker_fee_microdollars"]),
        data_allocation_microdollars=cast("int", item["data_allocation_microdollars"]),
        inference_allocation_microdollars=cast("int", item["inference_allocation_microdollars"]),
        simulator_version=_version_from_payload(item["simulator_version"]),
        cost_model_version=_version_from_payload(item["cost_model_version"]),
    )


def _contract_from_canonical(value: str, product: Product) -> ContractId:
    prefix = f"{product.value}-"
    if not value.startswith(prefix):
        raise ValueError("normalized event contract has the wrong product")
    year, month = value.removeprefix(prefix).split("-")
    contract = ContractId(product, date(int(year), int(month), 1))
    if contract.canonical != value:
        raise ValueError("normalized event contract is not canonical")
    return contract


def _checkpoint_payload(
    *,
    run_id: UUID,
    input_index: int,
    input_key: str,
    source_watermarks: Mapping[str, object],
    engine_state: Mapping[str, object],
    engine_state_hash: str,
    preceding_output_hash: str,
    runtime_manifest_hash: str,
) -> dict[str, object]:
    return {
        "run_id": str(run_id),
        "input_index": input_index,
        "input_key": input_key,
        "source_watermarks": dict(source_watermarks),
        "engine_state": dict(engine_state),
        "engine_state_hash": engine_state_hash,
        "preceding_output_hash": preceding_output_hash,
        "runtime_manifest_hash": runtime_manifest_hash,
        "task_index": 0,
    }


def _records(
    result: Mapping[str, object], result_object: Mapping[str, object]
) -> dict[str, Sequence[Mapping[str, object]]]:
    decisions = cast("Sequence[Mapping[str, object]]", result["decisions"])
    return {
        "feature_snapshot": tuple(
            {
                "decision_id": item["decision_id"],
                "event_index": item["event_index"],
                "feature": item["feature"],
            }
            for item in decisions
        ),
        "decision": decisions,
        "policy_check": tuple(
            {
                "decision_id": item["decision_id"],
                "outcome": item["outcome"],
                "reason_codes": item["reason_codes"],
            }
            for item in decisions
        ),
        "sim_order_event": cast("Sequence[Mapping[str, object]]", result["orders"]),
        "sim_fill": cast("Sequence[Mapping[str, object]]", result["fills"]),
        "position_projection": cast("Sequence[Mapping[str, object]]", result["positions"]),
        "cost_projection": (cast("Mapping[str, object]", result["pnl"]),),
        "export_manifest": (result_object,),
    }


class HistoricalReplayService:
    def __init__(self, repository: HistoricalRepositoryPort, store: ObjectStore) -> None:
        self._repository = repository
        self._store = store

    async def register_fixture(self, path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        try:
            dataset = parse_fixture(raw)
        except (DomainError, KeyError, TypeError, ValueError) as error:
            quarantined = quarantine_fixture(self._store, raw=raw, reason=str(error))
            raise DomainError(
                f"fixture rejected and quarantined as {quarantined.key}: {error}"
            ) from error
        raw_fixture_hash = hashlib.sha256(raw).hexdigest()
        fixture_hash = dataset.input_hash
        dataset_id = stable_uuid("replay-dataset", fixture_hash)
        artifacts: list[ReplayArtifact] = []
        for instrument in (Product.ES, Product.NES):
            events = tuple(item for item in dataset.events if item.instrument is instrument)
            artifacts.append(
                seal_replay_events(
                    self._store,
                    dataset_id=str(dataset_id),
                    trade_date=dataset.trade_date.isoformat(),
                    instrument=instrument.value,
                    evidence_class=dataset.evidence_class,
                    events=events,
                )
            )
        manifest = seal_replay_dataset_manifest(
            self._store,
            dataset_id=str(dataset_id),
            fixture_hash=fixture_hash,
            trade_date=dataset.trade_date.isoformat(),
            evidence_class=dataset.evidence_class,
            artifacts=tuple(artifacts),
        )
        row = await self._repository.register_dataset(
            dataset_id=dataset_id,
            manifest_id=stable_uuid("replay-dataset-manifest", manifest.sha256),
            manifest=manifest,
            evidence_class=dataset.evidence_class,
            artifacts=tuple(artifacts),
        )
        return {
            "dataset_id": str(row.dataset_id),
            "fixture_hash": fixture_hash,
            "raw_fixture_hash": raw_fixture_hash,
            "manifest_hash": row.manifest_hash,
            "manifest_key": row.manifest_key,
            "generation": row.generation,
            "evidence_class": row.evidence_class.value,
            "accepted_input_hashes": [item.stored.sha256 for item in artifacts],
        }

    async def create_experiment(self, dataset_manifest_hash: str) -> dict[str, object]:
        row, dataset, input_hashes = await self._load_dataset(dataset_manifest_hash)
        configuration = default_configuration()
        runtime = RuntimeManifest(
            manifest_version="sopara.runtime-manifest.v1",
            dataset=VersionRef("dataset", row.manifest_hash[:12], row.manifest_hash),
            calendar=_version("calendar", "cme-2026-v1"),
            feature=_version("feature", "es-delta-1-v1"),
            strategy=_version("strategy", "threshold-abstain-v1"),
            simulator=configuration.simulator_version,
            cost_model=configuration.cost_model_version,
            risk_policy=_version("risk-policy", "one-contract-v1"),
            input_sha256=input_hashes,
            seed=0,
        )
        payload: dict[str, object] = {
            "schema_version": "sopara.experiment-manifest.v1",
            "dataset_manifest_hash": row.manifest_hash,
            "dataset_hash": dataset.input_hash,
            "trade_date": dataset.trade_date.isoformat(),
            "evidence_class": dataset.evidence_class.value,
            "runtime_manifest": cast("dict[str, object]", canonical_value(runtime)),
            "runtime_manifest_hash": runtime.fingerprint,
            "replay_configuration": _configuration_payload(configuration),
            "configuration_hash": configuration.fingerprint,
            "counterfactuals": ["NO_TRADE"],
            "hypothesis": "ES movement can produce deterministic NES simulation evidence",
            "rejection_criterion": "net outcome does not exceed the NO_TRADE baseline",
        }
        experiment_hash = canonical_sha256(payload)
        experiment_id = stable_uuid("experiment", experiment_hash)
        saved = await self._repository.create_experiment(
            experiment_id=experiment_id,
            experiment_hash=experiment_hash,
            evidence_class=dataset.evidence_class,
            payload=payload,
        )
        return {
            "experiment_id": str(saved.experiment_id),
            "experiment_hash": saved.experiment_hash,
            "runtime_manifest_hash": runtime.fingerprint,
            "configuration_hash": configuration.fingerprint,
            "evidence_class": saved.evidence_class.value,
        }

    async def start(
        self, experiment_hash: str, *, stop_after: int | None = None
    ) -> dict[str, object]:
        experiment_row = await self._repository.experiment(experiment_hash)
        payload = experiment_row.payload
        dataset_hash = cast("str", payload["dataset_manifest_hash"])
        runtime_hash = cast("str", payload["runtime_manifest_hash"])
        run_id = stable_uuid(
            "replay-run",
            {
                "dataset_manifest_hash": dataset_hash,
                "experiment_hash": experiment_hash,
                "runtime_manifest_hash": runtime_hash,
            },
        )
        run = await self._repository.create_run(
            run_id=run_id,
            dataset_manifest_hash=dataset_hash,
            experiment_hash=experiment_hash,
            runtime_manifest_hash=runtime_hash,
            evidence_class=experiment_row.evidence_class,
        )
        return await self._execute(
            run, payload, ReplayState.initial(), stop_after, allow_resume=False
        )

    async def resume(self, run_id: UUID) -> dict[str, object]:
        run = await self._repository.run(run_id)
        if run.state != "INTERRUPTED":
            raise ValueError(f"only an interrupted run may resume, found {run.state}")
        experiment = await self._repository.experiment(run.experiment_hash)
        checkpoint = await self._repository.latest_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError("interrupted run has no approved checkpoint")
        _, dataset, _ = await self._load_dataset(run.dataset_manifest_hash)
        await self._verify_checkpoint_chain(run, dataset)
        state = state_from_payload(checkpoint.engine_state)
        return await self._execute(run, experiment.payload, state, None, allow_resume=True)

    async def _execute(
        self,
        run: RunRow,
        experiment: Mapping[str, object],
        state: ReplayState,
        stop_after: int | None,
        *,
        allow_resume: bool,
    ) -> dict[str, object]:
        if stop_after is not None and stop_after <= 0:
            raise ValueError("stop-after must be positive")
        _, dataset, input_hashes = await self._load_dataset(run.dataset_manifest_hash)
        configuration = _configuration_from_payload(experiment["replay_configuration"])
        await self._repository.begin(run.run_id, allow_resume=allow_resume)
        processed = 0
        preceding = run.checkpoint_hash or run.dataset_manifest_hash
        while state.next_input_index < len(dataset.events):
            event = dataset.events[state.next_input_index]
            state = advance_replay(dataset, configuration, state)
            state_payload = cast("Mapping[str, object]", canonical_value(state))
            state_hash = state.fingerprint
            input_key = canonical_sha256(event)
            watermarks: dict[str, object] = {}
            for consumed in dataset.events[: event.global_index + 1]:
                watermarks[consumed.instrument.value] = {
                    "source_sequence": consumed.source_sequence,
                    "source_time_ns": consumed.source_time_ns,
                }
            checkpoint_payload = _checkpoint_payload(
                run_id=run.run_id,
                input_index=event.global_index,
                input_key=input_key,
                source_watermarks=watermarks,
                engine_state=state_payload,
                engine_state_hash=state_hash,
                preceding_output_hash=preceding,
                runtime_manifest_hash=run.runtime_manifest_hash,
            )
            checkpoint_hash = canonical_sha256(checkpoint_payload)
            await self._repository.save_checkpoint(
                run_id=run.run_id,
                input_index=event.global_index,
                input_key=input_key,
                source_watermarks=watermarks,
                engine_state=state_payload,
                engine_state_hash=state_hash,
                preceding_output_hash=preceding,
                runtime_manifest_hash=run.runtime_manifest_hash,
                checkpoint_hash=checkpoint_hash,
            )
            preceding = checkpoint_hash
            processed += 1
            if (
                stop_after is not None
                and processed >= stop_after
                and state.next_input_index < len(dataset.events)
            ):
                interrupted = await self._repository.interrupt(run.run_id)
                return self._run_payload(interrupted)
        result = finish_replay(
            dataset,
            configuration,
            run.runtime_manifest_hash,
            state,
            input_hashes,
        )
        result_object = seal_replay_result(self._store, run_id=str(run.run_id), result=result)
        result_payload = cast("Mapping[str, object]", canonical_value(result))
        object_record = {
            "object_key": result_object.key,
            "generation": result_object.generation,
            "sha256": result_object.sha256,
            "schema_id": result.schema_version,
        }
        completed = await self._repository.complete(
            run_id=run.run_id,
            evidence_class=result.evidence_class,
            result_object=result_object,
            result_hash=result.fingerprint,
            records=_records(result_payload, object_record),
        )
        return self._run_payload(completed)

    def _verify_checkpoint(
        self,
        run: RunRow,
        dataset: ReplayDataset,
        checkpoint: CheckpointRow,
        *,
        expected_index: int,
        expected_preceding: str,
    ) -> None:
        if checkpoint.runtime_manifest_hash != run.runtime_manifest_hash:
            raise ValueError("checkpoint runtime manifest does not match the run")
        if checkpoint.input_index != expected_index:
            raise ValueError("checkpoint index is not contiguous")
        if checkpoint.preceding_output_hash != expected_preceding:
            raise ValueError("checkpoint preceding-output chain does not match")
        if checkpoint.input_key != canonical_sha256(dataset.events[checkpoint.input_index]):
            raise ValueError("checkpoint input key does not match the frozen dataset")
        state = state_from_payload(checkpoint.engine_state)
        if state.fingerprint != checkpoint.engine_state_hash:
            raise ValueError("checkpoint engine state hash does not match")
        payload = _checkpoint_payload(
            run_id=run.run_id,
            input_index=checkpoint.input_index,
            input_key=checkpoint.input_key,
            source_watermarks=checkpoint.source_watermarks,
            engine_state=checkpoint.engine_state,
            engine_state_hash=checkpoint.engine_state_hash,
            preceding_output_hash=checkpoint.preceding_output_hash,
            runtime_manifest_hash=checkpoint.runtime_manifest_hash,
        )
        if canonical_sha256(payload) != checkpoint.checkpoint_hash:
            raise ValueError("checkpoint envelope hash does not match")

    async def _verify_checkpoint_chain(self, run: RunRow, dataset: ReplayDataset) -> None:
        checkpoints = await self._repository.checkpoints(run.run_id)
        if len(checkpoints) != run.last_input_index + 1:
            raise ValueError("run checkpoint count does not match its durable input index")
        preceding = run.dataset_manifest_hash
        for index, checkpoint in enumerate(checkpoints):
            self._verify_checkpoint(
                run,
                dataset,
                checkpoint,
                expected_index=index,
                expected_preceding=preceding,
            )
            preceding = checkpoint.checkpoint_hash
        if run.checkpoint_hash != preceding:
            raise ValueError("run projection does not reference the terminal checkpoint")

    async def status(self, run_id: UUID) -> dict[str, object]:
        return self._run_payload(await self._repository.run(run_id))

    async def reconcile(self, run_id: UUID) -> dict[str, object]:
        run = await self._repository.run(run_id)
        if run.state == "INTERRUPTED":
            mismatches: list[str] = []
            try:
                _, dataset, _ = await self._load_dataset(run.dataset_manifest_hash)
                await self._verify_checkpoint_chain(run, dataset)
            except (DomainError, KeyError, TypeError, ValueError) as error:
                mismatches.append(str(error))
            checkpoint_payload = {
                "schema_version": "sopara.replay-reconciliation.v1",
                "scope": "INTERRUPTED_CHECKPOINT",
                "run_id": str(run_id),
                "accepted": not mismatches,
                "mismatches": mismatches,
                "checkpoint_hash": run.checkpoint_hash,
                "evidence_class": run.evidence_class.value,
            }
            updated = await self._repository.record_reconciliation(
                run_id=run_id,
                evidence_class=run.evidence_class,
                payload=checkpoint_payload,
                accepted=not mismatches,
                finalize=False,
            )
            return {**self._run_payload(updated), "reconciliation": checkpoint_payload}
        if run.state != "COMPLETED":
            raise ValueError(
                f"only a completed or interrupted replay may reconcile, found {run.state}"
            )
        mismatches: list[str] = []
        try:
            _, dataset, input_hashes = await self._load_dataset(run.dataset_manifest_hash)
            await self._verify_checkpoint_chain(run, dataset)
        except (DomainError, KeyError, TypeError, ValueError) as error:
            mismatches.append(str(error))
            input_hashes = ()
        if (
            run.result_object_key is None
            or run.result_hash is None
            or run.result_generation is None
        ):
            mismatches.append("run result object reference is incomplete")
            payload: Mapping[str, object] = {}
        else:
            actual = self._store.stat(run.result_object_key)
            raw = self._store.read(run.result_object_key)
            if actual.generation != run.result_generation:
                mismatches.append("result object generation differs from SQL")
            if actual.sha256 != hashlib.sha256(raw).hexdigest():
                mismatches.append("result object bytes differ from object metadata")
            if actual.sha256 != run.result_hash:
                mismatches.append("result object hash differs from SQL")
            payload = _mapping(json.loads(raw), "replay result")
            if payload.get("evidence_class") != run.evidence_class.value:
                mismatches.append("result evidence class differs from run")
            accepted_hashes = cast("Sequence[str]", payload.get("accepted_input_hashes", ()))
            if tuple(accepted_hashes) != input_hashes:
                mismatches.append("accepted input hashes differ from the frozen dataset")
        if payload:
            object_record = {
                "object_key": run.result_object_key,
                "generation": run.result_generation,
                "sha256": run.result_hash,
                "schema_id": payload["schema_version"],
            }
            expected_records = _records(payload, object_record)
            actual_records = await self._repository.records(run_id)
            for name, expected in expected_records.items():
                expected_hashes = sorted(canonical_sha256(item) for item in expected)
                actual_hashes = sorted(canonical_sha256(item) for item in actual_records[name])
                if expected_hashes != actual_hashes:
                    mismatches.append(f"{name} SQL records differ from the result object")
            pnl = _mapping(payload["pnl"], "P&L")
            costs = sum(
                Decimal(cast("str", pnl[name]))
                for name in (
                    "spread",
                    "latency",
                    "slippage",
                    "exchange_fees",
                    "broker_fees",
                    "data_allocation",
                    "inference_allocation",
                )
            )
            if Decimal(cast("str", pnl["net"])) != Decimal(cast("str", pnl["gross"])) - costs:
                mismatches.append("P&L components do not independently fold to net")
            if Decimal(cast("str", pnl["allocated_research_cost"])) != Decimal(
                cast("str", pnl["data_allocation"])
            ) + Decimal(cast("str", pnl["inference_allocation"])):
                mismatches.append("research allocation does not fold from its components")
        reconciliation_payload = {
            "schema_version": "sopara.replay-reconciliation.v1",
            "scope": "COMPLETED_RESULT",
            "run_id": str(run_id),
            "accepted": not mismatches,
            "mismatches": mismatches,
            "result_hash": run.result_hash,
            "evidence_class": run.evidence_class.value,
        }
        updated = await self._repository.record_reconciliation(
            run_id=run_id,
            evidence_class=run.evidence_class,
            payload=reconciliation_payload,
            accepted=not mismatches,
        )
        return {**self._run_payload(updated), "reconciliation": reconciliation_payload}

    async def report(
        self, run_id: UUID, *, output: Path, as_observed: bool = False
    ) -> dict[str, object]:
        run = await self._repository.run(run_id)
        if run.state != "RECONCILED":
            raise ValueError("reports require a reconciled replay")
        if as_observed and run.evidence_class is not EvidenceClass.OBSERVED_NES:
            raise ValueError("synthetic proxy evidence cannot enter an observed-NES report")
        if run.result_object_key is None:
            raise ValueError("reconciled run has no result object")
        result = _mapping(json.loads(self._store.read(run.result_object_key)), "replay result")
        pnl = _mapping(result["pnl"], "P&L")
        lines = [
            "# Sopara historical replay",
            "",
            f"- Run: `{run_id}`",
            f"- Evidence class: `{run.evidence_class.value}`",
            f"- Result hash: `{run.result_hash}`",
            f"- Decisions: {len(cast('Sequence[object]', result['decisions']))}",
            f"- Fills: {len(cast('Sequence[object]', result['fills']))}",
            "",
            "## P&L decomposition",
            "",
            "| Component | USD |",
            "| --- | ---: |",
        ]
        lines.extend(
            f"| {name} | {pnl[name]} |"
            for name in (
                "gross",
                "spread",
                "latency",
                "slippage",
                "exchange_fees",
                "broker_fees",
                "allocated_research_cost",
                "net",
            )
        )
        lines.extend(
            [
                "",
                "## Counterfactual",
                "",
                "The predeclared `NO_TRADE` baseline has gross and net outcome of 0.",
                "",
            ]
        )
        rendered = "\n".join(lines)
        output.write_text(rendered, encoding="utf-8")
        return {
            "run_id": str(run_id),
            "output": str(output),
            "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "evidence_class": run.evidence_class.value,
        }

    async def compare_no_trade(self, run_id: UUID) -> dict[str, object]:
        run = await self._repository.run(run_id)
        if run.state != "RECONCILED" or run.result_object_key is None:
            raise ValueError("comparison requires a reconciled replay")
        result = _mapping(json.loads(self._store.read(run.result_object_key)), "replay result")
        pnl = _mapping(result["pnl"], "P&L")
        canonical_net = Decimal(cast("str", pnl["net"]))
        return {
            "run_id": str(run_id),
            "canonical_net": format(canonical_net, "f"),
            "no_trade_net": "0",
            "delta": format(canonical_net, "f"),
            "canonical_beats_no_trade": canonical_net > 0,
            "evidence_class": run.evidence_class.value,
        }

    async def _load_dataset(
        self, manifest_hash: str
    ) -> tuple[DatasetRow, ReplayDataset, tuple[str, ...]]:
        row = await self._repository.dataset(manifest_hash)
        stored = self._store.stat(row.manifest_key)
        if stored.generation != row.generation or stored.sha256 != row.manifest_hash:
            raise ValueError("dataset manifest object differs from SQL")
        manifest = _mapping(json.loads(self._store.read(row.manifest_key)), "dataset manifest")
        if manifest.get("schema_version") != "sopara.replay-dataset-manifest.v1":
            raise ValueError("unsupported dataset manifest schema")
        if manifest.get("evidence_class") != row.evidence_class.value:
            raise ValueError("dataset manifest evidence class differs from SQL")
        object_entries = cast("Sequence[Mapping[str, object]]", manifest["objects"])
        events: list[ReplayEvent] = []
        input_hashes: list[str] = []
        for entry in object_entries:
            key = cast("str", entry["key"])
            actual = self._store.stat(key)
            if actual.generation != entry["generation"] or actual.sha256 != entry["sha256"]:
                raise ValueError("dataset member differs from frozen manifest")
            events.extend(decode_events(self._store.read(key)))
            input_hashes.append(actual.sha256)
        events.sort(key=lambda item: item.global_index)
        es_ids = {item.contract_id for item in events if item.instrument is Product.ES}
        nes_ids = {item.contract_id for item in events if item.instrument is Product.NES}
        if len(es_ids) != 1 or len(nes_ids) != 1:
            raise ValueError("dataset must contain exactly one explicit ES and NES contract")
        dataset = ReplayDataset(
            schema_version="sopara.synthetic-replay-fixture.v1",
            evidence_class=row.evidence_class,
            trade_date=date.fromisoformat(cast("str", manifest["trade_date"])),
            es_contract=_contract_from_canonical(next(iter(es_ids)), Product.ES),
            nes_contract=_contract_from_canonical(next(iter(nes_ids)), Product.NES),
            events=tuple(events),
        )
        if dataset.input_hash != manifest["fixture_hash"]:
            raise ValueError("normalized dataset does not reproduce the accepted fixture hash")
        return row, dataset, tuple(input_hashes)

    @staticmethod
    def _run_payload(run: RunRow) -> dict[str, object]:
        return {
            "run_id": str(run.run_id),
            "state": run.state,
            "last_input_index": run.last_input_index,
            "checkpoint_hash": run.checkpoint_hash,
            "result_object_key": run.result_object_key,
            "result_generation": run.result_generation,
            "result_hash": run.result_hash,
            "evidence_class": run.evidence_class.value,
            "row_version": run.row_version,
        }
