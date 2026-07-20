"""Хранилище открытых данных ФНС

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fns_datasets",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=True),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("records_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_loaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "fns_records",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("inn", sa.String(length=12), nullable=False),
        sa.Column("dataset_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=1000), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inn", "dataset_code", name="uq_fns_inn_dataset"),
    )
    op.create_index("ix_fns_records_inn", "fns_records", ["inn"])
    op.create_index("ix_fns_lookup", "fns_records", ["dataset_code", "inn"])


def downgrade() -> None:
    op.drop_table("fns_records")
    op.drop_table("fns_datasets")
