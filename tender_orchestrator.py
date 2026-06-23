"""Tender orchestrator for Webwright.

Defines sites in tender_sites.yaml, runs each site, extracts structured tender
data, and writes a per-site tenders.json plus a run-summary.json.

Three execution modes (mix freely per site):
  http     : free, ~2s. Pure httpx + selectolax. Static HTML listings only.
  cached   : ~$0.001. Replays a previously-generated final_script.py. No LLM.
  webwright: full agent run. ~$0.10. Use only for sites that change structure
             often or that need browser interaction.

Usage (from project root):
    python tender_orchestrator.py                       # run every enabled site once
    python tender_orchestrator.py --only bnm_procurement
    python tender_orchestrator.py --sites tender_sites.yaml --output-root outputs/orchestrator
    python tender_orchestrator.py --loop --interval-hours 24   # cron-like loop
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SITES_FILE = PROJECT_ROOT / "tender_sites.yaml"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "orchestrator"
HISTORY_FILENAME = "history.jsonl"

SITE_DISPLAY_NAMES: dict[str, str] = {
    "bnm_procurement":      "Bank Negara Malaysia",
    "hasil_senarai_tender": "LHDN",
    "dbkl_eperolehan":      "DBKL",
    "mpaj_tender":          "MPAJ",
    "mbsj_tender":          "MBSJ",
    "kkr_senarai_tender":   "KKR",
    "mbi_perolehan":        "MBI",
    "bursa_tenders":        "BURSA",
    "kwsp_perolehan":       "KWSP",
    "mof_tender":           "MOF",
}


@dataclass
class Site:
    id: str
    url: str
    config_specs: list[str]
    json_keys: list[str]
    task: str
    enabled: bool = True
    mode: str = "webwright"          # "http" | "cached" | "webwright"
    http_selectors: dict[str, str] | None = None  # json_key -> CSS selector
    http_row_selector: str | None = None          # CSS selector for one row
    http_max_rows: int = 200
    # Optional: pick the <table> whose text contains this substring before
    # applying http_row_selector. Disambiguates pages with several tables.
    http_table_contains: str | None = None
    # Optional: drop this many leading rows (e.g. a header row that isn't in
    # a <thead>) after selecting rows.
    http_skip_header_rows: int = 0
    # Pagination for http mode: set http_page_param to the URL query param name
    # (e.g. "page") and http_max_pages to the max pages to try.  Pages start at
    # http_page_start (0 for KKR, 1 for most sites).
    http_page_param: str | None = None
    http_max_pages: int = 1
    http_page_start: int = 0
    cached_script: str | None = None  # path (relative to project root) of final_script.py to replay

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Site":
        return cls(
            id=str(raw["id"]).strip(),
            url=str(raw["url"]).strip(),
            config_specs=[str(c) for c in raw.get("config_specs", ["base.yaml"])],
            json_keys=[str(k) for k in raw.get("json_keys", ["title"])],
            task=str(raw.get("task", "")).strip(),
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode", "webwright")).strip().lower(),
            http_selectors=raw.get("http_selectors"),
            http_row_selector=raw.get("http_row_selector"),
            http_max_rows=int(raw.get("http_max_rows", 200)),
            http_table_contains=raw.get("http_table_contains"),
            http_skip_header_rows=int(raw.get("http_skip_header_rows", 0)),
            http_page_param=raw.get("http_page_param"),
            http_max_pages=int(raw.get("http_max_pages", 1)),
            http_page_start=int(raw.get("http_page_start", 0)),
            cached_script=raw.get("cached_script"),
        )


def load_sites(path: Path) -> list[Site]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Site.from_dict(item) for item in data.get("sites", [])]


def load_env_file(path: Path) -> None:
    """Tiny .env loader so OPENAI_API_KEY / OPENROUTER_API_KEY / etc. exist."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def run_webwright_for_site(site: Site, run_dir: Path) -> dict[str, Any]:
    """Invoke the webwright CLI for a single site. Returns a result dict."""
    cmd = [
        sys.executable,
        "-m",
        "webwright.run.cli",
        "main",
        "-t",
        site.task,
        "--task-id",
        site.id,
        "-o",
        str(run_dir),
    ]
    for spec in site.config_specs:
        cmd[2:2] = ["-c", spec]  # splice in -c <spec> right after "main"
    # The splice above puts -c after 'main'; tidy so -c flags precede -t.
    cmd = [
        sys.executable,
        "-m",
        "webwright.run.cli",
        "main",
    ]
    for spec in site.config_specs:
        cmd += ["-c", spec]
    cmd += ["-t", site.task, "--task-id", site.id, "-o", str(run_dir)]

    print(f"[{site.id}] running: {' '.join(cmd[:8])} ... [{site.task[:60]}...]")
    started = datetime.now(timezone.utc)
    completed = None
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        ok = completed.returncode == 0
        err_tail = completed.stderr[-400:] if completed.stderr else ""
    except subprocess.TimeoutExpired:
        ok = False
        err_tail = "TIMEOUT after 600s"
    finished = datetime.now(timezone.utc)
    return {
        "site_id": site.id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "ok": ok,
        "exit_code": None if completed is None else completed.returncode,
        "error_tail": err_tail,
        "run_dir": str(run_dir),
    }


def fetch_via_http(site: Site, run_dir: Path) -> dict[str, Any]:
    """Tier 1: scrape the listing page with httpx + selectolax. Zero LLM cost."""
    import httpx
    started = datetime.now(timezone.utc)
    try:
        # Lazy import so this stays optional
        from selectolax.parser import HTMLParser
    except ImportError:
        return {
            "site_id": site.id,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "exit_code": None,
            "error_tail": "selectolax not installed. Run: pip install selectolax",
            "run_dir": str(run_dir),
            "mode": "http",
        }
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "page.html"
    tenders_path = run_dir / "tenders.json"

    def _clean(text: str | None) -> str | None:
        if text is None:
            return None
        text = " ".join(text.split())
        text = re.sub(r"\s*\[Edit\]\s*$", "", text, flags=re.IGNORECASE).strip()
        return text or None

    def _parse_page(html: str) -> list[dict[str, Any]]:
        tree = HTMLParser(html)
        if not site.http_row_selector or not site.http_selectors:
            raise ValueError(
                "http mode requires both http_row_selector and http_selectors "
                "in tender_sites.yaml for this site"
            )
        scope = tree
        if site.http_table_contains:
            needle = site.http_table_contains.lower()
            chosen = next(
                (t for t in tree.css("table") if needle in (t.text() or "").lower()),
                None,
            )
            if chosen is None:
                return []   # table not found on this page → stop pagination
            scope = chosen
        rows = scope.css(site.http_row_selector)
        if site.http_skip_header_rows:
            rows = rows[site.http_skip_header_rows:]
        rows = rows[: site.http_max_rows]
        page_tenders = []
        for row in rows:
            record: dict[str, Any] = {}
            for key, selector in site.http_selectors.items():
                node = row.css_first(selector)
                record[key] = _clean(node.text(strip=True)) if node else None
            if any(record.values()):
                page_tenders.append(record)
        return page_tenders

    try:
        req_headers = {"User-Agent": "Mozilla/5.0 (TenderOrchestrator/1.0)"}
        tenders: list[dict[str, Any]] = []
        base_url = site.url.split("?")[0] if site.http_page_param else site.url

        for page_num in range(site.http_page_start, site.http_page_start + site.http_max_pages):
            fetch_url = (
                f"{base_url}?{site.http_page_param}={page_num}"
                if site.http_page_param
                else site.url
            )
            resp = httpx.get(fetch_url, headers=req_headers, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            if page_num == site.http_page_start:
                raw_path.write_text(resp.text, encoding="utf-8")
            page_rows = _parse_page(resp.text)
            if not page_rows:
                break   # no more data — stop pagination
            tenders.extend(page_rows)
            if not site.http_page_param:
                break   # single-page site

        tenders_path.write_text(
            json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        ok = True
        err_tail = ""
    except Exception as exc:
        ok = False
        err_tail = f"{type(exc).__name__}: {exc}"[-400:]
        tenders_path.write_text("[]", encoding="utf-8")
    finished = datetime.now(timezone.utc)
    return {
        "site_id": site.id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "error_tail": err_tail,
        "run_dir": str(run_dir),
        "mode": "http",
        "tenders_path": str(tenders_path),
        "tender_count": 0 if not ok else len(json.loads(tenders_path.read_text(encoding="utf-8"))),
    }


def replay_cached_script(site: Site, run_dir: Path) -> dict[str, Any]:
    """Tier 2: run a previously-generated final_script.py directly. No LLM."""
    if not site.cached_script:
        return {
            "site_id": site.id,
            "ok": False,
            "error_tail": "cached mode requires cached_script path in tender_sites.yaml",
            "mode": "cached",
            "run_dir": str(run_dir),
        }
    script_path = PROJECT_ROOT / site.cached_script
    if not script_path.exists():
        return {
            "site_id": site.id,
            "ok": False,
            "error_tail": f"cached_script not found: {script_path}",
            "mode": "cached",
            "run_dir": str(run_dir),
        }
    run_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path), str(run_dir)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        ok = completed.returncode == 0
        err_tail = completed.stderr[-400:] if completed.stderr else ""
    except subprocess.TimeoutExpired:
        ok = False
        err_tail = "TIMEOUT after 300s"
    finished = datetime.now(timezone.utc)
    # final_script.py usually writes its own output; try to surface a tenders.json
    found = next(run_dir.rglob("tenders.json"), None)
    tenders_path = found or run_dir / "tenders.json"
    if not found:
        tenders_path.write_text("[]", encoding="utf-8")
    return {
        "site_id": site.id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "ok": ok,
        "exit_code": getattr(completed, "returncode", None),
        "error_tail": err_tail,
        "run_dir": str(run_dir),
        "mode": "cached",
        "tenders_path": str(tenders_path),
        "tender_count": 0 if not found else len(json.loads(tenders_path.read_text(encoding="utf-8"))),
    }


def write_history_entry(history_path: Path, entry: dict[str, Any]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_tenders(trajectory_path: Path) -> list[dict[str, Any]]:
    """Parse trajectory.json and return the structured tender list.

    Walks the message log in reverse and looks for an assistant message whose
    final_response (or content) parses as a JSON array of tender objects.
    Strips ```json ... ``` fences if present.
    """
    if not trajectory_path.exists():
        return []
    try:
        data = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    for msg in reversed(data.get("messages", [])):
        if msg.get("role") != "assistant":
            continue
        extra = msg.get("extra", {}) or {}
        candidate = extra.get("final_response") or msg.get("content", "")
        if isinstance(candidate, list):
            candidate = "".join(
                part.get("text", "") for part in candidate if isinstance(part, dict)
            )
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:]
            candidate = candidate.strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
        if isinstance(parsed, dict):
            for key in ("tenders", "results", "data", "items"):
                if key in parsed and isinstance(parsed[key], list):
                    return [row for row in parsed[key] if isinstance(row, dict)]
            return [parsed]
    return []


def _fingerprint(row: dict[str, Any]) -> str:
    """Stable per-tender identity used for dedup.

    Whitelist the fields that uniquely identify a tender across runs and ignore
    formatting noise (extra spaces, attribute reordering). Falls back to a
    canonical JSON dump if no whitelisted field is present.
    """
    candidates = (
        row.get("title"),
        row.get("reference_no"),
        row.get("tender_no"),
        row.get("tender_id"),
        row.get("notice_no"),
    )
    parts = [str(c).strip() for c in candidates if c]
    if not parts:
        # Last resort: dump the whole row in canonical form
        return json.dumps(row, sort_keys=True, ensure_ascii=False)
    return "|".join(parts)


def diff_against_history(rows: list[dict[str, Any]], history_path: Path) -> dict[str, Any]:
    """Return new/updated/removed counts vs the most recent history entry,
    plus a running tally of all-time-seen tenders (including this run).

    'Previous run' is the literal last line of history, even if it was empty.
    That makes 'removed' intuitive for cron: it's what was here last time and
    isn't here now -- not what disappeared at some unspecified point in the
    past.
    """
    cur_keys = {_fingerprint(r) for r in rows}
    all_time_seen: set[str] = set()
    last_entry: dict[str, Any] | None = None
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            for r in entry.get("rows", []) or []:
                all_time_seen.add(_fingerprint(r))
        # The literal last entry, regardless of whether it had rows
        for line in reversed(history_path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "rows" in entry:
                last_entry = entry
                break
    all_time_with_this = all_time_seen | cur_keys
    last_rows = (last_entry or {}).get("rows", []) or []
    last_keys = {_fingerprint(r) for r in last_rows}
    new_since_last = cur_keys - last_keys
    removed_since_last = last_keys - cur_keys
    brand_new = cur_keys - all_time_seen
    return {
        "baseline_runs": 0 if last_entry is None else 1,
        "baseline_rows": len(last_rows),
        "current_rows": len(rows),
        "new_count": len(new_since_last),
        "removed_count": len(removed_since_last),
        "unchanged_count": len(cur_keys & last_keys),
        "brand_new_count": len(brand_new),
        "all_time_total": len(all_time_with_this),
        "first_run": last_entry is None,
    }


_MALAY_MONTHS = {
    "jan": 1, "feb": 2, "mac": 3, "mar": 3, "apr": 4,
    "mei": 5, "may": 5, "jun": 6, "jul": 7,
    "ogos": 8, "ogs": 8, "aug": 8,
    "sep": 9, "okt": 10, "oct": 10,
    "nov": 11, "dis": 12, "dec": 12,
}

_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ISO: 2026-07-30
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
    # DD/MM/YYYY: 22/07/2026
    (re.compile(r"(\d{1,2})/(\d{2})/(\d{4})"), "dmy_slash"),
    # DD.MM.YYYY: 08.06.2026  (KWSP style)
    (re.compile(r"(\d{1,2})\.(\d{2})\.(\d{4})"), "dmy_dot"),
    # DD Mon YYYY or DD MONTH YYYY (English or Malay): 26 Jun 2026, 31 Dis 2026
    (re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"), "dmy_word"),
]


def _parse_closing_date(value: str | None) -> date | None:
    """Return a date from a closing_date string, or None if unparseable."""
    if not value:
        return None
    v = value.strip()
    # BNM appends " [Closed]" — treat explicitly as past
    if "[Closed]" in v or "[closed]" in v.lower():
        return date(2000, 1, 1)
    # Strip trailing annotations like " (Rabu)", " (KHAMIS)", time part
    v = re.sub(r"\s*\([^)]*\)", "", v)          # remove (day-name)
    v = re.sub(r"\s*-\s*\d{2}:\d{2}.*$", "", v) # remove "- HH:MM"
    v = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", v)   # remove " HH:MM..."
    v = v.strip()
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(v)
        if not m:
            continue
        try:
            if kind == "iso":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if kind in ("dmy_slash", "dmy_dot"):
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if kind == "dmy_word":
                month_str = m.group(2).lower()[:4]
                month = _MALAY_MONTHS.get(month_str) or _MALAY_MONTHS.get(month_str[:3])
                if month:
                    return date(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            continue
    return None


def _normalize_date_str(value: str | None) -> str | None:
    """Convert any recognisable date string to DD/MM/YYYY, else return as-is."""
    if not value:
        return value
    parsed = _parse_closing_date(value)
    if parsed:
        return parsed.strftime("%d/%m/%Y")
    return value


def send_email_report(
    tenders: list[dict[str, Any]],
    combined_path: Path,
    run_stamp: str,
) -> None:
    """Send the combined tender list by email if SMTP env vars are set."""
    sender   = os.environ.get("EMAIL_FROM", "").strip()
    to_raw   = os.environ.get("EMAIL_TO", "").strip()
    password = os.environ.get("EMAIL_APP_PASS", "").strip()

    if not (sender and to_raw and password):
        return

    recipients = [e.strip() for e in to_raw.split(",") if e.strip()]
    smtp_host  = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
    today_str  = datetime.now().strftime("%d/%m/%Y")

    # ---- count tenders per display name, show ALL 10 sites ----
    site_counts: dict[str, int] = {name: 0 for name in SITE_DISPLAY_NAMES.values()}
    for r in tenders:
        src = r.get("source") or SITE_DISPLAY_NAMES.get(r.get("site_id", ""), "?")
        site_counts[src] = site_counts.get(src, 0) + 1

    active_count = sum(1 for n in site_counts.values() if n > 0)

    # ---- summary table rows ----
    summary_rows = ""
    for i, (site_name, cnt) in enumerate(site_counts.items()):
        bg = "#ffffff" if i % 2 == 0 else "#f8f9fa"
        badge_bg = "#0d47a1" if cnt > 0 else "#bdbdbd"
        summary_rows += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 20px;font-size:13px;color:#333;border-bottom:1px solid #e8e8e8'>{site_name}</td>"
            f"<td style='padding:9px 20px;text-align:center;border-bottom:1px solid #e8e8e8'>"
            f"<span style='background:{badge_bg};color:#fff;border-radius:12px;"
            f"padding:3px 10px;font-size:12px;font-weight:bold'>{cnt}</span></td>"
            f"</tr>"
        )

    # ---- tender rows ----
    tender_rows = ""
    for i, r in enumerate(tenders):
        bg = "#ffffff" if i % 2 == 0 else "#f0f7ff"
        src = r.get("source", r.get("site_id", ""))
        title = r.get("title", "")
        closing = r.get("closing_date", "")
        tender_rows += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:8px 14px;font-size:11px;color:#0d47a1;font-weight:bold;"
            f"white-space:nowrap;border-bottom:1px solid #e8e8e8'>{src}</td>"
            f"<td style='padding:8px 14px;font-size:12px;color:#222;"
            f"border-bottom:1px solid #e8e8e8'>{title}</td>"
            f"<td style='padding:8px 14px;font-size:12px;color:#333;"
            f"white-space:nowrap;border-bottom:1px solid #e8e8e8'>{closing}</td>"
            f"</tr>"
        )

    html_body = f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#eef2f7;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#eef2f7">
<tr><td align="center" style="padding:28px 12px">

  <table width="780" cellpadding="0" cellspacing="0"
         style="background:#ffffff;border-radius:10px;overflow:hidden;
                box-shadow:0 4px 16px rgba(0,0,0,.10)">

    <!-- HEADER -->
    <tr>
      <td style="background:linear-gradient(135deg,#0d47a1 0%,#1565c0 100%);padding:30px 36px">
        <div style="color:#ffffff;font-size:20px;font-weight:bold;letter-spacing:1px;
                    text-transform:uppercase">
          Malaysian Tender Daily Alert
        </div>
        <div style="color:#90caf9;font-size:13px;margin-top:6px">
          {today_str} &nbsp;&middot;&nbsp; Run ID: {run_stamp}
        </div>
      </td>
    </tr>

    <!-- STATS BAR -->
    <tr>
      <td style="background:#e3f2fd;padding:18px 36px">
        <table cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding-right:40px;text-align:center">
              <div style="font-size:36px;font-weight:bold;color:#0d47a1;line-height:1">{len(tenders)}</div>
              <div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.8px;margin-top:4px">Open Tenders</div>
            </td>
            <td style="padding-right:40px;text-align:center">
              <div style="font-size:36px;font-weight:bold;color:#0d47a1;line-height:1">{active_count}</div>
              <div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.8px;margin-top:4px">Active Sources</div>
            </td>
            <td style="text-align:center">
              <div style="font-size:36px;font-weight:bold;color:#0d47a1;line-height:1">10</div>
              <div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.8px;margin-top:4px">Sites Monitored</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- SECTION: SUMMARY -->
    <tr>
      <td style="padding:28px 36px 0">
        <div style="font-size:13px;font-weight:bold;color:#0d47a1;text-transform:uppercase;
                    letter-spacing:.8px;border-bottom:2px solid #0d47a1;padding-bottom:8px;margin-bottom:0">
          Summary by Source
        </div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
          <tr style="background:#0d47a1">
            <th style="padding:9px 20px;text-align:left;color:#fff;font-size:12px;
                       font-weight:normal;text-transform:uppercase;letter-spacing:.5px">Source</th>
            <th style="padding:9px 20px;text-align:center;color:#fff;font-size:12px;
                       font-weight:normal;text-transform:uppercase;letter-spacing:.5px;width:80px">Count</th>
          </tr>
          {summary_rows}
        </table>
      </td>
    </tr>

    <!-- SECTION: ALL TENDERS -->
    <tr>
      <td style="padding:28px 36px 0">
        <div style="font-size:13px;font-weight:bold;color:#0d47a1;text-transform:uppercase;
                    letter-spacing:.8px;border-bottom:2px solid #0d47a1;padding-bottom:8px;margin-bottom:0">
          All Open Tenders
        </div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
          <tr style="background:#0d47a1">
            <th style="padding:9px 14px;text-align:left;color:#fff;font-size:12px;
                       font-weight:normal;text-transform:uppercase;letter-spacing:.5px;width:100px">Source</th>
            <th style="padding:9px 14px;text-align:left;color:#fff;font-size:12px;
                       font-weight:normal;text-transform:uppercase;letter-spacing:.5px">Tender Title</th>
            <th style="padding:9px 14px;text-align:left;color:#fff;font-size:12px;
                       font-weight:normal;text-transform:uppercase;letter-spacing:.5px;width:100px">Closing Date</th>
          </tr>
          {tender_rows}
        </table>
      </td>
    </tr>

    <!-- FOOTER -->
    <tr>
      <td style="padding:20px 36px;background:#f5f7fa;border-top:1px solid #e0e0e0;margin-top:28px">
        <div style="font-size:11px;color:#888">
          Full data attached as <strong>all_tenders.json</strong> &nbsp;&middot;&nbsp;
          Generated by Tender Orchestrator &nbsp;&middot;&nbsp; {today_str}
        </div>
      </td>
    </tr>

  </table>
</td></tr>
</table>
</body>
</html>"""

    # ---- assemble the message ----
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[Tender Alert] {len(tenders)} Open Tenders — {today_str}"
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    json_bytes = combined_path.read_bytes()
    attachment = MIMEApplication(json_bytes, Name="all_tenders.json")
    attachment["Content-Disposition"] = 'attachment; filename="all_tenders.json"'
    msg.attach(attachment)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"email    -> sent to {', '.join(recipients)}  ({len(tenders)} tenders)")
    except Exception as exc:
        print(f"email    -> FAILED: {exc}", file=sys.stderr)


def _is_open(row: dict[str, Any], today: date) -> bool:
    """Return True if the tender should be kept (open or no date info)."""
    closing = _parse_closing_date(row.get("closing_date"))
    if closing is None:
        return True   # can't tell → keep it
    return closing >= today


_PHONE_RE    = re.compile(r"^[\d\s\-+()\/.]+$")           # only digits/symbols → phone number
_KEYWORD_RE  = re.compile(r"^[A-Z][A-Z\s\-\/]+$")         # all-caps short keyword → FAKS, E-MEL, TALIAN AM
_HEADER_WORDS = {"bil", "no", "tajuk", "title", "reference_no", "tarikh", "date", "status"}


def _is_valid(row: dict[str, Any]) -> bool:
    """Return True if the row looks like a real tender entry.

    Rejects:
    - no title
    - title is a phone number  (MPAJ contact footer)
    - closing_date is a keyword not a date  (FAKS, E-MEL → MPAJ contact footer)
    - any field value equals its own key name  (BIL=BIL → MBSJ repeated header rows)
    - no closing_date AND no url  (notices / nav rows with no actionable date)
    """
    title = (row.get("title") or "").strip()
    if not title:
        return False

    # phone number in title column
    if _PHONE_RE.match(title):
        return False

    closing = (row.get("closing_date") or "").strip()

    # closing_date is a keyword like "FAKS" or "E-MEL", not a real date
    if closing and _KEYWORD_RE.match(closing):
        return False

    # header row echoed into data — any field value == its own field name
    for key, val in row.items():
        if isinstance(val, str) and val.strip().lower() == key.strip().lower():
            return False

    return bool(closing) or bool(row.get("url"))


def orchestrate_once(
    sites: list[Site],
    output_root: Path,
    only: str | None = None,
) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary: dict[str, Any] = {
        "run_stamp": stamp,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "sites": [],
    }
    for site in sites:
        if not site.enabled:
            continue
        if only and site.id != only:
            continue
        run_dir = output_root / site.id / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        # Pass forward-slashed paths to webwright on Windows: agent-generated
        # Python code frequently treats backslashes as escape sequences,
        # producing filenames like ``C:fooC:bar.py`` instead of nested dirs.
        run_dir_for_webwright = Path(str(run_dir).replace("\\", "/"))
        tenders: list[dict[str, Any]] = []
        tenders_path = run_dir / "tenders.json"
        result: dict[str, Any] = {"site_id": site.id, "mode": site.mode, "ok": False,
                                  "error_tail": "uninitialized", "run_dir": str(run_dir)}
        # ---- mode dispatch with per-site error isolation ----
        try:
            if site.mode == "http":
                result = fetch_via_http(site, run_dir)
                tenders_path = Path(result["tenders_path"])
                if tenders_path.exists():
                    try:
                        tenders = json.loads(tenders_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        tenders = []
            elif site.mode == "cached":
                result = replay_cached_script(site, run_dir)
                tenders_path = Path(result["tenders_path"])
                if tenders_path.exists():
                    try:
                        tenders = json.loads(tenders_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        tenders = []
            else:  # default: full webwright agent run
                result = run_webwright_for_site(site, run_dir_for_webwright)
                trajectory = next(run_dir.rglob("trajectory.json"), None)
                tenders_path = run_dir / "tenders.json"
                if trajectory is not None:
                    tenders = extract_tenders(trajectory)
                    tenders_path.write_text(
                        json.dumps(tenders, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
        except Exception as exc:
            # One site's failure must not abort the whole pass
            result = {
                "site_id": site.id,
                "mode": site.mode,
                "ok": False,
                "exit_code": None,
                "error_tail": f"{type(exc).__name__}: {exc}"[-400:],
                "run_dir": str(run_dir),
            }
            print(f"[{site.id}] CRASH {type(exc).__name__}: {exc}")

        # Make sure tenders_path points at a real file even on failure
        if not tenders_path.exists():
            tenders_path.write_text("[]", encoding="utf-8")

        history_path = output_root / site.id / HISTORY_FILENAME
        diff = diff_against_history(tenders, history_path)
        write_history_entry(
            history_path,
            {
                "site_id": site.id,
                "stamp": stamp,
                "rows": tenders,
                "diff": diff,
            },
        )
        summary["sites"].append(
            {
                **result,
                "tenders_path": str(tenders_path),
                "tender_count": len(tenders),
                "diff": diff,
            }
        )
        print(
            f"[{site.id}] {'OK' if result['ok'] else 'FAIL'} "
            f"rows={len(tenders)} "
            f"all_time={diff.get('all_time_total', '?')} "
            + ("(first run — baseline established)"
               if diff.get("first_run")
               else f"new={diff.get('new_count', '?')} "
                    f"brand_new={diff.get('brand_new_count', '?')} "
                    f"removed={diff.get('removed_count', '?')} "
                    f"unchanged={diff.get('unchanged_count', '?')}")
        )

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary_path = output_root / f"summary_{stamp}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsummary -> {summary_path}")

    # Write combined output: every open tender from every site in one flat list.
    today = datetime.now().date()
    all_tenders: list[dict[str, Any]] = []
    skipped_null = 0
    skipped_closed = 0
    for site_result in summary["sites"]:
        tp = site_result.get("tenders_path")
        if not tp:
            continue
        try:
            rows = json.loads(Path(tp).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        display_name = SITE_DISPLAY_NAMES.get(site_result["site_id"], site_result["site_id"])
        for row in rows:
            if not _is_valid(row):
                skipped_null += 1
                continue
            if not _is_open(row, today):
                skipped_closed += 1
                continue
            normalized = dict(row)
            if normalized.get("title"):
                normalized["title"] = normalized["title"].upper()
            for date_field in ("closing_date", "issue_date", "date"):
                if normalized.get(date_field):
                    normalized[date_field] = _normalize_date_str(normalized[date_field]) or normalized[date_field]
            all_tenders.append({"source": display_name, "site_id": site_result["site_id"], **normalized})
    combined_path = output_root / "all_tenders.json"
    combined_path.write_text(json.dumps(all_tenders, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"combined  -> {combined_path}  "
        f"({len(all_tenders)} open tenders, "
        f"skipped {skipped_closed} closed + {skipped_null} no-title)"
    )

    send_email_report(all_tenders, combined_path, stamp)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Tender orchestrator for Webwright.")
    parser.add_argument("--sites", default=str(DEFAULT_SITES_FILE), help="Path to tender_sites.yaml")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Where to write per-site outputs")
    parser.add_argument("--only", default=None, help="Run only the site with this id")
    parser.add_argument("--mode", choices=["http", "cached", "webwright"], default=None,
                        help="Run only sites with this mode (default: all enabled)")
    parser.add_argument("--loop", action="store_true", help="Run forever on --interval-hours cadence")
    parser.add_argument("--interval-hours", type=float, default=24.0, help="Loop interval in hours (default 24)")
    args = parser.parse_args()

    load_env_file(PROJECT_ROOT / ".env")
    sites = load_sites(Path(args.sites))
    if not sites:
        print(f"No sites defined in {args.sites}", file=sys.stderr)
        return 2
    if args.mode:
        sites = [s for s in sites if s.mode == args.mode]
        if not sites:
            print(f"No sites with mode={args.mode!r} in {args.sites}", file=sys.stderr)
            return 2
    output_root = Path(args.output_root)

    if args.loop:
        while True:
            orchestrate_once(sites, output_root, only=args.only)
            sleep_seconds = max(60, int(args.interval_hours * 3600))
            print(f"\nsleeping {sleep_seconds}s before next pass...\n")
            time.sleep(sleep_seconds)
    else:
        summary = orchestrate_once(sites, output_root, only=args.only)
        all_ok = all(s.get("ok") for s in summary["sites"])
        n_fail = sum(1 for s in summary["sites"] if not s.get("ok"))
        return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())