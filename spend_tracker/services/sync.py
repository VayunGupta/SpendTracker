from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from spend_tracker.config import Settings
from spend_tracker.models import Account, BankTransaction, PlaidItem, SplitwiseExpense, SyncState
from spend_tracker.providers.plaid import PlaidClient
from spend_tracker.providers.splitwise import SplitwiseClient


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def money(value: Any) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


async def save_plaid_item(
    db: Session,
    settings: Settings,
    public_token: str,
    institution_name: Optional[str] = None,
) -> PlaidItem:
    client = PlaidClient(settings)
    exchange = await client.exchange_public_token(public_token)
    access_token = exchange["access_token"]
    item_id = exchange["item_id"]
    item_response = await client.get_item(access_token)
    item_institution_name = institution_name or item_response.get("item", {}).get("institution_name")

    plaid_item = db.scalar(select(PlaidItem).where(PlaidItem.item_id == item_id))
    if plaid_item is None:
        plaid_item = PlaidItem(item_id=item_id, access_token=access_token, institution_name=item_institution_name)
        db.add(plaid_item)
    else:
        plaid_item.access_token = access_token
        plaid_item.institution_name = item_institution_name

    db.flush()
    accounts_response = await client.get_accounts(access_token)
    for account in accounts_response.get("accounts", []):
        existing = db.scalar(select(Account).where(Account.account_id == account["account_id"]))
        if existing is None:
            existing = Account(account_id=account["account_id"], plaid_item_id=plaid_item.id, name=account["name"])
            db.add(existing)
        existing.name = account["name"]
        existing.mask = account.get("mask")
        existing.type = account.get("type")
        existing.subtype = account.get("subtype")

    db.commit()
    db.refresh(plaid_item)
    return plaid_item


async def sync_plaid_transactions(db: Session, settings: Settings) -> int:
    client = PlaidClient(settings)
    changed = 0
    for item in db.scalars(select(PlaidItem)).all():
        has_more = True
        cursor = item.cursor
        while has_more:
            response = await client.sync_transactions(item.access_token, cursor)
            for transaction in response.get("added", []) + response.get("modified", []):
                upsert_bank_transaction(db, transaction)
                changed += 1
            for removed in response.get("removed", []):
                existing = db.scalar(
                    select(BankTransaction).where(BankTransaction.transaction_id == removed["transaction_id"])
                )
                if existing is not None:
                    db.delete(existing)
                    changed += 1
            cursor = response.get("next_cursor", cursor)
            has_more = bool(response.get("has_more"))
        item.cursor = cursor
    db.commit()
    return changed


def upsert_bank_transaction(db: Session, transaction: Dict[str, Any]) -> BankTransaction:
    existing = db.scalar(select(BankTransaction).where(BankTransaction.transaction_id == transaction["transaction_id"]))
    if existing is None:
        existing = BankTransaction(
            account_id=transaction["account_id"],
            transaction_id=transaction["transaction_id"],
            name=transaction["name"],
            amount=money(transaction["amount"]),
            date=date.fromisoformat(transaction["date"]),
            raw_json=json.dumps(transaction),
        )
        db.add(existing)

    existing.account_id = transaction["account_id"]
    existing.merchant_name = transaction.get("merchant_name")
    existing.name = transaction["name"]
    existing.category = ", ".join(transaction.get("category") or []) or None
    existing.amount = money(transaction["amount"])
    existing.iso_currency_code = transaction.get("iso_currency_code")
    existing.authorized_date = parse_date(transaction.get("authorized_date"))
    existing.date = date.fromisoformat(transaction["date"])
    existing.pending = bool(transaction.get("pending"))
    existing.raw_json = json.dumps(transaction)
    return existing


async def sync_splitwise_expenses(db: Session, settings: Settings) -> int:
    client = SplitwiseClient(settings)
    splitwise_user_id = settings.splitwise_user_id
    if splitwise_user_id is None:
        current_user = await client.get_current_user()
        splitwise_user_id = int(current_user["id"])

    sync_started_at = datetime.now(timezone.utc)
    cursor = get_sync_state(db, "splitwise.updated_after")
    changed = 0
    offset = 0
    limit = 100
    while True:
        expenses = await client.get_expenses(limit=limit, offset=offset, updated_after=cursor)
        for expense in expenses:
            upsert_splitwise_expense(db, splitwise_user_id, expense)
            changed += 1
        if len(expenses) < limit:
            break
        offset += limit

    set_sync_state(db, "splitwise.updated_after", sync_started_at.isoformat().replace("+00:00", "Z"))
    db.commit()
    return changed


def get_sync_state(db: Session, key: str) -> Optional[str]:
    state = db.get(SyncState, key)
    return state.value if state else None


def set_sync_state(db: Session, key: str, value: str) -> None:
    state = db.get(SyncState, key)
    if state is None:
        state = SyncState(key=key, value=value)
        db.add(state)
    else:
        state.value = value


def upsert_splitwise_expense(db: Session, splitwise_user_id: int, expense: Dict[str, Any]) -> SplitwiseExpense:
    expense_id = int(expense["id"])
    existing = db.scalar(select(SplitwiseExpense).where(SplitwiseExpense.expense_id == expense_id))
    if existing is None:
        existing = SplitwiseExpense(
            expense_id=expense_id,
            description=expense.get("description") or "Splitwise expense",
            cost=money(expense.get("cost")),
            date=parse_date(expense.get("date")) or date.today(),
            raw_json=json.dumps(expense),
        )
        db.add(existing)

    my_paid_share = Decimal("0.00")
    my_owed_share = Decimal("0.00")
    for user in expense.get("users", []):
        user_info = user.get("user") or {}
        if int(user_info.get("id", 0)) == splitwise_user_id:
            my_paid_share = money(user.get("paid_share"))
            my_owed_share = money(user.get("owed_share"))
            break

    existing.description = expense.get("description") or "Splitwise expense"
    category = expense.get("category") or {}
    existing.category = category.get("name")
    existing.cost = money(expense.get("cost"))
    existing.currency_code = expense.get("currency_code")
    existing.date = parse_date(expense.get("date")) or date.today()
    creator = expense.get("created_by") or {}
    existing.created_by_user_id = creator.get("id")
    existing.paid_share = my_paid_share
    existing.owed_share = my_owed_share
    existing.deleted_at = datetime.fromisoformat(expense["deleted_at"].replace("Z", "+00:00")) if expense.get("deleted_at") else None
    existing.raw_json = json.dumps(expense)
    return existing
