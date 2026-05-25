from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import json
from typing import Dict, Optional, Tuple, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spend_tracker.config import Settings, get_settings
from spend_tracker.db import get_db, init_db
from spend_tracker.models import BankTransaction, ManualSpend, ReconciledSpend, SplitwiseExpense
from spend_tracker.providers.plaid import PlaidApiError, PlaidClient
from spend_tracker.providers.splitwise import SplitwiseApiError
from spend_tracker.services.reconciliation import is_balance_movement, is_counted_bank_spend, recompute_reconciled_spend
from spend_tracker.services.sync import save_plaid_item, sync_plaid_transactions, sync_splitwise_expenses

app = FastAPI(title="Spend Tracker")
app.mount("/static", StaticFiles(directory="spend_tracker/static"), name="static")
templates = Jinja2Templates(directory="spend_tracker/templates")


class PublicTokenPayload(BaseModel):
    public_token: str
    institution_name: Optional[str] = None


class ManualSpendPayload(BaseModel):
    bank_transaction_id: int
    description: str
    amount: Decimal
    note: Optional[str] = None


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/oauth/plaid", response_class=HTMLResponse)
def plaid_oauth_return(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/plaid/link-token")
async def create_link_token(settings: Settings = Depends(get_settings)) -> dict:
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise HTTPException(status_code=400, detail="Plaid credentials are not configured")
    try:
        return await PlaidClient(settings).create_link_token()
    except PlaidApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/api/plaid/exchange-public-token")
async def exchange_public_token(
    payload: PublicTokenPayload,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Union[str, int]]:
    try:
        item = await save_plaid_item(db, settings, payload.public_token, payload.institution_name)
    except PlaidApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"status": "connected", "item_id": item.item_id, "id": item.id}


@app.post("/api/sync/plaid")
async def sync_plaid(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Dict[str, int]:
    try:
        count = await sync_plaid_transactions(db, settings)
    except PlaidApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    reconciled = recompute_reconciled_spend(db)
    return {"changed": count, "reconciled_rows": reconciled}


@app.post("/api/sync/splitwise")
async def sync_splitwise(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Dict[str, int]:
    if not settings.splitwise_api_key:
        raise HTTPException(status_code=400, detail="Splitwise API key is not configured")
    try:
        count = await sync_splitwise_expenses(db, settings)
    except SplitwiseApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    reconciled = recompute_reconciled_spend(db)
    return {"changed": count, "reconciled_rows": reconciled}


@app.post("/api/reconcile")
def reconcile(db: Session = Depends(get_db)) -> Dict[str, int]:
    return {"reconciled_rows": recompute_reconciled_spend(db)}


@app.get("/api/transfer-review")
def transfer_review(db: Session = Depends(get_db)) -> dict:
    manual_by_bank_id = {
        manual.bank_transaction_id: manual
        for manual in db.scalars(select(ManualSpend)).all()
    }
    transfers = [
        transaction
        for transaction in db.scalars(
            select(BankTransaction)
            .where(BankTransaction.pending.is_(False), BankTransaction.amount > 0)
            .order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
        ).all()
        if is_balance_movement(transaction)
    ]
    return {
        "transfers": [
            {
                "id": transaction.id,
                "date": transaction.date.isoformat(),
                "description": transaction.merchant_name or transaction.name,
                "amount": float(transaction.amount),
                "manual": (
                    {
                        "id": manual_by_bank_id[transaction.id].id,
                        "description": manual_by_bank_id[transaction.id].description,
                        "amount": float(manual_by_bank_id[transaction.id].amount),
                        "note": manual_by_bank_id[transaction.id].note,
                    }
                    if transaction.id in manual_by_bank_id
                    else None
                ),
            }
            for transaction in transfers
        ]
    }


@app.get("/api/reimbursements")
def reimbursements(db: Session = Depends(get_db)) -> dict:
    credits = db.scalars(
        select(BankTransaction)
        .where(BankTransaction.pending.is_(False), BankTransaction.amount < 0)
        .order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
    ).all()
    reimbursement_rows = [
        transaction
        for transaction in credits
        if any(
            pattern in transaction.name.lower()
            for pattern in ["zel from", "zelle from", "venmo", "cash app"]
        )
    ]
    return {
        "reimbursements": [
            {
                "id": transaction.id,
                "date": transaction.date.isoformat(),
                "description": transaction.merchant_name or transaction.name,
                "amount": float(abs(transaction.amount)),
            }
            for transaction in reimbursement_rows
        ]
    }


@app.post("/api/manual-spend")
def create_manual_spend(payload: ManualSpendPayload, db: Session = Depends(get_db)) -> dict:
    bank_transaction = db.get(BankTransaction, payload.bank_transaction_id)
    if bank_transaction is None:
        raise HTTPException(status_code=404, detail="Bank transaction not found")
    if not is_balance_movement(bank_transaction):
        raise HTTPException(status_code=400, detail="Manual spend can only be attached to excluded transfers/payments")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Manual spend amount must be positive")

    manual = db.scalar(select(ManualSpend).where(ManualSpend.bank_transaction_id == payload.bank_transaction_id))
    if manual is None:
        manual = ManualSpend(bank_transaction_id=payload.bank_transaction_id, description=payload.description, amount=payload.amount)
        db.add(manual)
    manual.description = payload.description
    manual.amount = payload.amount
    manual.note = payload.note
    db.commit()
    reconciled = recompute_reconciled_spend(db)
    return {"status": "saved", "reconciled_rows": reconciled}


@app.delete("/api/manual-spend/{manual_spend_id}")
def delete_manual_spend(manual_spend_id: int, db: Session = Depends(get_db)) -> dict:
    manual = db.get(ManualSpend, manual_spend_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual spend not found")
    db.delete(manual)
    db.commit()
    reconciled = recompute_reconciled_spend(db)
    return {"status": "deleted", "reconciled_rows": reconciled}


def date_range_for_preset(preset: str, start: Optional[date], end: Optional[date]) -> Tuple[Optional[date], Optional[date]]:
    today = date.today()
    if preset == "all":
        return start, end
    if preset == "this_month":
        return today.replace(day=1), end
    if preset == "last_month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    if preset == "last_30_days":
        return today - timedelta(days=30), end
    if preset == "last_90_days":
        return today - timedelta(days=90), end
    if preset == "custom":
        return start, end
    raise HTTPException(status_code=400, detail="Unsupported date preset")


def apply_reconciled_filters(statement, start: Optional[date], end: Optional[date]):
    if start is not None:
        statement = statement.where(ReconciledSpend.date >= start)
    if end is not None:
        statement = statement.where(ReconciledSpend.date <= end)
    return statement


def splitwise_payment_summary(db: Session, start: Optional[date], end: Optional[date]) -> Dict[str, float]:
    statement = select(SplitwiseExpense).where(SplitwiseExpense.description == "Payment")
    if start is not None:
        statement = statement.where(SplitwiseExpense.date >= start)
    if end is not None:
        statement = statement.where(SplitwiseExpense.date <= end)

    paid_out = Decimal("0.00")
    received = Decimal("0.00")
    for expense in db.scalars(statement).all():
        try:
            raw = json.loads(expense.raw_json)
        except ValueError:
            continue
        if raw.get("payment") is not True:
            continue
        for user in raw.get("users", []):
            info = user.get("user") or {}
            first_name = info.get("first_name")
            if first_name != "Vayun":
                continue
            paid_share = Decimal(str(user.get("paid_share") or "0"))
            owed_share = Decimal(str(user.get("owed_share") or "0"))
            received += paid_share
            paid_out += owed_share
    return {"paid_out": float(paid_out), "received": float(received), "net": float(received - paid_out)}


def row_category(row: ReconciledSpend, db: Session) -> str:
    if row.source == "splitwise":
        return "Splitwise net"
    if row.source == "manual":
        return "Manual"
    if row.bank_transaction_id is None:
        return "Other"

    transaction = db.get(BankTransaction, row.bank_transaction_id)
    if transaction is None:
        return "Other"
    if transaction.category:
        return transaction.category.split(",")[0].title()
    try:
        raw = json.loads(transaction.raw_json)
    except ValueError:
        return "Other"
    personal_category = raw.get("personal_finance_category") or {}
    primary = str(personal_category.get("primary") or "")
    detailed = str(personal_category.get("detailed") or "")
    category = primary or detailed
    if not category:
        return "Other"
    category = category.replace("_", " ").title()
    return category.replace("And", "&")


def analytics_for_rows(rows: list, db: Session) -> dict:
    categories = defaultdict(Decimal)
    sources = defaultdict(Decimal)
    monthly = defaultdict(Decimal)
    for row in rows:
        amount = Decimal(row.adjusted_amount)
        if amount == 0:
            continue
        categories[row_category(row, db)] += amount
        sources[row.source] += amount
        monthly[row.date.strftime("%Y-%m")] += amount

    def sorted_items(values):
        return [
            {"label": label, "amount": float(amount)}
            for label, amount in sorted(values.items(), key=lambda item: abs(item[1]), reverse=True)
        ]

    return {
        "categories": sorted_items(categories),
        "sources": sorted_items(sources),
        "monthly": [
            {"month": month, "amount": float(amount)}
            for month, amount in sorted(monthly.items())
        ],
    }


@app.get("/api/dashboard")
def dashboard(
    preset: str = Query(default="all"),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=250),
    db: Session = Depends(get_db),
) -> dict:
    start, end = date_range_for_preset(preset, start, end)
    total_statement = apply_reconciled_filters(
        select(func.coalesce(func.sum(ReconciledSpend.adjusted_amount), 0)),
        start,
        end,
    )
    total = db.scalar(total_statement) or Decimal("0.00")
    bank_transactions = db.scalars(select(BankTransaction)).all()
    raw_spend_total = sum(
        (
            transaction.amount
            for transaction in bank_transactions
            if is_counted_bank_spend(transaction)
            and (start is None or transaction.date >= start)
            and (end is None or transaction.date <= end)
        ),
        Decimal("0.00"),
    )
    bank_net_statement = select(func.coalesce(func.sum(BankTransaction.amount), 0))
    if start is not None:
        bank_net_statement = bank_net_statement.where(BankTransaction.date >= start)
    if end is not None:
        bank_net_statement = bank_net_statement.where(BankTransaction.date <= end)
    bank_net_total = db.scalar(bank_net_statement) or Decimal("0.00")
    payment_summary = splitwise_payment_summary(db, start, end)
    bank_count_statement = select(func.count(BankTransaction.id))
    if start is not None:
        bank_count_statement = bank_count_statement.where(BankTransaction.date >= start)
    if end is not None:
        bank_count_statement = bank_count_statement.where(BankTransaction.date <= end)
    bank_count = db.scalar(bank_count_statement) or 0

    splitwise_count_statement = select(func.count(SplitwiseExpense.id))
    if start is not None:
        splitwise_count_statement = splitwise_count_statement.where(SplitwiseExpense.date >= start)
    if end is not None:
        splitwise_count_statement = splitwise_count_statement.where(SplitwiseExpense.date <= end)
    splitwise_count = db.scalar(splitwise_count_statement) or 0
    count_statement = apply_reconciled_filters(select(func.count(ReconciledSpend.id)), start, end)
    reconciled_count = db.scalar(count_statement) or 0
    offset = (page - 1) * limit
    row_statement = apply_reconciled_filters(
        select(ReconciledSpend).order_by(ReconciledSpend.date.desc(), ReconciledSpend.id.desc()),
        start,
        end,
    )
    filtered_rows = db.scalars(row_statement).all()
    rows = db.scalars(row_statement.offset(offset).limit(limit)).all()
    total_pages = max(1, (reconciled_count + limit - 1) // limit)
    analytics = analytics_for_rows(filtered_rows, db)
    return {
        "summary": {
            "real_spend": float(total),
            "raw_bank_spend": float(raw_spend_total),
            "bank_net": float(bank_net_total),
            "splitwise_payments_paid_out": payment_summary["paid_out"],
            "splitwise_payments_received": payment_summary["received"],
            "splitwise_payments_net": payment_summary["net"],
            "bank_transactions": bank_count,
            "splitwise_expenses": splitwise_count,
            "reconciled_rows": reconciled_count,
            "displayed_rows": len(rows),
            "display_limit": limit,
            "page": page,
            "total_pages": total_pages,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "preset": preset,
        },
        "transactions": [
            {
                "id": row.id,
                "source": row.source,
                "category": row_category(row, db),
                "date": row.date.isoformat(),
                "description": row.description,
                "original_amount": float(row.original_amount),
                "adjusted_amount": float(row.adjusted_amount),
                "note": row.note,
            }
            for row in rows
        ],
        "analytics": analytics,
    }
