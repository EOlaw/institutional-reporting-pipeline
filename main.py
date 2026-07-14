"""
Institutional Reporting & Compliance Pipeline -- entry point.

    python main.py

    Runs the full pipeline: ingest three sources -> validate -> quarantine bad
    rows -> re-validate -> compile a reconciled fact table + account summary ->
    persist to a SQL warehouse -> export Tableau/Power BI-ready CSVs and an
    executive KPI summary. Prints a compliance summary to stdout at the end.
    """

from src.pipeline import run_pipeline


def main():
      result = run_pipeline()

    print("\n" + "=" * 70)
    print("COMPLIANCE SUMMARY")
    print("=" * 70)
    print(f"Accounts reported on:        {len(result['accounts'])}")
    print(f"Transactions compiled:       {len(result['fact'])}")
    print(f"Rows quarantined:            {sum(e['count'] for e in result['quarantine_log'])}")
    high_priority = result["account_summary"]
    high_priority = high_priority[high_priority["compliance_priority"] == "HIGH"]
    print(f"High-priority accounts:      {len(high_priority)}")
    print(f"Structuring-pattern flags:   {len(result['structuring_flags'])}")
    print("\nTop 5 accounts by volume:")
    print(
              result["account_summary"][
                  ["account_id", "client_name", "risk_rating", "total_volume_usd", "compliance_priority"]
      ].head(5).to_string(index=False)
    )
    print("\nReports written to outputs/reports/")


if __name__ == "__main__":
    main()
