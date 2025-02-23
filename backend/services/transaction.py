"""
backend/services/transaction.py

Implements the core multi-line double-entry and BTC FIFO logic:
 - Creates Transactions as "headers"
 - Builds LedgerEntry lines from single-entry fields if provided
 - Ensures a "BTC Fees" account is created for fees
 - Creates BitcoinLot on deposit/buy
 - Disposes from lots on withdrawal/sell
 - Rebuilds everything on transaction update, if not locked

Now includes:
 - _enforce_fee_rules to ensure Transfer fee currency, Buy/Sell fee currency, etc.
"""

from sqlalchemy.orm import Session
from datetime import datetime
from decimal import Decimal

from backend.models.transaction import (
    Transaction, LedgerEntry, BitcoinLot, LotDisposal
)
from backend.models.account import Account
from backend.schemas.transaction import TransactionCreate  # optional usage

from collections import defaultdict
from fastapi import HTTPException


# --------------------------------------------------------------------------------
# Public Functions
# --------------------------------------------------------------------------------

def get_all_transactions(db: Session):
    """
    Return all Transactions, typically ordered descending by timestamp.
    The user might also want a filter by date or type if the dataset is large.
    """
    return (
        db.query(Transaction)
        .order_by(Transaction.timestamp.desc())
        .all()
    )

def get_transaction_by_id(transaction_id: int, db: Session):
    """
    Retrieve a single Transaction by its ID.
    Returns None if not found.
    """
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()

def create_transaction_record(tx_data: dict, db: Session) -> Transaction:
    """
    Creates a new Transaction in the multi-line ledger system.

    Steps:
     1) Make sure a "BTC Fees" account exists if we want to track fee lines separately.
     2) Enforce fee rules for Transfer, Buy, Sell (e.g. Transfer from BTC => BTC fee).
     3) Create a Transaction row (header).
     4) Convert single-entry fields into LedgerEntries (double-entry lines).
     5) Verify ledger balance for purely internal transactions.
     6) If type=Deposit/Buy => create a BitcoinLot
     7) If type=Withdrawal/Sell => do FIFO partial disposal
     8) If Sell => compute realized gain from partial-lot lines

    Returns the newly created Transaction object after commit.
    """
    # Step 1: ensure a "BTC Fees" account
    ensure_fee_account_exists(db)

    # Step 2: enforce fee rules
    _enforce_fee_rules(tx_data, db)

    # Step 3: create the Transaction "header"
    new_tx = Transaction(
        from_account_id = tx_data.get("from_account_id"),
        to_account_id   = tx_data.get("to_account_id"),
        type            = tx_data.get("type"),
        amount          = tx_data.get("amount"),
        fee_amount      = tx_data.get("fee_amount"),
        fee_currency    = tx_data.get("fee_currency"),
        timestamp       = tx_data.get("timestamp", datetime.utcnow()),
        source          = tx_data.get("source"),
        purpose         = tx_data.get("purpose"),
        cost_basis_usd  = tx_data.get("cost_basis_usd"),
        proceeds_usd    = tx_data.get("proceeds_usd"),
        is_locked       = tx_data.get("is_locked", False),
        created_at      = datetime.utcnow(),
        updated_at      = datetime.utcnow()
    )
    db.add(new_tx)
    db.flush()  # get new_tx.id
    new_tx.group_id = new_tx.id

    # Step 4: build ledger entries
    remove_ledger_entries_for_tx(new_tx, db)
    build_ledger_entries_for_transaction(new_tx, tx_data, db)

    # Step 5: verify ledger balance (for internal transactions only)
    _verify_double_entry_balance_for_internal(new_tx, db)

    # Step 6: if deposit/buy => create a BTC lot
    if new_tx.type in ("Deposit", "Buy"):
        maybe_create_bitcoin_lot(new_tx, tx_data, db)

    # Step 7: if withdrawal/sell => do FIFO disposal
    if new_tx.type in ("Withdrawal", "Sell"):
        maybe_dispose_lots_fifo(new_tx, tx_data, db)

    # Step 8: if sell => compute realized gains
    if new_tx.type == "Sell":
        compute_sell_summary_from_disposals(new_tx, db)

    db.commit()
    db.refresh(new_tx)
    return new_tx

def update_transaction_record(transaction_id: int, tx_data: dict, db: Session):
    """
    Update an existing Transaction. If locked, we skip.
    Otherwise, we remove old ledger lines and lot usage, then rebuild them
    from the new data. This is simpler than partial/delta updates but loses 
    the old record. Fine for single-user until the transaction is locked.
    """
    tx = get_transaction_by_id(transaction_id, db)
    if not tx or tx.is_locked:
        return None

    if "from_account_id" in tx_data:
        tx.from_account_id = tx_data["from_account_id"]
    if "to_account_id" in tx_data:
        tx.to_account_id = tx_data["to_account_id"]
    if "amount" in tx_data:
        tx.amount = tx_data["amount"]
    if "fee_amount" in tx_data:
        tx.fee_amount = tx_data["fee_amount"]
    if "fee_currency" in tx_data:
        tx.fee_currency = tx_data["fee_currency"]
    if "type" in tx_data:
        tx.type = tx_data["type"]
    if "timestamp" in tx_data:
        tx.timestamp = tx_data["timestamp"]
    if "source" in tx_data:
        tx.source = tx_data["source"]
    if "purpose" in tx_data:
        tx.purpose = tx_data["purpose"]
    if "cost_basis_usd" in tx_data:
        tx.cost_basis_usd = tx_data["cost_basis_usd"]
    if "proceeds_usd" in tx_data:
        tx.proceeds_usd = tx_data["proceeds_usd"]

    tx.updated_at = datetime.utcnow()

    # rebuild
    remove_ledger_entries_for_tx(tx, db)
    remove_lot_usage_for_tx(tx, db)
    build_ledger_entries_for_transaction(tx, tx_data, db)

    if tx.type in ("Deposit", "Buy"):
        maybe_create_bitcoin_lot(tx, tx_data, db)
    if tx.type in ("Withdrawal", "Sell"):
        maybe_dispose_lots_fifo(tx, tx_data, db)
        if tx.type == "Sell":
            compute_sell_summary_from_disposals(tx, db)

    db.commit()
    db.refresh(tx)
    return tx

def delete_transaction_record(transaction_id: int, db: Session):
    """
    Delete a transaction if not locked. This cascades to
    LedgerEntries, BitcoinLots, and LotDisposals via the
    'cascade="all, delete-orphan"' relationships in the model.
    """
    tx = get_transaction_by_id(transaction_id, db)
    if not tx or tx.is_locked:
        return False

    db.delete(tx)
    db.commit()
    return True

# --------------------------------------------------------------------------------
# Internal Helpers
# --------------------------------------------------------------------------------

def ensure_fee_account_exists(db: Session):
    """
    Check if an Account named "BTC Fees" exists; if not, create it.
    For multi-user, you might do a user-specific fee account or 
    a system user_id=1. This is up to your application design.
    """
    fee_acct = db.query(Account).filter_by(name="BTC Fees").first()
    if not fee_acct:
        fee_acct = Account(
            user_id=1,  # or a system-level user
            name="BTC Fees",
            currency="BTC"
        )
        db.add(fee_acct)
        db.commit()
        db.refresh(fee_acct)
    return fee_acct

def remove_ledger_entries_for_tx(tx: Transaction, db: Session):
    """
    Delete all LedgerEntry lines for this Transaction so we can re-build them
    if the user changes critical fields like 'amount' or 'type'.
    """
    for entry in list(tx.ledger_entries):
        db.delete(entry)
    db.flush()

def remove_lot_usage_for_tx(tx: Transaction, db: Session):
    """
    Remove any BitcoinLots or partial disposals created by this Transaction,
    so we can re-create them if the user changes e.g. 'Deposit' -> 'Buy' or 
    changes 'amount' significantly. 
    """
    for disp in list(tx.lot_disposals):
        db.delete(disp)
    for lot in list(tx.bitcoin_lots_created):
        db.delete(lot)
    db.flush()

def build_ledger_entries_for_transaction(tx: Transaction, tx_data: dict, db: Session):
    """
    Convert single-entry style fields (from_account_id, amount, fee_amount) 
    into multiple LedgerEntry lines. Typically:

    1) from_acct => negative outflow
    2) to_acct => positive inflow
    3) fee_acct => separate line for fee if present
    """
    from_acct_id = tx_data.get("from_account_id")
    to_acct_id   = tx_data.get("to_account_id")
    amount       = Decimal(tx_data.get("amount", 0))
    fee_amount   = Decimal(tx_data.get("fee_amount", 0))
    fee_curr     = tx_data.get("fee_currency", "BTC")

    from_acct = db.query(Account).filter(Account.id == from_acct_id).first() if from_acct_id else None
    to_acct   = db.query(Account).filter(Account.id == to_acct_id).first()   if to_acct_id else None

    # main outflow (from_acct): negative (amount + fee_amount)
    if from_acct and amount > 0:
        main_out_amt = -(amount + fee_amount)
        db.add(LedgerEntry(
            transaction_id=tx.id,
            account_id=from_acct.id,
            amount=main_out_amt,
            currency=from_acct.currency,
            entry_type="MAIN_OUT"
        ))

    # main inflow (to_acct): positive amount
    if to_acct and amount > 0:
        db.add(LedgerEntry(
            transaction_id=tx.id,
            account_id=to_acct.id,
            amount=amount,
            currency=to_acct.currency,
            entry_type="MAIN_IN"
        ))

    # separate fee line -> "BTC Fees" if available
    if fee_amount > 0:
        fee_acct = db.query(Account).filter_by(name="BTC Fees").first()
        if fee_acct:
            db.add(LedgerEntry(
                transaction_id=tx.id,
                account_id=fee_acct.id,
                amount=fee_amount,
                currency=fee_curr,
                entry_type="FEE"
            ))

    db.flush()

def maybe_create_bitcoin_lot(tx: Transaction, tx_data: dict, db: Session):
    """
    If the transaction is a deposit/buy into a BTC account, create a new BitcoinLot.
    The 'amount' plus 'cost_basis_usd' (if provided) define total_btc and cost basis.
    """
    to_acct = db.query(Account).filter(Account.id == tx.to_account_id).first()
    if not to_acct or to_acct.currency != "BTC":
        return

    btc_amount = tx.amount or Decimal(0)
    if btc_amount <= 0:
        return

    cost_basis = Decimal(tx_data.get("cost_basis_usd", 0))

    new_lot = BitcoinLot(
        created_txn_id=tx.id,
        acquired_date=tx.timestamp,
        total_btc=btc_amount,
        remaining_btc=btc_amount,
        cost_basis_usd=cost_basis,
    )
    db.add(new_lot)
    db.flush()

def maybe_dispose_lots_fifo(tx: Transaction, tx_data: dict, db: Session):
    """
    For a 'Withdrawal' or 'Sell' from a BTC from_account, 
    do FIFO partial disposal. This creates LotDisposal rows
    each showing how many BTC are taken from each older lot, 
    plus partial basis/proceeds/gain if desired.
    """
    from_acct = db.query(Account).filter(Account.id == tx.from_account_id).first()
    if not from_acct or from_acct.currency != "BTC":
        return

    btc_outflow = float(tx.amount or 0)
    if btc_outflow <= 0:
        return

    total_proceeds = float(tx.proceeds_usd or 0)

    lots = db.query(BitcoinLot).filter(
        BitcoinLot.remaining_btc > 0
    ).order_by(BitcoinLot.acquired_date).all()

    remaining_outflow = btc_outflow
    total_outflow = btc_outflow

    for lot in lots:
        if remaining_outflow <= 0:
            break
        lot_rem = float(lot.remaining_btc)
        if lot_rem <= 0:
            continue

        can_use = min(lot_rem, remaining_outflow)
        lot_fraction = can_use / lot_rem

        disposal_basis = float(lot.cost_basis_usd) * lot_fraction

        partial_proceeds = 0.0
        if total_outflow > 0:
            partial_proceeds = (can_use / total_outflow) * total_proceeds

        disposal_gain = partial_proceeds - disposal_basis

        disp = LotDisposal(
            lot_id=lot.id,
            transaction_id=tx.id,
            disposed_btc=Decimal(can_use),
            disposal_basis_usd=Decimal(disposal_basis),
            proceeds_usd_for_that_portion=Decimal(partial_proceeds),
            realized_gain_usd=Decimal(disposal_gain)
        )
        db.add(disp)

        lot.remaining_btc = Decimal(lot_rem - can_use)
        remaining_outflow -= can_use

    # If there's leftover outflow, user doesn't have enough BTC.
    db.flush()

def compute_sell_summary_from_disposals(tx: Transaction, db: Session):
    """
    Summarize the partial-lot usage for a Sell transaction:
    - Sum disposal_basis_usd, realized_gain_usd => set tx.cost_basis_usd, tx.realized_gain_usd
    - Determine earliest acquired_date => classify holding_period as 'LONG'/'SHORT'
    """
    disposals = db.query(LotDisposal).filter(LotDisposal.transaction_id == tx.id).all()
    if not disposals:
        return

    total_basis = 0.0
    total_gain = 0.0
    earliest_date = None

    for disp in disposals:
        total_basis += float(disp.disposal_basis_usd or 0)
        total_gain  += float(disp.realized_gain_usd or 0)

        lot = db.query(BitcoinLot).get(disp.lot_id)
        if lot and (earliest_date is None or lot.acquired_date < earliest_date):
            earliest_date = lot.acquired_date

    tx.cost_basis_usd     = Decimal(total_basis)
    tx.realized_gain_usd  = Decimal(total_gain)

    if earliest_date:
        days_held = (tx.timestamp.date() - earliest_date.date()).days
        tx.holding_period = "LONG" if days_held > 365 else "SHORT"
    else:
        tx.holding_period = None

    db.flush()

def _verify_double_entry_balance_for_internal(tx: Transaction, db: Session):
    """
    If both from_account_id and to_account_id are internal (not 99),
    then the sum of ledger entries for this transaction (by currency)
    should net to zero.

    If EITHER side is account_id=99 (external), we skip the check.
    """
    if tx.from_account_id == 99 or tx.to_account_id == 99:
        return

    ledger_entries = db.query(LedgerEntry).filter(LedgerEntry.transaction_id == tx.id).all()
    sums_by_currency = defaultdict(Decimal)
    for entry in ledger_entries:
        if entry.account_id != 99:
            sums_by_currency[entry.currency] += entry.amount

    for currency, total in sums_by_currency.items():
        if total != Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail=f"Ledger not balanced for {currency}: {total}"
            )

def _enforce_fee_rules(tx_data: dict, db: Session):
    """
    Custom logic enforcing certain fee rules:
    - If type="Transfer" and from_acct is BTC => fee must be BTC, 
      else if from_acct is USD => fee must be USD
    - If type in ("Buy","Sell") => fee must be USD
    """
    tx_type = tx_data.get("type")
    from_id = tx_data.get("from_account_id")
    fee_amount = Decimal(tx_data.get("fee_amount", 0))
    fee_currency = tx_data.get("fee_currency", "USD")

    if fee_amount <= 0:
        return  # no fee, skip checks

    if tx_type == "Transfer":
        # Must see what currency the from_acct is
        if not from_id or from_id == 99:
            return  # external => no strict rule
        from_acct = db.query(Account).filter(Account.id == from_id).first()
        if not from_acct:
            return
        if from_acct.currency == "BTC" and fee_currency != "BTC":
            raise HTTPException(
                status_code=400,
                detail="Transfer from a BTC account => fee must be in BTC."
            )
        if from_acct.currency == "USD" and fee_currency != "USD":
            raise HTTPException(
                status_code=400,
                detail="Transfer from a USD account => fee must be in USD."
            )

    elif tx_type in ("Buy", "Sell"):
        if fee_currency != "USD":
            raise HTTPException(
                status_code=400,
                detail=f"{tx_type} => fee must be in USD."
            )
