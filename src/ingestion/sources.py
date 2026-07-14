"""
Multi-source ingestion for the institutional reporting pipeline.

Simulates the three data sources a real institutional reporting/compliance
function typically has to reconcile, each with its own shape and its own
data-quality issues injected on purpose so the validation stage has
something real to catch:

  1. Accounts / KYC register  (system-of-record style: one row per client account)
  2. Transactions ledger      (high-volume, append-only, references accounts)
  3. FX reference rates       (market data feed, used to normalize amounts to USD)

Each loader either reads an existing CSV under data/raw/ (so this can be
pointed at real exports) or generates a synthetic dataset deterministically
from a seed, so the full pipeline runs end-to-end with zero external
downloads.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

COUNTRIES = [
    "US", "GB", "DE", "FR", "SG", "JP", "CA", "AU", "BR", "IN",
    "KP", "IR", "SY", "MM", "AF",  # high-risk jurisdictions, intentionally included
]
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "SGD", "CAD", "AUD", "BRL", "INR"]
ACCOUNT_TYPES = ["RETAIL", "COMMERCIAL", "INSTITUTIONAL", "CORRESPONDENT"]
TXN_TYPES = ["WIRE", "ACH", "CARD", "CASH_EQUIVALENT", "SECURITIES_SETTLEMENT"]


def generate_accounts(n_accounts: int, seed: int) -> pd.DataFrame:
    """Generates the accounts / KYC register."""
    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)

    account_ids = [f"ACC-{100000 + i}" for i in range(n_accounts)]
    onboarding_dates = [
        datetime(2019, 1, 1) + timedelta(days=int(d))
        for d in rng.integers(0, 365 * 6, n_accounts)
    ]

    # Risk rating is not uniform -- most accounts are LOW/MEDIUM, a smaller
    # tail is HIGH, mirroring a realistic institutional risk distribution.
    risk_ratings = py_rng.choices(
        ["LOW", "MEDIUM", "HIGH"], weights=[0.6, 0.32, 0.08], k=n_accounts
    )

    df = pd.DataFrame(
        {
            "account_id": account_ids,
            "client_name": [f"Client {i:05d}" for i in range(n_accounts)],
            "account_type": py_rng.choices(ACCOUNT_TYPES, k=n_accounts),
            "jurisdiction": py_rng.choices(COUNTRIES, weights=[12] * 10 + [1] * 5, k=n_accounts),
            "risk_rating": risk_ratings,
            "onboarding_date": onboarding_dates,
            "kyc_status": py_rng.choices(
                ["VERIFIED", "PENDING_REVIEW", "EXPIRED"], weights=[0.9, 0.07, 0.03], k=n_accounts
            ),
        }
    )

    # Inject a small, realistic amount of missing data (e.g. legacy accounts
    # migrated without a full KYC record) for the validation stage to catch.
    missing_idx = py_rng.sample(range(n_accounts), k=max(1, int(0.01 * n_accounts)))
    df.loc[missing_idx, "kyc_status"] = None

    return df


def generate_transactions(accounts: pd.DataFrame, n_transactions: int, seed: int) -> pd.DataFrame:
    """Generates the transaction ledger, referencing the given accounts table."""
    rng = np.random.default_rng(seed + 1)
    py_rng = random.Random(seed + 1)

    account_ids = accounts["account_id"].tolist()
    start = datetime(2024, 1, 1)

    txn_account = py_rng.choices(account_ids, k=n_transactions)
    txn_days = rng.integers(0, 365, n_transactions)
    txn_timestamps = [start + timedelta(days=int(d), seconds=int(rng.integers(0, 86400))) for d in txn_days]

    # Log-normal amounts: mostly small/medium transactions, a long right
    # tail of large ones -- this is what makes threshold-based AML flags
    # meaningful rather than arbitrary.
    amounts = rng.lognormal(mean=7.5, sigma=1.6, size=n_transactions).round(2)

    df = pd.DataFrame(
        {
            "transaction_id": [f"TXN-{1_000_000 + i}" for i in range(n_transactions)],
            "account_id": txn_account,
            "timestamp": txn_timestamps,
            "amount": amounts,
            "currency": py_rng.choices(CURRENCIES, weights=[40, 15, 10, 5, 8, 6, 6, 5, 5], k=n_transactions),
            "transaction_type": py_rng.choices(TXN_TYPES, k=n_transactions),
            "counterparty_country": py_rng.choices(COUNTRIES, weights=[12] * 10 + [1] * 5, k=n_transactions),
        }
    )

    # Inject a handful of data-quality problems on purpose:
    #  - a few duplicate transaction IDs (should be caught by validation)
    #  - a few negative/zero amounts (invalid, should be caught)
    #  - a few orphan account references (referential integrity failure)
    dup_idx = py_rng.sample(range(n_transactions), k=5)
    for i in dup_idx[1:]:
        df.loc[i, "transaction_id"] = df.loc[dup_idx[0], "transaction_id"]

    bad_amount_idx = py_rng.sample(range(n_transactions), k=5)
    df.loc[bad_amount_idx, "amount"] = 0.0

    orphan_idx = py_rng.sample(range(n_transactions), k=3)
    df.loc[orphan_idx, "account_id"] = "ACC-999999"

    return df


def generate_fx_rates(seed: int) -> pd.DataFrame:
    """Generates a static FX reference table: 1 unit of currency -> USD."""
    py_rng = random.Random(seed + 2)
    base_rates = {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.27,
        "JPY": 0.0067,
        "SGD": 0.74,
        "CAD": 0.73,
        "AUD": 0.66,
        "BRL": 0.20,
        "INR": 0.012,
    }
    return pd.DataFrame(
        {
            "currency": list(base_rates.keys()),
            "usd_rate": list(base_rates.values()),
            "as_of_date": [datetime(2025, 1, 1).date()] * len(base_rates),
        }
    )


def load_or_generate(path: str, generator_fn, *args, **kwargs) -> pd.DataFrame:
    """Loads a CSV from data/raw/ if present, otherwise generates + saves it there.

    This is the seam that lets this pipeline be pointed at real exports:
    drop a real accounts.csv / transactions.csv / fx_rates.csv into
    data/raw/ and the pipeline uses it untouched; otherwise it produces a
    realistic synthetic dataset so the full pipeline is runnable with zero
    setup.
    """
    if os.path.exists(path):
        return pd.read_csv(path)

    df = generator_fn(*args, **kwargs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return df
