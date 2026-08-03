from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_import_jobs"
down_revision: str | Sequence[str] | None = "0004_import_staging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "job_type",
            sa.Enum(
                "parse_import",
                "confirm_import",
                "recalculate_portfolio",
                "rollback_import",
                name="import_job_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                name="import_job_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column(
            "last_error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
        sa.CheckConstraint("attempts >= 0", name="import_job_attempts_nonnegative"),
        sa.CheckConstraint(
            "(status = 'running' AND locked_at IS NOT NULL AND locked_by IS NOT NULL) "
            "OR (status <> 'running' AND locked_at IS NULL AND locked_by IS NULL)",
            name="import_job_lock_state",
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR jsonb_typeof(last_error) = 'object'",
            name="import_job_last_error_object",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name="fk_import_jobs_batch_id_import_batches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_jobs"),
        sa.UniqueConstraint("batch_id", "job_type", name="uq_import_jobs_batch_type"),
    )
    op.create_index(
        "ix_import_jobs_claim",
        "import_jobs",
        ["status", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_claim", table_name="import_jobs")
    op.drop_table("import_jobs")
