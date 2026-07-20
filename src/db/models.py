from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    criteria_json: Mapped[dict] = mapped_column(JSONB)
    schedule: Mapped[str] = mapped_column(String(16), default="daily")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_queries_due", "is_active", "next_run_at"),)


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_runs_query_started", "search_query_id", "started_at"),)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    ogrn: Mapped[str | None] = mapped_column(String(15), unique=True, index=True)
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(512))
    full_name: Mapped[str | None] = mapped_column(String(1024))
    opf: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(32))
    region_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region_name: Mapped[str | None] = mapped_column(String(128))
    registration_date: Mapped[date | None] = mapped_column(Date)
    main_okved: Mapped[str | None] = mapped_column(String(16), index=True)
    okved_list: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    revenue_year: Mapped[int | None] = mapped_column(Integer)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    tax_status: Mapped[str] = mapped_column(String(24), default="unknown")
    tax_regimes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    tax_source: Mapped[str | None] = mapped_column(String(64))

    phones: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    emails: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    website: Mapped[str | None] = mapped_column(String(255))
    manager_name: Mapped[str | None] = mapped_column(String(255))

    source: Mapped[str] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data_json: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FnsDataset(Base):
    """Состояние загруженного набора открытых данных ФНС.

    is_complete критичен: вывод «спецрежимов нет» допустим только если набор
    загружен целиком. При частичной загрузке отсутствие ИНН ничего не означает.
    """

    __tablename__ = "fns_datasets"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    file_id: Mapped[str | None] = mapped_column(String(255))
    actual_date: Mapped[date | None] = mapped_column(Date)
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_loaded: Mapped[int] = mapped_column(Integer, default=0)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class FnsRecord(Base):
    __tablename__ = "fns_records"

    # записей миллионы — обычного int не хватит с запасом
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inn: Mapped[str] = mapped_column(String(12), index=True)
    dataset_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(1000))
    data: Mapped[dict] = mapped_column(JSONB)
    actual_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("inn", "dataset_code", name="uq_fns_inn_dataset"),
        Index("ix_fns_lookup", "dataset_code", "inn"),
    )


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), index=True
    )
    search_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_runs.id", ondelete="SET NULL")
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB)
    change_reason: Mapped[list | None] = mapped_column(JSONB)

    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("search_query_id", "company_id", name="uq_result_query_company"),
        Index("ix_results_listing", "search_query_id", "is_hidden", "first_seen_at"),
    )
