"""
SQL warehouse layer for the institutional reporting pipeline.

Persists the compiled reporting model into a small star schema:

    dim_accounts        -- one row per account
    fact_transactions    -- one row per (cleaned) transaction, USD-normalized
    account_summary      -- pre-aggregated per-account rollup (fast dashboard reads)

Uses SQLite by default so the pipeline runs with zero setup; the schema and
all queries are plain ANSI-ish SQL so swapping the connection for Postgres
(psycopg2 / SQLAlchemy) is a matter of changing `get_connection()`, not
rewriting any query.
"""

from __future__ import annotations

import sqlite3

import pandas as pd


class ReportingWarehouse:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def write_tables(
        self,
        accounts: pd.DataFrame,
        fact_transactions: pd.DataFrame,
        account_summary: pd.DataFrame,
        structuring_flags: pd.DataFrame,
    ):
        with self.get_connection() as conn:
            accounts.to_sql("dim_accounts", conn, if_exists="replace", index=False)
            fact_transactions.to_sql("fact_transactions", conn, if_exists="replace", index=False)
            account_summary.to_sql("account_summary", conn, if_exists="replace", index=False)
            structuring_flags.to_sql("structuring_flags", conn, if_exists="replace", index=False)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_account ON fact_transactions(account_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_timestamp ON fact_transactions(timestamp)")
            conn.commit()

    def query(self, sql: str) -> pd.DataFrame:
        with self.get_connection() as conn:
            return pd.read_sql_query(sql, conn)

    def high_risk_exposure_by_jurisdiction(self) -> pd.DataFrame:
        """Example analytical query a compliance dashboard would run directly
        against the warehouse rather than recomputing in Python."""
        sql = """
            SELECT
                jurisdiction,
                COUNT(DISTINCT account_id)            AS account_count,
                SUM(amount_usd)                        AS total_volume_usd,
                SUM(CASE WHEN is_large_transaction THEN 1 ELSE 0 END) AS large_transaction_count
            FROM fact_transactions
            GROUP BY jurisdiction
            ORDER BY total_volume_usd DESC
        """
        return self.query(sql)
