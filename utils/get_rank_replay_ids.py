"""Retired legacy workflow. See the explicit rlanalytics CLI and README.

Legacy formulas, mutable text-file pipelines, scraping, spreadsheet updates,
credential literals, pickle caches, and destructive cleanup are not executed.
Old function-level APIs have intentionally been removed.
"""

def main():
    import sys
    print(
        "This legacy workflow is retired. Use python -m rlanalytics --help "
        "for explicit import, report, and analysis commands. No action was taken.",
        file=sys.stderr,
    )
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
