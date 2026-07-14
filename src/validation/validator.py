"""
Automated data-quality validation for the institutional reporting pipeline.

Every check returns a ValidationResult so a single validate() call produces
a full report rather than stopping at the first failure. Checks are split
into two severities:

  - CRITICAL: the pipeline halts and refuses to compile a report. Examples:
    referential integrity failures (a transaction referencing an account
    that doesn't exist), duplicate primary keys, invalid monetary amounts.
    These would silently corrupt a compliance report if allowed through.

  - WARNING: logged and included in the validation report, but do not halt
    the pipeline. Examples: null-rate above threshold on a non-critical
    column, KYC status pending/expired. These need human review but
    shouldn't block an otherwise-valid reporting run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    passed: bool
    detail: str
    failing_count: int = 0


@dataclass
class ValidationReport:
    checks: list = field(default_factory=list)

    @property
    def critical_failures(self):
        return [c for c in self.checks if c.severity == Severity.CRITICAL and not c.passed]

    @property
    def warnings(self):
        return [c for c in self.checks if c.severity == Severity.WARNING and not c.passed]

    @property
    def is_valid(self) -> bool:
        """Pipeline may proceed iff there are no CRITICAL failures."""
        return len(self.critical_failures) == 0

    def summary(self) -> str:
        lines = [f"Validation report: {len(self.checks)} checks run"]
        for c in self.checks:
            status = "PASS" if c.passed else f"FAIL ({c.failing_count} rows)"
            lines.append(f"  [{c.severity.value:<8}] {c.name}: {status} -- {c.detail}")
        lines.append(
            f"Result: {'VALID -- pipeline may proceed' if self.is_valid else 'INVALID -- pipeline halted'}"
        )
        return "\n".join(lines)


class DataValidator:
    """Runs schema, referential-integrity, range, and null-rate checks."""

    def __init__(self, max_null_rate: float = 0.02, min_amount: float = 0.01, valid_risk_ratings=None):
        self.max_null_rate = max_null_rate
        self.min_amount = min_amount
        self.valid_risk_ratings = valid_risk_ratings or ["LOW", "MEDIUM", "HIGH"]

    def validate(self, accounts, transactions, fx_rates) -> ValidationReport:
        report = ValidationReport()

        report.checks.append(self._check_required_columns(
            accounts, ["account_id", "client_name", "risk_rating", "jurisdiction"], "accounts"
        ))
        report.checks.append(self._check_required_columns(
            transactions, ["transaction_id", "account_id", "amount", "currency", "timestamp"], "transactions"
        ))
        report.checks.append(self._check_required_columns(
            fx_rates, ["currency", "usd_rate"], "fx_rates"
        ))

        report.checks.append(self._check_unique(accounts, "account_id", "accounts.account_id uniqueness"))
        report.checks.append(self._check_unique(transactions, "transaction_id", "transactions.transaction_id uniqueness"))

        report.checks.append(self._check_referential_integrity(transactions, accounts))
        report.checks.append(self._check_referential_integrity_fx(transactions, fx_rates))

        report.checks.append(self._check_positive_amounts(transactions))

        report.checks.append(self._check_valid_set(
            accounts, "risk_rating", self.valid_risk_ratings, "accounts.risk_rating domain"
        ))

        report.checks.append(self._check_null_rate(accounts, "kyc_status", "accounts.kyc_status null rate"))

        return report

    # -- individual checks ----------------------------------------------

    def _check_required_columns(self, df, required_cols, label) -> CheckResult:
        missing = [c for c in required_cols if c not in df.columns]
        return CheckResult(
            name=f"required_columns[{label}]",
            severity=Severity.CRITICAL,
            passed=len(missing) == 0,
            detail=f"missing columns: {missing}" if missing else "all required columns present",
            failing_count=len(missing),
        )

    def _check_unique(self, df, col, name) -> CheckResult:
        dup_count = int(df[col].duplicated().sum())
        return CheckResult(
            name=name,
            severity=Severity.CRITICAL,
            passed=dup_count == 0,
            detail=f"{dup_count} duplicate values in '{col}'",
            failing_count=dup_count,
        )

    def _check_referential_integrity(self, transactions, accounts) -> CheckResult:
        valid_ids = set(accounts["account_id"])
        orphans = ~transactions["account_id"].isin(valid_ids)
        orphan_count = int(orphans.sum())
        return CheckResult(
            name="referential_integrity[transactions.account_id -> accounts.account_id]",
            severity=Severity.CRITICAL,
            passed=orphan_count == 0,
            detail=f"{orphan_count} transactions reference a nonexistent account_id",
            failing_count=orphan_count,
        )

    def _check_referential_integrity_fx(self, transactions, fx_rates) -> CheckResult:
        valid_currencies = set(fx_rates["currency"])
        missing = ~transactions["currency"].isin(valid_currencies)
        missing_count = int(missing.sum())
        return CheckResult(
            name="referential_integrity[transactions.currency -> fx_rates.currency]",
            severity=Severity.CRITICAL,
            passed=missing_count == 0,
            detail=f"{missing_count} transactions use a currency with no FX rate on file",
            failing_count=missing_count,
        )

    def _check_positive_amounts(self, transactions) -> CheckResult:
        invalid = transactions["amount"] < self.min_amount
        invalid_count = int(invalid.sum())
        return CheckResult(
            name="range_check[transactions.amount]",
            severity=Severity.CRITICAL,
            passed=invalid_count == 0,
            detail=f"{invalid_count} transactions have amount < {self.min_amount}",
            failing_count=invalid_count,
        )

    def _check_valid_set(self, df, col, allowed, name) -> CheckResult:
        invalid = ~df[col].isin(allowed)
        invalid_count = int(invalid.sum())
        return CheckResult(
            name=name,
            severity=Severity.CRITICAL,
            passed=invalid_count == 0,
            detail=f"{invalid_count} rows have a '{col}' value outside {allowed}",
            failing_count=invalid_count,
        )

    def _check_null_rate(self, df, col, name) -> CheckResult:
        null_rate = float(df[col].isna().mean())
        passed = null_rate <= self.max_null_rate
        return CheckResult(
            name=name,
            severity=Severity.WARNING,
            passed=passed,
            detail=f"null rate {null_rate:.2%} (threshold {self.max_null_rate:.2%})",
            failing_count=int(df[col].isna().sum()),
        )

    def quarantine(self, accounts, transactions, fx_rates):
        """Removes rows that fail CRITICAL checks into a quarantine set and
        returns (clean_accounts, clean_transactions, fx_rates, quarantine_log).

        This is the automated remediation step between "raw ingested data"
        and "data compiled into a compliance report": rather than a single
        bad batch blocking every account's reporting run, the specific
        offending rows are isolated, logged for manual review, and excluded
        from the compiled output. The pipeline only proceeds once the
        *cleaned* data passes validate() with zero CRITICAL failures.
        """
        quarantine_log = []
        txns = transactions.copy()

        valid_account_ids = set(accounts["account_id"])
        orphan_mask = ~txns["account_id"].isin(valid_account_ids)
        if orphan_mask.any():
            quarantine_log.append(
                {"reason": "orphan_account_reference", "count": int(orphan_mask.sum())}
            )
            txns = txns[~orphan_mask]

        valid_currencies = set(fx_rates["currency"])
        bad_ccy_mask = ~txns["currency"].isin(valid_currencies)
        if bad_ccy_mask.any():
            quarantine_log.append(
                {"reason": "unknown_currency", "count": int(bad_ccy_mask.sum())}
            )
            txns = txns[~bad_ccy_mask]

        bad_amount_mask = txns["amount"] < self.min_amount
        if bad_amount_mask.any():
            quarantine_log.append(
                {"reason": "non_positive_amount", "count": int(bad_amount_mask.sum())}
            )
            txns = txns[~bad_amount_mask]

        dup_mask = txns["transaction_id"].duplicated(keep="first")
        if dup_mask.any():
            quarantine_log.append(
                {"reason": "duplicate_transaction_id", "count": int(dup_mask.sum())}
            )
            txns = txns[~dup_mask]

        return accounts, txns.reset_index(drop=True), fx_rates, quarantine_log
