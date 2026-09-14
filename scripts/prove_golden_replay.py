from __future__ import annotations

import argparse
from pathlib import Path

from sopara.adapters.evidence.stores import SyntheticFilesystemObjectStore
from sopara.adapters.replay.artifacts import seal_replay_events, seal_replay_result
from sopara.adapters.replay.serialization import read_fixture
from sopara.application.historical import default_configuration
from sopara.application.identities import stable_uuid
from sopara.domain.canonical import canonical_json
from sopara.domain.evidence import EvidenceClass
from sopara.domain.instruments import Product
from sopara.domain.replay import ReplayState, advance_replay, finish_replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--object-root", required=True, type=Path)
    args = parser.parse_args()
    dataset = read_fixture(args.fixture)
    configuration = default_configuration()
    runtime_hash = "a" * 64
    store = SyntheticFilesystemObjectStore(args.object_root, EvidenceClass.SYNTHETIC_NES_PROXY)
    dataset_id = str(stable_uuid("replay-dataset", dataset.input_hash))
    artifact_rows = []
    input_hashes = []
    for instrument in (Product.ES, Product.NES):
        artifact = seal_replay_events(
            store,
            dataset_id=dataset_id,
            trade_date=dataset.trade_date.isoformat(),
            instrument=instrument.value,
            evidence_class=dataset.evidence_class,
            events=tuple(item for item in dataset.events if item.instrument is instrument),
        )
        input_hashes.append(artifact.stored.sha256)
        artifact_rows.append(
            {
                "instrument": instrument.value,
                "generation": artifact.stored.generation,
                "sha256": artifact.stored.sha256,
            }
        )
    state = ReplayState.initial()
    for _ in dataset.events:
        state = advance_replay(dataset, configuration, state)
    result = finish_replay(dataset, configuration, runtime_hash, state, tuple(input_hashes))
    result_object = seal_replay_result(
        store,
        run_id=str(stable_uuid("golden-run", result.fingerprint)),
        result=result,
    )
    print(
        canonical_json(
            {
                "artifacts": artifact_rows,
                "result": result,
                "result_object": {
                    "generation": result_object.generation,
                    "sha256": result_object.sha256,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
