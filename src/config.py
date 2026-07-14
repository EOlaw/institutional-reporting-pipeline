"""Central configuration: paths + tunable thresholds, all overridable via config/settings.yaml."""

import os
import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
PROCESSED_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")
WAREHOUSE_PATH = os.path.join(PROCESSED_DATA_DIR, "warehouse.db")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "settings.yaml")

DEFAULT_SETTINGS = {
    "ingestion": {
        "n_accounts": 500,
        "n_transactions": 20000,
        "seed": 42,
    },
    "validation": {
        "max_null_rate": 0.02,
        "min_amount": 0.01,
        "valid_risk_ratings": ["LOW", "MEDIUM", "HIGH"],
    },
    "compliance": {
        "large_transaction_usd_threshold": 10000,
        "high_risk_jurisdictions": ["KP", "IR", "SY", "MM", "AF"],
        "structuring_window_days": 1,
        "structuring_txn_count_threshold": 3,
    },
}


def load_settings() -> dict:
    """Loads config/settings.yaml, falling back to DEFAULT_SETTINGS for any missing keys."""
    settings = {k: dict(v) for k, v in DEFAULT_SETTINGS.items()}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            user_settings = yaml.safe_load(f) or {}
        for section, values in user_settings.items():
            settings.setdefault(section, {})
            settings[section].update(values or {})
    return settings


def ensure_dirs():
    for d in [RAW_DATA_DIR, PROCESSED_DATA_DIR, REPORTS_DIR]:
        os.makedirs(d, exist_ok=True)
