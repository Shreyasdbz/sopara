"""Create the transactional evidence spine."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_transactional_evidence"
down_revision = "0001_pre_wp2"
branch_labels = None
depends_on = None

SCHEMA = "sopara"
EVIDENCE_CLASS_CHECK = (
    "evidence_class IN ('OBSERVED_NES','SYNTHETIC_NES_PROXY','NO_EXECUTION_EVIDENCE')"
)


def uuid_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def record_table(name: str, *, evidence: bool = False) -> None:
    columns = [
        uuid_column("record_id"),
        uuid_column("aggregate_id"),
    ]
    if evidence:
        columns.append(sa.Column("evidence_class", sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("payload_hash", sa.Text(), nullable=False),
            sa.Column(
                "recorded_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("record_id", name=f"pk_{name}"),
            sa.UniqueConstraint("aggregate_id", "payload_hash", name=f"uq_{name}_aggregate_hash"),
        ]
    )
    if evidence:
        columns.append(sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name=f"ck_{name}_evidence_class"))
    op.create_table(name, *columns, schema=SCHEMA)


def upgrade() -> None:
    op.create_table(
        "trading_session",
        uuid_column("session_id"),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("evidence_class", sa.Text(), nullable=False),
        uuid_column("experiment_id"),
        sa.Column("runtime_manifest_hash", sa.Text(), nullable=False),
        sa.Column("calendar_version", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        uuid_column("reconciliation_id", nullable=True),
        sa.Column("row_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("session_id", name="pk_trading_session"),
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
        schema=SCHEMA,
    )
    op.create_table(
        "session_window",
        uuid_column("window_id"),
        uuid_column("session_id"),
        sa.Column("window_code", sa.Text(), nullable=False),
        sa.Column("eligible_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cloud_run_execution", sa.Text()),
        sa.Column("lease_epoch", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("terminal_watermarks", postgresql.JSONB()),
        uuid_column("manifest_id", nullable=True),
        sa.Column(
            "invalid_reason_codes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("row_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("window_id", name="pk_session_window"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sopara.trading_session.session_id"], name="fk_window_session"
        ),
        sa.UniqueConstraint("session_id", "window_code", name="uq_session_window_code"),
        sa.CheckConstraint("window_code IN ('AM', 'PM')", name="ck_session_window_code"),
        sa.CheckConstraint(
            "eligible_end_utc > eligible_start_utc", name="ck_session_window_interval"
        ),
        sa.CheckConstraint("lease_epoch >= 0", name="ck_session_window_lease_epoch"),
        sa.CheckConstraint("row_version > 0", name="ck_session_window_row_version"),
        schema=SCHEMA,
    )
    op.create_table(
        "window_lease",
        sa.Column("lease_key", sa.Text(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("holder_execution", sa.Text(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("lease_key", name="pk_window_lease"),
        sa.CheckConstraint("lease_epoch > 0", name="ck_window_lease_epoch"),
        sa.CheckConstraint("expires_at > acquired_at", name="ck_window_lease_interval"),
        schema=SCHEMA,
    )
    op.create_table(
        "command_inbox",
        uuid_column("command_id"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        uuid_column("target_id"),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("actor_email_hash", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), server_default="RECEIVED", nullable=False),
        uuid_column("result_event_id", nullable=True),
        sa.PrimaryKeyConstraint("command_id", name="pk_command_inbox"),
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
        schema=SCHEMA,
    )
    op.create_table(
        "domain_event",
        uuid_column("event_id"),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        uuid_column("aggregate_id"),
        sa.Column("aggregate_seq", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("occurred_at_ns", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        uuid_column("correlation_id"),
        uuid_column("causation_id"),
        sa.Column(
            "reason_codes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("runtime_manifest_hash", sa.Text(), nullable=False),
        sa.Column("lease_key", sa.Text()),
        sa.Column("lease_epoch", sa.BigInteger()),
        sa.PrimaryKeyConstraint("event_id", name="pk_domain_event"),
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
        schema=SCHEMA,
    )
    op.create_table(
        "audit_event",
        sa.Column("audit_seq", sa.BigInteger(), sa.Identity(), nullable=False),
        uuid_column("audit_id"),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        uuid_column("correlation_id"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("audit_seq", name="pk_audit_event"),
        sa.UniqueConstraint("audit_id", name="uq_audit_event_audit_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "outbox_event",
        sa.Column("outbox_seq", sa.BigInteger(), sa.Identity(), nullable=False),
        uuid_column("event_id"),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("outbox_seq", name="pk_outbox_event"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["sopara.domain_event.event_id"], name="fk_outbox_domain_event"
        ),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_event_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "aggregate_projection",
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        uuid_column("aggregate_id"),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        uuid_column("last_event_id"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("aggregate_type", "aggregate_id", name="pk_aggregate_projection"),
        sa.CheckConstraint("row_version > 0", name="ck_aggregate_projection_row_version"),
        schema=SCHEMA,
    )
    op.create_table(
        "data_source",
        uuid_column("source_id"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("source_id", name="pk_data_source"),
        sa.UniqueConstraint("name", name="uq_data_source_name"),
        schema=SCHEMA,
    )
    op.create_table(
        "entitlement_snapshot",
        uuid_column("snapshot_id"),
        uuid_column("source_id"),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_through", sa.Date(), nullable=False),
        sa.Column("permitted_uses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("policy_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_entitlement_snapshot"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sopara.data_source.source_id"], name="fk_entitlement_source"
        ),
        sa.CheckConstraint(
            "effective_through >= effective_from", name="ck_entitlement_effective_interval"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "dataset_manifest",
        uuid_column("manifest_id"),
        uuid_column("dataset_id"),
        sa.Column("manifest_key", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("evidence_class", sa.Text(), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("manifest_id", name="pk_dataset_manifest"),
        sa.UniqueConstraint("manifest_key", name="uq_dataset_manifest_key"),
        sa.UniqueConstraint("manifest_hash", name="uq_dataset_manifest_hash"),
        sa.CheckConstraint("generation > 0", name="ck_dataset_manifest_generation"),
        sa.CheckConstraint("object_count >= 0", name="ck_dataset_manifest_object_count"),
        sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_dataset_manifest_evidence_class"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_object",
        uuid_column("object_id"),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("metageneration", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("crc32c", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("schema_id", sa.Text(), nullable=False),
        sa.Column("evidence_class", sa.Text(), nullable=False),
        sa.Column("source_range", postgresql.JSONB(), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("object_id", name="pk_market_object"),
        sa.UniqueConstraint("object_key", name="uq_market_object_key"),
        sa.UniqueConstraint("object_key", "generation", name="uq_market_object_generation"),
        sa.CheckConstraint("generation > 0", name="ck_market_object_generation"),
        sa.CheckConstraint("metageneration > 0", name="ck_market_object_metageneration"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_market_object_size"),
        sa.CheckConstraint(EVIDENCE_CLASS_CHECK, name="ck_market_object_evidence_class"),
        schema=SCHEMA,
    )
    op.create_table(
        "versioned_artifact",
        uuid_column("artifact_id"),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_versioned_artifact"),
        sa.UniqueConstraint("artifact_type", "version", name="uq_versioned_artifact_type_version"),
        schema=SCHEMA,
    )
    for name in (
        "experiment",
        "feature_snapshot",
        "decision",
        "policy_check",
        "sim_order_event",
        "sim_fill",
        "position_projection",
        "cost_projection",
        "incident",
        "incident_occurrence",
        "reconciliation",
        "export_manifest",
        "model_review",
    ):
        record_table(
            name,
            evidence=name
            in {
                "experiment",
                "feature_snapshot",
                "decision",
                "sim_order_event",
                "sim_fill",
                "position_projection",
                "cost_projection",
                "reconciliation",
                "export_manifest",
                "model_review",
            },
        )

    op.execute(
        """
        CREATE FUNCTION sopara.reject_ledger_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$ BEGIN
          RAISE EXCEPTION 'append-only ledger mutation rejected'
            USING ERRCODE = '55000';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_domain_event_append_only BEFORE UPDATE OR DELETE "
        "ON sopara.domain_event FOR EACH ROW EXECUTE FUNCTION sopara.reject_ledger_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_event_append_only BEFORE UPDATE OR DELETE "
        "ON sopara.audit_event FOR EACH ROW EXECUTE FUNCTION sopara.reject_ledger_mutation()"
    )
    op.execute("REVOKE ALL ON FUNCTION sopara.reject_ledger_mutation() FROM PUBLIC")

    for role in (
        "schema_owner",
        "web_command",
        "live_engine",
        "replay_engine",
        "reconciler",
        "model_importer",
        "readonly_owner",
    ):
        op.execute(
            f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA sopara FROM PUBLIC")
    op.execute(
        "GRANT USAGE ON SCHEMA sopara TO web_command, live_engine, replay_engine, "
        "reconciler, model_importer, readonly_owner"
    )
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA sopara TO readonly_owner")
    op.execute("GRANT SELECT ON sopara.aggregate_projection, sopara.outbox_event TO web_command")
    op.execute(
        "GRANT SELECT ON sopara.market_object, sopara.dataset_manifest, "
        "sopara.domain_event TO reconciler"
    )
    op.execute(
        "GRANT INSERT ON sopara.domain_event, sopara.outbox_event, "
        "sopara.audit_event TO live_engine, replay_engine"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON sopara.window_lease, "
        "sopara.aggregate_projection TO live_engine"
    )
    op.execute("GRANT INSERT ON sopara.model_review TO model_importer")
    op.execute(
        "REVOKE UPDATE, DELETE ON sopara.domain_event, sopara.audit_event FROM PUBLIC, "
        "web_command, live_engine, replay_engine, reconciler, model_importer, readonly_owner"
    )


def downgrade() -> None:
    raise RuntimeError("Sopara migrations are forward-only")
