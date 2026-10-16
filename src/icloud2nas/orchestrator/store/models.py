"""SQLAlchemy models (spec §4). Timestamps are aware UTC; ids are uuid4 hex strings."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Aware-UTC datetimes over SQLite's naive DateTime.

    Strip tz on write, re-attach UTC on read.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; store aware UTC values (use utcnow())")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(200))
    apple_id: Mapped[str] = mapped_column(String(320), unique=True)
    region: Mapped[str] = mapped_column(String(3))
    password_enc: Mapped[bytes] = mapped_column(LargeBinary)
    client_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="new")
    last_auth_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    failures: Mapped[int] = mapped_column(default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    libraries: Mapped[list[Library]] = relationship(back_populates="account")


class Library(Base):
    __tablename__ = "libraries"
    __table_args__ = (UniqueConstraint("account_id", "zone_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(10))  # personal | shared
    zone_name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    target_dir: Mapped[str] = mapped_column(String(500))  # relative to photos root
    enabled: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    interval_min: Mapped[int] = mapped_column(default=30)
    nightly_at: Mapped[str] = mapped_column(String(5), default="03:00")
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_nightly_on: Mapped[date | None] = mapped_column(default=None)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")

    account: Mapped[Account] = relationship(back_populates="libraries")

    @property
    def stats(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.stats_json or "{}")
        return data


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    library_id: Mapped[str | None] = mapped_column(ForeignKey("libraries.id"), default=None)
    mode: Mapped[str] = mapped_column(String(10))
    trigger: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    exit_code: Mapped[int | None] = mapped_column(default=None)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    error_kind: Mapped[str | None] = mapped_column(String(20), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    log_path: Mapped[str | None] = mapped_column(String(500), default=None)
    plan_file: Mapped[str | None] = mapped_column(String(500), default=None)

    @property
    def stats(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.stats_json or "{}")
        return data


class ReconcilePlan(Base):
    __tablename__ = "reconcile_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    extraneous_count: Mapped[int] = mapped_column(default=0)
    plan_file: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), default="pending")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
