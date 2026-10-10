"""Add claim leases and heartbeat lookup support to ingestion jobs."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_0009"
down_revision = "20260923_0008"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    columns = {
        column["name"] for column in _inspector().get_columns("ingestion_jobs")
    }
    if "claim_token" not in columns:
        op.add_column(
            "ingestion_jobs",
            sa.Column("claim_token", sa.String(length=36), nullable=True),
        )
    if "claimed_at" not in columns:
        op.add_column(
            "ingestion_jobs",
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
        )
    if "heartbeat_at" not in columns:
        op.add_column(
            "ingestion_jobs",
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        )

    indexes = {index["name"] for index in _inspector().get_indexes("ingestion_jobs")}
    if "ix_ingestion_jobs_status_heartbeat_at" not in indexes:
        op.create_index(
            "ix_ingestion_jobs_status_heartbeat_at",
            "ingestion_jobs",
            ["status", "heartbeat_at"],
            unique=False,
        )


def downgrade() -> None:
    indexes = {index["name"] for index in _inspector().get_indexes("ingestion_jobs")}
    if "ix_ingestion_jobs_status_heartbeat_at" in indexes:
        op.drop_index(
            "ix_ingestion_jobs_status_heartbeat_at",
            table_name="ingestion_jobs",
        )

    columns = {
        column["name"] for column in _inspector().get_columns("ingestion_jobs")
    }
    for column_name in ("heartbeat_at", "claimed_at", "claim_token"):
        if column_name in columns:
            op.drop_column("ingestion_jobs", column_name)
