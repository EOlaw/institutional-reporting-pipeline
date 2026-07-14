"""
Multi-source data compilation for the institutional reporting pipeline.

Takes the three validated, cleaned sources (accounts, transactions, fx_rates)
and compiles them into the reporting model:

  - a normalized fact table (every transaction, amount converted to USD)
  - per-account aggregates (volume, transaction count, risk exposure)
  - compliance flags (large transactions, high-risk-jurisdiction exposure,
    potential structuring patterns)

This is the "reconciliation" step that a Python/SQL institutional reporting
pipeline exists to do: three systems that were never designed to talk to
each other (a core banking ledger, a KYC system, a market data feed) get
compiled into one coherent, analysis-ready dataset.
"""

from __future__ import annotations

import pandas as pd


class ReportCompiler:
    def __init__(
        self,
        large_transaction_usd_threshold: float = 10000,
        high_risk_jurisdictions: list = None,
        structuring_window_days: int = 1,
        structuring_txn_count_threshold: int = 3,
    ):
        self.large_transaction_usd_threshold = large_transaction_usd_threshold
        self.high_risk_jurisdictions = set(high_risk_jurisdictions or [])
        self.structuring_window_days = structuring_window_days
        self.structuring_txn_count_threshold = structuring_txn_count_threshold

    def compile_fact_table(self, transactions: pd.DataFrame, accounts: pd.DataFrame, fx_rates: pd.DataFrame) -> pd.DataFrame:
        """Joins transactions -> accounts (risk context) -> fx_rates (USD normalization)."""
        txns = transactions.copy()
        txns["timestamp"] = pd.to_datetime(txns["timestamp"])

        fact = txns.merge(
            accounts[["account_id", "client_name", "account_type", "jurisdiction", "risk_rating", "kyc_status"]],
            on="account_id",
            how="left",
            suffixes=("", "_account"),
        )
        fact = fact.merge(fx_rates[["currency", "usd_rate"]], on="currency", how="left")
        fact["amount_usd"] = (fact["amount"] * fact["usd_rate"]).round(2)

        fact["is_large_transaction"] = fact["amount_usd"] >= self.large_transaction_usd_threshold
        fact["is_high_risk_counterparty"] = fact["counterparty_country"].isin(self.high_risk_jurisdictions)
        fact["is_high_risk_jurisdiction_account"] = fact["jurisdiction"].isin(self.high_risk_jurisdictions)

        return fact

    def compile_account_summary(self, fact: pd.DataFrame) -> pd.DataFrame:
        """Per-account rollup: exactly what a compliance analyst reviews per client."""
        grouped = fact.groupby("account_id").agg(
            client_name=("client_name", "first"),
            account_type=("account_type", "first"),
            jurisdiction=("jurisdiction", "first"),
            risk_rating=("risk_rating", "first"),
            kyc_status=("kyc_status", "first"),
            transaction_count=("transaction_id", "count"),
            total_volume_usd=("amount_usd", "sum"),
            avg_transaction_usd=("amount_usd", "mean"),
            max_transaction_usd=("amount_usd", "max"),
            large_transaction_count=("is_large_transaction", "sum"),
            high_risk_counterparty_count=("is_high_risk_counterparty", "sum"),
        ).reset_index()

        grouped["total_volume_usd"] = grouped["total_volume_usd"].round(2)
        grouped["avg_transaction_usd"] = grouped["avg_transaction_usd"].round(2)

        grouped["compliance_priority"] = self._compliance_priority(grouped)
        return grouped.sort_values("total_volume_usd", ascending=False).reset_index(drop=True)

    def _compliance_priority(self, account_summary: pd.DataFrame) -> pd.Series:
        """Simple, explainable priority score -- not a black-box model, since
        this output is meant to be defensible to an auditor.

        Deliberately rate-based rather than presence-based: with enough
        transaction volume, almost every account will have *at least one*
        large transaction or high-risk counterparty by chance, which would
        make a presence-only score flag nearly everyone and defeat the
        point of triage. Requiring a meaningful share of an account's own
        transaction history to be flagged keeps the HIGH bucket a genuine
        minority worth an analyst's attention.
        """
        large_txn_rate = account_summary["large_transaction_count"] / account_summary["transaction_count"].clip(lower=1)
        high_risk_rate = account_summary["high_risk_counterparty_count"] / account_summary["transaction_count"].clip(lower=1)

        score = (
            (account_summary["risk_rating"] == "HIGH").astype(int) * 3
            + (large_txn_rate >= 0.15).astype(int) * 2
            + (high_risk_rate >= 0.15).astype(int) * 2
            + (account_summary["kyc_status"] != "VERIFIED").astype(int) * 1
        )
        return pd.cut(
            score,
            bins=[-1, 0, 2, 100],
            labels=["LOW", "MEDIUM", "HIGH"],
        )

    def detect_structuring_patterns(self, fact: pd.DataFrame) -> pd.DataFrame:
        """Flags accounts with several sub-threshold transactions clustered in
        a short window -- a classic structuring / smurfing pattern where
        individual transactions duck under a reporting threshold but the
        aggregate does not.
        """
        window = f"{self.structuring_window_days}D"
        candidates = fact[fact["amount_usd"] < self.large_transaction_usd_threshold].copy()
        candidates = candidates.sort_values("timestamp")

        flags = []
        for account_id, group in candidates.groupby("account_id"):
            group = group.set_index("timestamp")
            rolling_count = group["amount_usd"].rolling(window).count()
            rolling_sum = group["amount_usd"].rolling(window).sum()
            hits = rolling_count[rolling_count >= self.structuring_txn_count_threshold]
            for ts in hits.index:
                flags.append(
                    {
                        "account_id": account_id,
                        "window_end": ts,
                        "transactions_in_window": int(rolling_count.loc[ts]),
                        "total_amount_usd": round(float(rolling_sum.loc[ts]), 2),
                    }
                )

        if not flags:
            return pd.DataFrame(
                columns=["account_id", "window_end", "transactions_in_window", "total_amount_usd"]
            )

        return pd.DataFrame(flags).drop_duplicates(subset="account_id").reset_index(drop=True)
