# pyright: reportAssignmentType=false
# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

SCHEMA = "sopara"
metadata = sa.MetaData(schema=SCHEMA)
EVIDENCE_CLASS_CHECK = (
    "evidence_class IN ('OBSERVED_NES','SYNTHETIC_NES_PROXY','NO_EXECUTION_EVIDENCE')"
)


def _uuid() -> sa.Uuid:
    return sa.Uuid(as_uuid=True)


schema_metadata = sa.Table(
    "schema_metadata",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

trading_session = sa.Table(
    "trading_session",
    metadata,
    sa.Column("session_id", _uuid(), primary_key=True),
    sa.Column("trade_date", sa.Date, nullable=False),
    sa.Column("mode", sa.Text, nullable=False),
    sa.Column("evidence_class", sa.Text, nullable=False),
    sa.Column("experiment_id", _uuid(), nullable=False),
    sa.Column("runtime_manifest_hash", sa.Text, nullable=False),
    sa.Column("calendar_version", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("reconciliation_id", _uuid()),
    sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
    sa.UniqueConstraint(
        "trade_date",
        "experiment_id",
        "runtime_manifest_hash",
        name="uq_trading_session_identity",
    ),
    sa.CheckConstraint(
        "mode IN ('HISTORICAL_REPLAY', 'LIVE_SIMULATION')",
        name="ck_trading_session_mode",
    ),
    sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_trading_session_evidence_class"),
    sa.CheckConstraint("row_version > 0", name="ck_trading_session_row_version"),
)

session_window = sa.Table(
    "session_window",
    metadata,
    sa.Column("window_id", _uuid(), primary_key=True),
    sa.Column(
        "session_id",
        _uuid(),
        sa.ForeignKey(f"{SCHEMA}.trading_session.session_id", name="fk_window_session"),
        nullable=False,
    ),
    sa.Column("window_code", sa.Text, nullable=False),
    sa.Column("eligible_start_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("eligible_end_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cloud_run_execution", sa.Text),
    sa.Column("lease_epoch", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("terminal_watermarks", postgresql.JSONB),
    sa.Column("manifest_id", _uuid()),
    sa.Column(
        "invalid_reason_codes",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    ),
    sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
    sa.UniqueConstraint("session_id", "window_code", name="uq_session_window_code"),
    sa.CheckConstraint("window_code IN ('AM', 'PM')", name="ck_session_window_code"),
    sa.CheckConstraint("eligible_end_utc > eligible_start_utc", name="ck_session_window_interval"),
    sa.CheckConstraint("lease_epoch >= 0", name="ck_session_window_lease_epoch"),
    sa.CheckConstraint("row_version > 0", name="ck_session_window_row_version"),
)

window_lease = sa.Table(
    "window_lease",
    metadata,
    sa.Column("lease_key", sa.Text, primary_key=True),
    sa.Column("lease_epoch", sa.BigInteger, nullable=False),
    sa.Column("holder_execution", sa.Text, nullable=False),
    sa.Column(
        "acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "last_heartbeat_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint("lease_epoch > 0", name="ck_window_lease_epoch"),
    sa.CheckConstraint("expires_at > acquired_at", name="ck_window_lease_interval"),
)

command_inbox = sa.Table(
    "command_inbox",
    metadata,
    sa.Column("command_id", _uuid(), primary_key=True),
    sa.Column("idempotency_key", sa.Text, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("command_type", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", _uuid(), nullable=False),
    sa.Column("expected_version", sa.BigInteger, nullable=False),
    sa.Column("actor_email_hash", sa.Text, nullable=False),
    sa.Column(
        "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("state", sa.Text, nullable=False, server_default="RECEIVED"),
    sa.Column("result_event_id", _uuid()),
    sa.UniqueConstraint(
        "actor_email_hash",
        "command_type",
        "target_id",
        "idempotency_key",
        name="uq_command_idempotency_scope",
    ),
    sa.CheckConstraint("expected_version >= 0", name="ck_command_expected_version"),
    sa.CheckConstraint(
        "state IN ('RECEIVED','CLAIMED','COMPLETED','REJECTED','EXPIRED')",
        name="ck_command_state",
    ),
)

domain_event = sa.Table(
    "domain_event",
    metadata,
    sa.Column("event_id", _uuid(), primary_key=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("event_schema_version", sa.SmallInteger, nullable=False),
    sa.Column("aggregate_type", sa.Text, nullable=False),
    sa.Column("aggregate_id", _uuid(), nullable=False),
    sa.Column("aggregate_seq", sa.BigInteger, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("occurred_at_ns", sa.BigInteger, nullable=False),
    sa.Column("actor_type", sa.Text, nullable=False),
    sa.Column("actor_id", sa.Text, nullable=False),
    sa.Column("correlation_id", _uuid(), nullable=False),
    sa.Column("causation_id", _uuid(), nullable=False),
    sa.Column(
        "reason_codes",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    ),
    sa.Column("payload", postgresql.JSONB, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("runtime_manifest_hash", sa.Text, nullable=False),
    sa.Column("lease_key", sa.Text),
    sa.Column("lease_epoch", sa.BigInteger),
    sa.UniqueConstraint(
        "aggregate_type", "aggregate_id", "aggregate_seq", name="uq_domain_event_aggregate_seq"
    ),
    sa.CheckConstraint("event_schema_version > 0", name="ck_domain_event_schema_version"),
    sa.CheckConstraint("aggregate_seq > 0", name="ck_domain_event_aggregate_seq"),
    sa.CheckConstraint("occurred_at_ns >= 0", name="ck_domain_event_occurred_at_ns"),
    sa.CheckConstraint(
        "(lease_key IS NULL AND lease_epoch IS NULL) OR "
        "(lease_key IS NOT NULL AND lease_epoch IS NOT NULL AND lease_epoch > 0)",
        name="ck_domain_event_lease_pair",
    ),
)

audit_event = sa.Table(
    "audit_event",
    metadata,
    sa.Column("audit_seq", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("audit_id", _uuid(), nullable=False, unique=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("actor_id", sa.Text, nullable=False),
    sa.Column("correlation_id", _uuid(), nullable=False),
    sa.Column("payload", postgresql.JSONB, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

outbox_event = sa.Table(
    "outbox_event",
    metadata,
    sa.Column("outbox_seq", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column(
        "event_id",
        _uuid(),
        sa.ForeignKey(f"{SCHEMA}.domain_event.event_id", name="fk_outbox_domain_event"),
        nullable=False,
        unique=True,
    ),
    sa.Column("topic", sa.Text, nullable=False),
    sa.Column("payload", postgresql.JSONB, nullable=False),
    sa.Column(
        "committed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

aggregate_projection = sa.Table(
    "aggregate_projection",
    metadata,
    sa.Column("aggregate_type", sa.Text, primary_key=True),
    sa.Column("aggregate_id", _uuid(), primary_key=True),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("row_version", sa.BigInteger, nullable=False),
    sa.Column("last_event_id", _uuid(), nullable=False),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("row_version > 0", name="ck_aggregate_projection_row_version"),
)

data_source = sa.Table(
    "data_source",
    metadata,
    sa.Column("source_id", _uuid(), primary_key=True),
    sa.Column("name", sa.Text, nullable=False, unique=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("metadata", postgresql.JSONB, nullable=False),
)

entitlement_snapshot = sa.Table(
    "entitlement_snapshot",
    metadata,
    sa.Column("snapshot_id", _uuid(), primary_key=True),
    sa.Column(
        "source_id",
        _uuid(),
        sa.ForeignKey(f"{SCHEMA}.data_source.source_id", name="fk_entitlement_source"),
        nullable=False,
    ),
    sa.Column("owner_hash", sa.Text, nullable=False),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("effective_through", sa.Date, nullable=False),
    sa.Column("permitted_uses", postgresql.ARRAY(sa.Text), nullable=False),
    sa.Column("policy_hash", sa.Text, nullable=False),
    sa.CheckConstraint(
        "effective_through >= effective_from", name="ck_entitlement_effective_interval"
    ),
)

dataset_manifest = sa.Table(
    "dataset_manifest",
    metadata,
    sa.Column("manifest_id", _uuid(), primary_key=True),
    sa.Column("dataset_id", _uuid(), nullable=False),
    sa.Column("manifest_key", sa.Text, nullable=False, unique=True),
    sa.Column("manifest_hash", sa.Text, nullable=False, unique=True),
    sa.Column("generation", sa.BigInteger, nullable=False),
    sa.Column("evidence_class", sa.Text, nullable=False),
    sa.Column("object_count", sa.Integer, nullable=False),
    sa.Column(
        "committed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("generation > 0", name="ck_dataset_manifest_generation"),
    sa.CheckConstraint("object_count >= 0", name="ck_dataset_manifest_object_count"),
    sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_dataset_manifest_evidence_class"),
)

market_object = sa.Table(
    "market_object",
    metadata,
    sa.Column("object_id", _uuid(), primary_key=True),
    sa.Column("object_key", sa.Text, nullable=False, unique=True),
    sa.Column("generation", sa.BigInteger, nullable=False),
    sa.Column("metageneration", sa.BigInteger, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("crc32c", sa.Text, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("schema_id", sa.Text, nullable=False),
    sa.Column("evidence_class", sa.Text, nullable=False),
    sa.Column("source_range", postgresql.JSONB, nullable=False),
    sa.Column(
        "committed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("object_key", "generation", name="uq_market_object_generation"),
    sa.CheckConstraint("generation > 0", name="ck_market_object_generation"),
    sa.CheckConstraint("metageneration > 0", name="ck_market_object_metageneration"),
    sa.CheckConstraint("size_bytes >= 0", name="ck_market_object_size"),
    sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_market_object_evidence_class"),
)

versioned_artifact = sa.Table(
    "versioned_artifact",
    metadata,
    sa.Column("artifact_id", _uuid(), primary_key=True),
    sa.Column("artifact_type", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("payload", postgresql.JSONB, nullable=False),
    sa.UniqueConstraint("artifact_type", "version", name="uq_versioned_artifact_type_version"),
)

replay_run = sa.Table(
    "replay_run",
    metadata,
    sa.Column("run_id", _uuid(), primary_key=True),
    sa.Column("dataset_manifest_hash", sa.Text, nullable=False),
    sa.Column("experiment_hash", sa.Text, nullable=False),
    sa.Column("runtime_manifest_hash", sa.Text, nullable=False),
    sa.Column("evidence_class", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("last_input_index", sa.Integer, nullable=False, server_default="-1"),
    sa.Column("checkpoint_hash", sa.Text),
    sa.Column("result_object_key", sa.Text),
    sa.Column("result_generation", sa.BigInteger),
    sa.Column("result_hash", sa.Text),
    sa.Column("error_reason", sa.Text),
    sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint(
        "dataset_manifest_hash",
        "experiment_hash",
        "runtime_manifest_hash",
        name="uq_replay_run_identity",
    ),
    sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_replay_run_evidence_class"),
    sa.CheckConstraint(
        "state IN ('CREATED','RUNNING','INTERRUPTED','COMPLETED','RECONCILED','FAILED')",
        name="ck_replay_run_state",
    ),
    sa.CheckConstraint("last_input_index >= -1", name="ck_replay_run_input_index"),
    sa.CheckConstraint("row_version > 0", name="ck_replay_run_row_version"),
)

replay_checkpoint = sa.Table(
    "replay_checkpoint",
    metadata,
    sa.Column("checkpoint_id", _uuid(), primary_key=True),
    sa.Column(
        "run_id",
        _uuid(),
        sa.ForeignKey(f"{SCHEMA}.replay_run.run_id", name="fk_replay_checkpoint_run"),
        nullable=False,
    ),
    sa.Column("input_index", sa.Integer, nullable=False),
    sa.Column("input_key", sa.Text, nullable=False),
    sa.Column("source_watermarks", postgresql.JSONB, nullable=False),
    sa.Column("engine_state", postgresql.JSONB, nullable=False),
    sa.Column("engine_state_hash", sa.Text, nullable=False),
    sa.Column("preceding_output_hash", sa.Text, nullable=False),
    sa.Column("runtime_manifest_hash", sa.Text, nullable=False),
    sa.Column("task_index", sa.Integer, nullable=False, server_default="0"),
    sa.Column("checkpoint_hash", sa.Text, nullable=False, unique=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("run_id", "input_index", name="uq_replay_checkpoint_input"),
    sa.CheckConstraint("input_index >= 0", name="ck_replay_checkpoint_input_index"),
    sa.CheckConstraint("task_index >= 0", name="ck_replay_checkpoint_task_index"),
)


def _record_table(name: str, *, evidence: bool = False) -> sa.Table:
    columns: list[sa.Column[Any]] = [
        sa.Column("record_id", _uuid(), primary_key=True),
        sa.Column("aggregate_id", _uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]
    if evidence:
        columns.insert(2, sa.Column("evidence_class", sa.Text, nullable=False))
    constraints: list[Any] = [
        sa.UniqueConstraint("aggregate_id", "payload_hash", name=f"uq_{name}_aggregate_hash")
    ]
    if evidence:
        constraints.append(
            sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name=f"ck_{name}_evidence_class")
        )
    return sa.Table(
        name,
        metadata,
        *columns,
        *constraints,
    )


experiment = _record_table("experiment", evidence=True)
feature_snapshot = _record_table("feature_snapshot", evidence=True)
decision = _record_table("decision", evidence=True)
policy_check = _record_table("policy_check")
sim_order_event = _record_table("sim_order_event", evidence=True)
sim_fill = _record_table("sim_fill", evidence=True)
position_projection = _record_table("position_projection", evidence=True)
cost_projection = _record_table("cost_projection", evidence=True)
incident = _record_table("incident")
incident_occurrence = _record_table("incident_occurrence")
reconciliation = _record_table("reconciliation", evidence=True)
export_manifest = _record_table("export_manifest", evidence=True)
model_review = _record_table("model_review", evidence=True)
