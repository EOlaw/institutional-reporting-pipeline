# Institutional Reporting & Compliance Pipeline

### A Python/SQL pipeline that reconciles multi-source institutional data into validated, audit-ready compliance reports and BI-dashboard exports.

Three systems that were never designed to talk to each other - a core
transaction ledger, a KYC/accounts register, and a market data feed - get
ingested, automatically validated, cleaned, reconciled, and compiled into a
single reporting model: per-account risk summaries, flagged transactions,
structuring-pattern detection, and Tableau/Power BI-ready exports, backed by
a queryable SQL warehouse.

## What's implemented

- **Multi-source ingestion** (`src/ingestion/sources.py`) - three independent sources (accounts/KYC, transactions, FX reference rates), each loadable from a real CSV or generated synthetically with realistic data-quality problems (duplicate IDs, orphan references, invalid amounts) baked in on purpose.
- **Automated validation** (`src/validation/validator.py`) - 10 checks (schema, uniqueness, referential integrity, range, domain, null-rate) split into CRITICAL (halts the pipeline) and WARNING (logged, non-blocking) severities, each returning a structured, human-readable result.
- **Quarantine + re-validation loop** - rows that fail CRITICAL checks are isolated and logged (not silently dropped), the cleaned data is re-validated, and the pipeline only proceeds once it passes with zero CRITICAL failures - a real automated preprocessing/validation gate, not just a one-shot check.
- **Multi-source compilation** (`src/transformation/compiler.py`) - joins transactions to account risk context and FX rates, normalizes every amount to USD, and computes an explainable (not black-box) compliance-priority score per account plus rolling-window structuring/smurfing pattern detection.
- **SQL warehouse** (`src/storage/warehouse.py`) - a star schema (`dim_accounts`, `fact_transactions`, `account_summary`, `structuring_flags`) persisted to SQLite by default, with plain ANSI SQL queries that port to Postgres by swapping one connection function.
- **BI export layer** (`src/reporting/exporters.py`) - Tableau/Power BI-ready flat CSVs (account summary, flagged transactions, jurisdiction exposure, structuring flags) plus an executive KPI JSON summary.
- **Test suite** (`tests/test_pipeline.py`) - 9 tests covering ingestion, validation (including a regression test that an earlier scoring version over-flagged 85% of accounts as high-priority), quarantine correctness, and compilation - **all 9 pass** (`pytest tests/ -v`, verified in this repository's history).
## Project layout

```
institutional-reporting-pipeline/
|-- main.py # Entry point
|-- config/settings.yaml # All tunable thresholds
|-- src/
| |-- config.py # Paths + settings loader
| |-- ingestion/sources.py # Multi-source loaders/generators
| |-- validation/validator.py # Checks + quarantine logic
| |-- transformation/compiler.py # Join, normalize, score, detect patterns
| |-- storage/warehouse.py # SQLite star schema + queries
| |-- reporting/exporters.py # Tableau/Power BI CSV + KPI JSON export
| `-- pipeline.py # Orchestrator
|-- data/
| |-- raw/ # Ingested sources (generated or real)
| `-- processed/ # warehouse.db
|-- outputs/reports/ # BI exports land here
|-- tests/test_pipeline.py
`-- requirements.txt
```

## How to run

```bash
pip install -r requirements.txt

# Run the full pipeline: ingest -> validate -> quarantine -> compile -> store -> export
python main.py

# Run the test suite
pytest tests/ -v
```

Sample output from an actual run (500 synthetic accounts, 20,000 synthetic transactions):

```
Accounts reported on: 500
Transactions compiled: 19988
Rows quarantined: 12
High-priority accounts: 54
Structuring-pattern flags: 74
```

Point it at real data by dropping `accounts.csv`, `transactions.csv`, and
`fx_rates.csv` into `data/raw/` with the columns documented in
`src/ingestion/sources.py` - the pipeline uses whatever it finds there
instead of generating synthetic data.

## Design notes

- **Validation halts, quarantine unblocks** - a CRITICAL failure doesn't mean "give up," it means "isolate the offending rows, log them for review, and proceed on what's actually clean." The pipeline only produces a report from data that re-passes every CRITICAL check after quarantine.
- **Explainable scoring, not a model** - `compliance_priority` is a transparent, auditable point score (risk rating + rate-based transaction flags + KYC status), not an ML classifier, because a compliance report needs to be defensible to a human reviewer, not just accurate.
- **Rate-based, not presence-based, flagging** - an earlier version flagged an account as high-priority if it had *any* large transaction; with enough transaction volume, nearly every account eventually has one, which made the flag meaningless. Flagging now requires a meaningful *share* of an account's own history to look risky (see the regression test in `tests/test_pipeline.py`).
- **SQLite by default, Postgres-ready by design** - every query in `warehouse.py` is plain SQL against a `sqlite3.Connection`; pointing this at Postgres means changing `get_connection()`, not rewriting queries.

## Scope

This models the reconciliation-and-reporting layer of institutional
compliance - not a full AML case-management system. Detection logic
(large-transaction thresholds, structuring-window heuristics) is
intentionally simple and parameterized in `config/settings.yaml` rather than
tuned against a real regulatory ruleset, since there is no substitute for
domain and jurisdiction-specific compliance review before anything like this
touches real data.
