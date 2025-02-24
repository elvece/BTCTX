"""
backend/routers/account.py

FastAPI router handling Account endpoints. In a double-entry system,
each Account may appear in many LedgerEntry lines, but the user typically
manages Accounts (create/update/delete) separately from transactions.

We have merged the original CRUD logic for Accounts with new endpoints
that return computed ledger-based balances. The actual summation logic
lives in the 'reporting' service, so we import and use it here.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from sqlalchemy.orm import Session

# Import schema classes for request/response validation
from backend.schemas.account import AccountCreate, AccountUpdate, AccountRead

# Import the account service with CRUD logic (create, update, delete, etc.)
from backend.services import account as account_service

# Import the reporting service for balance calculations
from backend.services.reporting import (
    get_account_balance,
    get_all_account_balances
)

# We use this to provide a DB session dependency
from backend.database import get_db

router = APIRouter(tags=["accounts"])


@router.get("/balances")
def read_all_balances(db: Session = Depends(get_db)):
    """
    Return a list of all accounts with their computed ledger-based balance.

    The 'get_all_account_balances' function in reporting.py performs:
      - Summation of all LedgerEntry amounts for each Account
      - Returns a list of { account_id, name, currency, balance (Decimal) }

    Here, we convert the Decimal to float for JSON serialization.
    Example response (list of dicts):
    [
      {
        "account_id": 1,
        "name": "Bank",
        "currency": "USD",
        "balance": 1000.00
      },
      ...
    ]

    Returns:
        List[dict]: A JSON-serializable list containing account details
                    and their respective ledger-based balances.
    """
    results = get_all_account_balances(db)
    # Convert Decimal to float for each account's balance
    for item in results:
        item["balance"] = float(item["balance"])
    return results


@router.get("/{account_id}/balance")
def read_account_balance(account_id: int, db: Session = Depends(get_db)):
    """
    Return a single account's computed ledger-based balance.

    Uses 'get_account_balance' to sum all LedgerEntries for the given
    account_id. If the account doesn't exist, we still can compute
    a balance of 0 if there are no ledger entries. However, you may
    want to check if the account_id is valid first.

    Example response:
    {
      "account_id": 2,
      "balance": 0.0023
    }

    Args:
        account_id (int): The numeric ID of the account.
        db (Session): DB session.

    Returns:
        dict: { "account_id": <int>, "balance": <float> }

    Raises:
        HTTPException 404: If you want to ensure the account actually exists.
    """
    # Optional: Verify the account actually exists. If not found => 404
    account = account_service.get_account_by_id(account_id, db)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    bal = get_account_balance(db, account_id)
    return {"account_id": account_id, "balance": float(bal)}


@router.get("/", response_model=List[AccountRead])
def list_accounts(db: Session = Depends(get_db)):
    """
    Retrieve all Accounts in the system.

    In a single-user scenario, you might filter by that user's ID,
    but here we show them all. The service function 'get_all_accounts'
    ensures the four special accounts exist if missing.

    Returns:
        List[AccountRead]: A list of all accounts with basic fields
                           (id, user_id, name, currency).
    """
    return account_service.get_all_accounts(db)


@router.get("/{account_id}", response_model=AccountRead)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """
    Retrieve a specific Account by its ID, or return 404 if not found.

    Args:
        account_id (int): The numeric ID of the Account to retrieve.
        db (Session): SQLAlchemy session provided by the get_db dependency.

    Returns:
        AccountRead: The matching account, including 'id, user_id, name, currency'.
    """
    account = account_service.get_account_by_id(account_id, db)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


@router.post("/", response_model=AccountRead)
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """
    Create a new Account.

    The request body (AccountCreate) includes:
      - user_id: the owner
      - name: e.g. "Bank", "BTC Fees", "Wallet"
      - currency: "USD" or "BTC"

    The service layer handles the actual DB insertion, plus any special
    constraints (e.g., preventing creation of locked special accounts).

    Returns:
        AccountRead: The newly created account record.
    """
    new_account = account_service.create_account(account, db)
    return new_account


@router.put("/{account_id}", response_model=AccountRead)
def update_account(account_id: int, account: AccountUpdate, db: Session = Depends(get_db)):
    """
    Update an existing Account's 'name' or 'currency' (both optional).

    Args:
        account_id (int): The numeric ID of the account to update.
        account (AccountUpdate): The new data. Some fields may be None,
                                 meaning no update.
        db (Session): DB session.

    Returns:
        AccountRead: The updated account data, if successful.

    Raises:
        404: If the account with the given ID does not exist.
    """
    updated_account = account_service.update_account(account_id, account, db)
    if not updated_account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return updated_account


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """
    Delete an existing Account by ID, returning 204 on success.

    If the account doesn't exist or cannot be deleted (e.g. locked special account),
    the service returns False, and we raise a 404.

    Returns:
        None: An HTTP 204 (no content) on successful deletion.

    Raises:
        404: If the account doesn't exist or is blocked from deletion.
    """
    success = account_service.delete_account(account_id, db)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Account not found or cannot be deleted."
        )
    return
