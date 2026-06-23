"""Verify the orchestrator's outputs after a run.

Walks outputs/orchestrator/, reads each site's tenders.json, and reports:
  - row count
  - whether rows match the schema declared in tender_sites.yaml (json_keys)
  - any rows with all-null values (likely broken scraper)
  - any rows with raw HTML whitespace in field values (a known gpt-4o-mini issue)

Usage (from project root):
    python verify_orchestrator.py
    python verify_orchestrator.py --strict   # exit 1 if any site looks broken
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
SITES_FILE = PROJECT_ROOT / "tender_sites.yaml"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "orchestrator"
HTML_WHITESPACE = re.compile(r"[\n\r\t]")


def _expected_keys(site_cfg: dict) -> list[str]:
    keys = site_cfg.get("json_keys") or []
    return [str(k) for k in keys]


def _row_looks_broken(row: dict) -> str | None:
    """Return a human-readable reason if this row is broken, else None."""
    if not any(row.values()):
        return "all values are null/empty"
    for key, value in row.items():
        if isinstance(value, str) and HTML_WHITESPACE.search(value):
            return f"raw HTML whitespace in {key!r}: {value[:50]!r}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify orchestrator output.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any site looks broken.")
    args = parser.parse_args()

    if not SITES_FILE.exists():
        print(f"missing {SITES_FILE}", file=sys.stderr)
        return 2
    if not OUTPUT_ROOT.exists():
        print(f"no orchestrator output yet at {OUTPUT_ROOT}", file=sys.stderr)
        return 2

    sites_cfg = yaml.safe_load(SITES_FILE.read_text(encoding="utf-8")) or {}
    by_id = {s["id"]: s for s in sites_cfg.get("sites", [])}

    n_total = 0
    n_warn = 0
    print(f"{'site_id':25s} {'rows':>5s} {'mode':10s}  status")
    print("-" * 75)
    for site_dir in sorted(OUTPUT_ROOT.iterdir()):
        if not site_dir.is_dir() or site_dir.name == "summary":
            continue
        site_id = site_dir.name
        site_cfg = by_id.get(site_id, {})
        if not site_cfg.get("enabled", True):
            print(f"{site_id:25s} {'--':>5s} {'disabled':10s}  SKIPPED (enabled: false)")
            continue
        mode = site_cfg.get("mode", "webwright")
        expected_keys = _expected_keys(site_cfg)
        # Find the newest tenders.json across all stamps
        tenders_files = sorted(site_dir.rglob("tenders.json"), reverse=True)
        if not tenders_files:
            print(f"{site_id:25s} {'-':>5s} {mode:10s}  MISSING tenders.json")
            n_warn += 1
            continue
        tenders_path = tenders_files[0]
        try:
            rows = json.loads(tenders_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"{site_id:25s} {'-':>5s} {mode:10s}  BAD JSON: {exc}")
            n_warn += 1
            continue
        n_total += len(rows)
        # Per-row checks
        broken = [(i, reason) for i, row in enumerate(rows) if (reason := _row_looks_broken(row))]
        # Schema check
        if rows and expected_keys:
            actual_keys = set(rows[0].keys())
            missing = set(expected_keys) - actual_keys
            extra = actual_keys - set(expected_keys)
            schema_note = ""
            if missing:
                schema_note += f" missing={sorted(missing)}"
            if extra:
                schema_note += f" extra={sorted(extra)}"
        else:
            schema_note = ""
        status = "OK"
        if broken:
            status = f"BROKEN: {len(broken)} bad rows (first: {broken[0][1]})"
            n_warn += 1
        elif schema_note:
            status = f"schema drift{schema_note}"
            n_warn += 1
        elif not rows:
            status = "EMPTY (scraper returned 0 rows)"
            n_warn += 1
        print(f"{site_id:25s} {len(rows):>5d} {mode:10s}  {status}{schema_note}")
    print("-" * 75)
    print(f"total rows across {len(by_id)} sites: {n_total}; warnings: {n_warn}")
    if args.strict and n_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())