"""Начальная схема: users, search_queries, search_runs, companies, search_results

Revision ID: 0001
Revises:
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("criteria_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schedule", sa.String(length=16), nullable=False, server_default="daily"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_queries_user_id", "search_queries", ["user_id"])
    op.create_index("ix_queries_due", "search_queries", ["is_active", "next_run_at"])

    op.create_table(
        "search_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_query_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["search_query_id"], ["search_queries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_runs_search_query_id", "search_runs", ["search_query_id"])
    op.create_index("ix_runs_query_started", "search_runs", ["search_query_id", "started_at"])

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ogrn", sa.String(length=15), nullable=True),
        sa.Column("inn", sa.String(length=12), nullable=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("full_name", sa.String(length=1024), nullable=True),
        sa.Column("opf", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("region_code", sa.String(length=2), nullable=True),
        sa.Column("region_name", sa.String(length=128), nullable=True),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("main_okved", sa.String(length=16), nullable=True),
        sa.Column("okved_list", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("revenue_year", sa.Integer(), nullable=True),
        sa.Column("revenue", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("profit", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("tax_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("tax_regimes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("tax_source", sa.String(length=64), nullable=True),
        sa.Column("phones", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("emails", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("manager_name", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ogrn"),
    )
    op.create_index("ix_companies_ogrn", "companies", ["ogrn"])
    op.create_index("ix_companies_inn", "companies", ["inn"])
    op.create_index("ix_companies_region_code", "companies", ["region_code"])
    op.create_index("ix_companies_main_okved", "companies", ["main_okved"])

    op.create_table(
        "search_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_query_id", sa.Integer(), nullable=False),
        sa.Column("search_run_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("change_reason", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["search_query_id"], ["search_queries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_query_id", "company_id", name="uq_result_query_company"),
    )
    op.create_index("ix_search_results_search_query_id", "search_results", ["search_query_id"])
    op.create_index(
        "ix_results_listing", "search_results", ["search_query_id", "is_hidden", "first_seen_at"]
    )


def downgrade() -> None:
    op.drop_table("search_results")
    op.drop_table("companies")
    op.drop_table("search_runs")
    op.drop_table("search_queries")
    op.drop_table("users")
