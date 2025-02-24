# backend/services/reporting.py

from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.models.transaction import LedgerEntry
from backend.models.account import Account

def get_account_balance(db: Session, account_id: int) -> Decimal:
    """
    Compute the sum of all LedgerEntry.amount for a given account_id.
    Returns a Decimal or 0 if no entries exist.
    """
    total = db.query(func.sum(LedgerEntry.amount))\
              .filter(LedgerEntry.account_id == account_id)\
              .scalar()
    return total or Decimal('0.0')

def get_all_account_balances(db: Session):
    """
    For each Account, compute its ledger-based balance.
    Returns a list of dict objects with account_id, name, currency, balance.
    """
    accounts = db.query(Account).all()
    results = []
    for account in accounts:
        balance = db.query(func.sum(LedgerEntry.amount))\
                     .filter(LedgerEntry.account_id == account.id)\
                     .scalar() or Decimal('0.0')
        results.append({
            'account_id': account.id,
            'name': account.name,
            'currency': account.currency,
            'balance': balance
        })
    return results
