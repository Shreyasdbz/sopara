# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass
from sopara.domain.instruments import ContractId, Product
from sopara.domain.replay import ReplayDataset, ReplayEvent, ReplayEventType, ReplayState

REPLAY_EVENT_SCHEMA_ID = "sopara.replay-event.parquet.v1"
REPLAY_EVENT_SCHEMA = pa.schema(
    [
        pa.field("global_index", pa.int32(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("channel_id", pa.string(), nullable=False),
        pa.field("source_session_id", pa.string(), nullable=False),
        pa.field("source_sequence", pa.int64(), nullable=False),
        pa.field("message_index", pa.int32(), nullable=False),
        pa.field("contract_id", pa.string(), nullable=False),
        pa.field("source_time_ns", pa.int64(), nullable=False),
        pa.field("receive_time_ns", pa.int64(), nullable=False),
        pa.field("price_ticks", pa.int64()),
        pa.field("quantity", pa.int64()),
        pa.field("bid_ticks", pa.int64()),
        pa.field("ask_ticks", pa.int64()),
        pa.field("bid_size", pa.int64()),
        pa.field("ask_size", pa.int64()),
        pa.field("last_trade_size", pa.int64()),
        pa.field("evidence_class", pa.string(), nullable=False),
    ],
    metadata={b"schema_id": REPLAY_EVENT_SCHEMA_ID.encode("ascii"), b"compression": b"zstd"},
)

FIXTURE_KEYS = {
    "schema_version",
    "evidence_class",
    "trade_date",
    "contracts",
    "events",
}
EVENT_KEYS = {
    "global_index",
    "event_type",
    "source_id",
    "channel_id",
    "source_session_id",
    "source_sequence",
    "message_index",
    "contract_id",
    "source_time_ns",
    "receive_time_ns",
    "evidence_class",
    "price_ticks",
    "quantity",
    "bid_ticks",
    "ask_ticks",
    "bid_size",
    "ask_size",
    "last_trade_size",
}


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DomainError(f"{label} must be an object")
    return cast("dict[str, object]", value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError(f"{label} must be an integer")
    return value


def _optional_integer(value: object, label: str) -> int | None:
    return None if value is None else _integer(value, label)


def _contract(value: object, product: Product) -> ContractId:
    text = _text(value, f"{product.value} contract")
    prefix = f"{product.value}-"
    if not text.startswith(prefix):
        raise DomainError(f"{product.value} contract has the wrong product")
    try:
        year_text, month_text = text.removeprefix(prefix).split("-")
        contract = ContractId(product, date(int(year_text), int(month_text), 1))
    except (ValueError, TypeError) as error:
        raise DomainError(f"invalid {product.value} contract") from error
    if contract.canonical != text:
        raise DomainError(f"{product.value} contract is not canonical")
    return contract


def parse_fixture(raw: bytes) -> ReplayDataset:
    try:
        payload = _object(json.loads(raw), "fixture")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DomainError("fixture is not valid UTF-8 JSON") from error
    if set(payload) != FIXTURE_KEYS:
        raise DomainError("fixture fields do not match the v1 schema")
    evidence = EvidenceClass(_text(payload["evidence_class"], "evidence class"))
    contracts = _object(payload["contracts"], "contracts")
    if set(contracts) != {"ES", "NES"}:
        raise DomainError("fixture requires exact ES and NES contract fields")
    raw_events = payload["events"]
    if not isinstance(raw_events, list):
        raise DomainError("fixture events must be an array")
    events: list[ReplayEvent] = []
    for index, value in enumerate(raw_events):
        item = _object(value, f"event {index}")
        if not set(item).issubset(EVENT_KEYS):
            raise DomainError(f"event {index} contains unknown fields")
        required = EVENT_KEYS - {
            "price_ticks",
            "quantity",
            "bid_ticks",
            "ask_ticks",
            "bid_size",
            "ask_size",
            "last_trade_size",
        }
        if not required.issubset(item):
            raise DomainError(f"event {index} is incomplete")
        if _text(item["evidence_class"], f"event {index} evidence class") != evidence.value:
            raise DomainError("fixture contains mixed evidence classes")
        events.append(
            ReplayEvent(
                global_index=_integer(item["global_index"], "global index"),
                event_type=ReplayEventType(_text(item["event_type"], "event type")),
                source_id=_text(item["source_id"], "source id"),
                channel_id=_text(item["channel_id"], "channel id"),
                source_session_id=_text(item["source_session_id"], "source session id"),
                source_sequence=_integer(item["source_sequence"], "source sequence"),
                message_index=_integer(item["message_index"], "message index"),
                contract_id=_text(item["contract_id"], "contract id"),
                source_time_ns=_integer(item["source_time_ns"], "source time"),
                receive_time_ns=_integer(item["receive_time_ns"], "receive time"),
                price_ticks=_optional_integer(item.get("price_ticks"), "price ticks"),
                quantity=_optional_integer(item.get("quantity"), "quantity"),
                bid_ticks=_optional_integer(item.get("bid_ticks"), "bid ticks"),
                ask_ticks=_optional_integer(item.get("ask_ticks"), "ask ticks"),
                bid_size=_optional_integer(item.get("bid_size"), "bid size"),
                ask_size=_optional_integer(item.get("ask_size"), "ask size"),
                last_trade_size=_optional_integer(item.get("last_trade_size"), "last trade size"),
            )
        )
    try:
        trade_date = date.fromisoformat(_text(payload["trade_date"], "trade date"))
    except ValueError as error:
        raise DomainError("trade date is invalid") from error
    return ReplayDataset(
        schema_version=_text(payload["schema_version"], "schema version"),
        evidence_class=evidence,
        trade_date=trade_date,
        es_contract=_contract(contracts["ES"], Product.ES),
        nes_contract=_contract(contracts["NES"], Product.NES),
        events=tuple(events),
    )


def encode_events(events: Sequence[ReplayEvent], evidence_class: EvidenceClass) -> bytes:
    rows = [
        {
            "global_index": item.global_index,
            "event_type": item.event_type.value,
            "source_id": item.source_id,
            "channel_id": item.channel_id,
            "source_session_id": item.source_session_id,
            "source_sequence": item.source_sequence,
            "message_index": item.message_index,
            "contract_id": item.contract_id,
            "source_time_ns": item.source_time_ns,
            "receive_time_ns": item.receive_time_ns,
            "price_ticks": item.price_ticks,
            "quantity": item.quantity,
            "bid_ticks": item.bid_ticks,
            "ask_ticks": item.ask_ticks,
            "bid_size": item.bid_size,
            "ask_size": item.ask_size,
            "last_trade_size": item.last_trade_size,
            "evidence_class": evidence_class.value,
        }
        for item in events
    ]
    table = pa.Table.from_pylist(rows, schema=REPLAY_EVENT_SCHEMA)
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="zstd",
        version="2.6",
        use_dictionary=True,
        write_statistics=True,
        row_group_size=max(1, len(rows)),
    )
    payload = buffer.getvalue()
    pq.read_metadata(io.BytesIO(payload))
    return payload


def decode_events(payload: bytes) -> tuple[ReplayEvent, ...]:
    table = pq.read_table(io.BytesIO(payload), schema=REPLAY_EVENT_SCHEMA)
    events: list[ReplayEvent] = []
    for row in table.to_pylist():
        item = cast("dict[str, object]", row)
        events.append(
            ReplayEvent(
                global_index=cast("int", item["global_index"]),
                event_type=ReplayEventType(cast("str", item["event_type"])),
                source_id=cast("str", item["source_id"]),
                channel_id=cast("str", item["channel_id"]),
                source_session_id=cast("str", item["source_session_id"]),
                source_sequence=cast("int", item["source_sequence"]),
                message_index=cast("int", item["message_index"]),
                contract_id=cast("str", item["contract_id"]),
                source_time_ns=cast("int", item["source_time_ns"]),
                receive_time_ns=cast("int", item["receive_time_ns"]),
                price_ticks=cast("int | None", item["price_ticks"]),
                quantity=cast("int | None", item["quantity"]),
                bid_ticks=cast("int | None", item["bid_ticks"]),
                ask_ticks=cast("int | None", item["ask_ticks"]),
                bid_size=cast("int | None", item["bid_size"]),
                ask_size=cast("int | None", item["ask_size"]),
                last_trade_size=cast("int | None", item["last_trade_size"]),
            )
        )
    return tuple(events)


def state_from_payload(payload: Mapping[str, object]) -> ReplayState:
    raw_book = cast("Sequence[int] | None", payload["nes_book"])
    book: tuple[int, int, int, int, int] | None = None
    if raw_book is not None:
        if len(raw_book) != 5:
            raise ValueError("checkpoint NES book must have five fields")
        book = (raw_book[0], raw_book[1], raw_book[2], raw_book[3], raw_book[4])
    return ReplayState(
        next_input_index=cast("int", payload["next_input_index"]),
        logical_time_ns=cast("int", payload["logical_time_ns"]),
        previous_es_ticks=cast("int | None", payload["previous_es_ticks"]),
        current_es_ticks=cast("int | None", payload["current_es_ticks"]),
        nes_book=book,
        position=cast("int", payload["position"]),
        entry_ticks=cast("int | None", payload["entry_ticks"]),
        decisions=tuple(cast("Sequence[dict[str, object]]", payload["decisions"])),
        orders=tuple(cast("Sequence[dict[str, object]]", payload["orders"])),
        fills=tuple(cast("Sequence[dict[str, object]]", payload["fills"])),
        positions=tuple(cast("Sequence[dict[str, object]]", payload["positions"])),
    )


def read_fixture(path: Path) -> ReplayDataset:
    return parse_fixture(path.read_bytes())
