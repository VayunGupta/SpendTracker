from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_tracker.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class PlaidItem(Base):
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    access_token: Mapped[str] = mapped_column(Text)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255))
    cursor: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    accounts: Mapped[List["Account"]] = relationship(back_populates="plaid_item")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    plaid_item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"))
    account_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    mask: Mapped[Optional[str]] = mapped_column(String(10))
    type: Mapped[Optional[str]] = mapped_column(String(80))
    subtype: Mapped[Optional[str]] = mapped_column(String(80))

    plaid_item: Mapped[PlaidItem] = relationship(back_populates="accounts")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("transaction_id", name="uq_bank_transaction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(255), index=True)
    transaction_id: Mapped[str] = mapped_column(String(255), index=True)
    merchant_name: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[Optional[str]] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    iso_currency_code: Mapped[Optional[str]] = mapped_column(String(10))
    authorized_date: Mapped[Optional[date]] = mapped_column(Date)
    date: Mapped[date] = mapped_column(Date, index=True)
    pending: Mapped[bool] = mapped_column(default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class SplitwiseExpense(Base):
    __tablename__ = "splitwise_expenses"
    __table_args__ = (UniqueConstraint("expense_id", name="uq_splitwise_expense_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(Integer, index=True)
    description: Mapped[str] = mapped_column(String(500))
    category: Mapped[Optional[str]] = mapped_column(String(255))
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency_code: Mapped[Optional[str]] = mapped_column(String(10))
    date: Mapped[date] = mapped_column(Date, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    paid_share: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    owed_share: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class SyncState(Base):
    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ManualSpend(Base):
    __tablename__ = "manual_spend"
    __table_args__ = (UniqueConstraint("bank_transaction_id", name="uq_manual_spend_bank_transaction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_transaction_id: Mapped[int] = mapped_column(ForeignKey("bank_transactions.id"), index=True)
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ReconciledSpend(Base):
    __tablename__ = "reconciled_spend"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    bank_transaction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bank_transactions.id"), nullable=True)
    splitwise_expense_id: Mapped[Optional[int]] = mapped_column(ForeignKey("splitwise_expenses.id"), nullable=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(500))
    original_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    adjusted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
