from datetime import timedelta
from decimal import Decimal
import json
from typing import List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from spend_tracker.models import Account, BankTransaction, ManualSpend, PlaidItem, ReconciledSpend, SplitwiseExpense


def normalize(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {word for word in cleaned.split() if len(word) > 2}


def is_spend(amount: Decimal) -> bool:
    return amount > 0


def is_balance_movement(bank: BankTransaction) -> bool:
    text = f"{bank.name or ''} {bank.merchant_name or ''}".lower()
    patterns = [
        "e-payment discover",
        "discover ach web",
        "capital one ach web",
        "capital pmt",
        "crcardpmt capital one",
        "mobile pmtcapital one",
        "online transfer to",
        "online transfer from",
    ]
    return any(pattern in text for pattern in patterns)


def is_counted_bank_spend(bank: BankTransaction) -> bool:
    return is_spend(bank.amount) and not bank.pending and not is_balance_movement(bank)


def match_score(bank: BankTransaction, expense: SplitwiseExpense) -> int:
    if is_splitwise_payment(expense):
        return 0
    if expense.paid_share <= 0 and expense.owed_share <= 0:
        return 0

    score = 0
    amount_delta = abs(bank.amount - expense.cost)
    if amount_delta <= Decimal("0.02"):
        score += 5
    elif amount_delta <= Decimal("1.00"):
        score += 2
    days = abs((bank.date - expense.date).days)
    if days <= 1:
        score += 3
    elif days <= 4:
        score += 1
    bank_words = normalize(bank.merchant_name or bank.name)
    split_words = normalize(expense.description)
    if bank_words and split_words and bank_words.intersection(split_words):
        score += 2
    return score


def can_auto_match(bank: BankTransaction, expense: SplitwiseExpense) -> bool:
    if is_splitwise_payment(expense):
        return False
    if expense.paid_share <= 0 and expense.owed_share <= 0:
        return False
    amount_delta = abs(bank.amount - expense.cost)
    if amount_delta > Decimal("0.02"):
        bank_words = normalize(bank.merchant_name or bank.name)
        split_words = normalize(expense.description)
        if not bank_words.intersection(split_words):
            return False
    return match_score(bank, expense) >= 8


def adjusted_bank_amount(bank: BankTransaction, expense: SplitwiseExpense) -> Decimal:
    return bank.amount


def payer_names(expense: SplitwiseExpense) -> List[str]:
    try:
        raw = json.loads(expense.raw_json)
    except ValueError:
        return []
    names = []
    for user in raw.get("users", []):
        if money_like(user.get("paid_share")) <= 0:
            continue
        info = user.get("user") or {}
        first_name = info.get("first_name")
        last_name = info.get("last_name")
        name = " ".join(part for part in [first_name, last_name] if part)
        if name:
            names.append(name)
    return names


def current_user_shares(expense: SplitwiseExpense) -> Tuple[Decimal, Decimal]:
    try:
        raw = json.loads(expense.raw_json)
    except ValueError:
        return Decimal("0.00"), Decimal("0.00")
    for user in raw.get("users", []):
        info = user.get("user") or {}
        if info.get("first_name") == "Vayun":
            return money_like(user.get("paid_share")), money_like(user.get("owed_share"))
    return Decimal("0.00"), Decimal("0.00")


def splitwise_payment_offsets(splitwise_expenses: List[SplitwiseExpense]) -> Tuple[set, set]:
    received_payments = []
    paid_out_payments = []
    for expense in splitwise_expenses:
        if not is_splitwise_payment(expense):
            continue
        paid_share, owed_share = current_user_shares(expense)
        if paid_share > 0:
            received_payments.append((expense.date, paid_share))
        if owed_share > 0:
            paid_out_payments.append((expense.date, owed_share))

    offset_expense_ids = set()
    for expense in splitwise_expenses:
        if is_splitwise_payment(expense):
            continue
        paid_share, owed_share = current_user_shares(expense)
        if owed_share > 0 and paid_share == 0:
            for payment_date, amount in received_payments:
                if amount == owed_share and abs((payment_date - expense.date).days) <= 14:
                    offset_expense_ids.add(expense.id)
                    break
        if paid_share > 0 and owed_share == 0:
            for payment_date, amount in paid_out_payments:
                if amount == paid_share and abs((payment_date - expense.date).days) <= 14:
                    offset_expense_ids.add(expense.id)
                    break
    return offset_expense_ids, {expense.id for expense in splitwise_expenses if is_splitwise_payment(expense)}


def current_user_net_balance(expense: SplitwiseExpense) -> Decimal:
    try:
        raw = json.loads(expense.raw_json)
    except ValueError:
        return Decimal("0.00")
    for user in raw.get("users", []):
        info = user.get("user") or {}
        if info.get("first_name") == "Vayun":
            return money_like(user.get("net_balance"))
    return Decimal("0.00")


def money_like(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def splitwise_only_note(expense: SplitwiseExpense) -> str:
    names = payer_names(expense)
    if names:
        return f"Splitwise expense paid by {', '.join(names)}"
    return "Splitwise expense paid by someone else"


def is_splitwise_payment(expense: SplitwiseExpense) -> bool:
    try:
        raw = json.loads(expense.raw_json)
    except ValueError:
        return False
    if raw.get("payment") is True:
        return True
    category = raw.get("category") or {}
    return str(category.get("name") or "").lower() == "payment"


def institution_source_name(institution_name: Optional[str]) -> str:
    if not institution_name:
        return "bank"
    lowered = institution_name.lower()
    if "capital one" in lowered:
        return "capital one"
    if "discover" in lowered:
        return "discover"
    if "pnc" in lowered:
        return "pnc"
    return institution_name


def bank_sources_by_account(db: Session) -> dict:
    rows = db.execute(
        select(Account.account_id, PlaidItem.institution_name)
        .join(PlaidItem, Account.plaid_item_id == PlaidItem.id)
    ).all()
    return {
        account_id: institution_source_name(institution_name)
        for account_id, institution_name in rows
    }


def recompute_reconciled_spend(db: Session) -> int:
    db.execute(delete(ReconciledSpend))

    account_sources = bank_sources_by_account(db)
    bank_dates = db.execute(select(BankTransaction.date).where(BankTransaction.pending.is_(False))).all()
    min_bank_date = min((row[0] for row in bank_dates), default=None)
    max_bank_date = max((row[0] for row in bank_dates), default=None)
    splitwise_expenses = db.scalars(
        select(SplitwiseExpense).where(SplitwiseExpense.deleted_at.is_(None))
    ).all()
    offset_splitwise_ids, payment_splitwise_ids = splitwise_payment_offsets(splitwise_expenses)
    unmatched_splitwise_ids = {expense.id for expense in splitwise_expenses}

    rows: List[ReconciledSpend] = []
    for bank in db.scalars(select(BankTransaction).where(BankTransaction.pending.is_(False))).all():
        if not is_counted_bank_spend(bank):
            continue
        bank_source = account_sources.get(bank.account_id, "bank")
        candidates = [
            expense
            for expense in splitwise_expenses
            if expense.id in unmatched_splitwise_ids
            and expense.id not in offset_splitwise_ids
            and expense.id not in payment_splitwise_ids
            and abs((bank.date - expense.date).days) <= 5
            and abs(bank.amount - expense.cost) <= Decimal("1.00")
        ]
        best = max(candidates, key=lambda expense: match_score(bank, expense), default=None)
        if best is not None and can_auto_match(bank, best):
            unmatched_splitwise_ids.remove(best.id)
            rows.append(
                ReconciledSpend(
                    source=bank_source,
                    bank_transaction_id=bank.id,
                    splitwise_expense_id=best.id,
                    date=bank.date,
                    description=bank.merchant_name or bank.name,
                    original_amount=bank.amount,
                    adjusted_amount=adjusted_bank_amount(bank, best),
                    note=f"Matched Splitwise expense for audit: {best.description}",
                )
            )
        else:
            rows.append(
                ReconciledSpend(
                    source=bank_source,
                    bank_transaction_id=bank.id,
                    date=bank.date,
                    description=bank.merchant_name or bank.name,
                    original_amount=bank.amount,
                    adjusted_amount=bank.amount,
                    note=None,
                )
            )

    splitwise_net = Decimal("0.00")
    for expense in splitwise_expenses:
        splitwise_net += current_user_net_balance(expense)

    if splitwise_net != 0:
        rows.append(
            ReconciledSpend(
                source="splitwise",
                date=max_bank_date or max((expense.date for expense in splitwise_expenses), default=None),
                description="Splitwise net balance",
                original_amount=Decimal("0.00"),
                adjusted_amount=-splitwise_net,
                note="Net of imported Splitwise expenses and settlements",
            )
        )

    for manual in db.scalars(select(ManualSpend)).all():
        bank = db.get(BankTransaction, manual.bank_transaction_id)
        if bank is None:
            continue
        rows.append(
            ReconciledSpend(
                source="manual",
                bank_transaction_id=bank.id,
                date=bank.date,
                description=manual.description,
                original_amount=Decimal("0.00"),
                adjusted_amount=manual.amount,
                note=manual.note or f"Manual resolution for transfer: {bank.name}",
            )
        )

    db.add_all(rows)
    db.commit()
    return len(rows)


def nearby_window(expense: SplitwiseExpense) -> Tuple:
    return expense.date - timedelta(days=5), expense.date + timedelta(days=5)
