"""Create the pre-WP2 schema marker fixture."""

from alembic import op
import sqlalchemy as sa

revision = "0001_pre_wp2"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA sopara")
    op.create_table(
        "schema_metadata",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name="pk_schema_metadata"),
        schema="sopara",
    )
    op.execute(
        "INSERT INTO sopara.schema_metadata (key, value) VALUES ('schema_fixture', 'pre-wp2')"
    )


def downgrade() -> None:
    raise RuntimeError("Sopara migrations are forward-only")
