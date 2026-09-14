"""Add durable historical replay runs and checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_historical_replay"
down_revision = "0002_transactional_evidence"
branch_labels = None
depends_on = None

SCHEMA = "sopara"
EVIDENCE_CLASS_CHECK = (
    "evidence_class IN ('OBSERVED_NES','SYNTHETIC_NES_PROXY','NO_EXECUTION_EVIDENCE')"
)


def uuid_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "replay_run",
        uuid_column("run_id"),
        sa.Column("dataset_manifest_hash", sa.Text(), nullable=False),
        sa.Column("experiment_hash", sa.Text(), nullable=False),
        sa.Column("runtime_manifest_hash", sa.Text(), nullable=False),
        sa.Column("evidence_class", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("last_input_index", sa.Integer(), server_default="-1", nullable=False),
        sa.Column("checkpoint_hash", sa.Text()),
        sa.Column("result_object_key", sa.Text()),
        sa.Column("result_generation", sa.BigInteger()),
        sa.Column("result_hash", sa.Text()),
        sa.Column("error_reason", sa.Text()),
        sa.Column("row_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_replay_run"),
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
        schema=SCHEMA,
    )
    op.create_table(
        "replay_checkpoint",
        uuid_column("checkpoint_id"),
        uuid_column("run_id"),
        sa.Column("input_index", sa.Integer(), nullable=False),
        sa.Column("input_key", sa.Text(), nullable=False),
        sa.Column("source_watermarks", postgresql.JSONB(), nullable=False),
        sa.Column("engine_state", postgresql.JSONB(), nullable=False),
        sa.Column("engine_state_hash", sa.Text(), nullable=False),
        sa.Column("preceding_output_hash", sa.Text(), nullable=False),
        sa.Column("runtime_manifest_hash", sa.Text(), nullable=False),
        sa.Column("task_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checkpoint_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("checkpoint_id", name="pk_replay_checkpoint"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["sopara.replay_run.run_id"], name="fk_replay_checkpoint_run"
        ),
        sa.UniqueConstraint("checkpoint_hash", name="uq_replay_checkpoint_hash"),
        sa.UniqueConstraint("run_id", "input_index", name="uq_replay_checkpoint_input"),
        sa.CheckConstraint("input_index >= 0", name="ck_replay_checkpoint_input_index"),
        sa.CheckConstraint("task_index >= 0", name="ck_replay_checkpoint_task_index"),
        schema=SCHEMA,
    )
    op.execute(
        "CREATE TRIGGER trg_replay_checkpoint_append_only BEFORE UPDATE OR DELETE "
        "ON sopara.replay_checkpoint FOR EACH ROW "
        "EXECUTE FUNCTION sopara.reject_ledger_mutation()"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON sopara.replay_run TO replay_engine")
    op.execute(
        "GRANT SELECT, INSERT ON sopara.replay_checkpoint, sopara.dataset_manifest, "
        "sopara.market_object, sopara.experiment, sopara.feature_snapshot, sopara.decision, "
        "sopara.policy_check, sopara.sim_order_event, sopara.sim_fill, "
        "sopara.position_projection, sopara.cost_projection, sopara.export_manifest "
        "TO replay_engine"
    )
    op.execute("GRANT SELECT ON sopara.replay_run, sopara.replay_checkpoint TO reconciler")


def downgrade() -> None:
    raise RuntimeError("Sopara migrations are forward-only")
