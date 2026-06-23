"""Cached extraction script for BNM procurement notices.

Usage (called by tender_orchestrator.py in cached mode):
    python bnm_procurement.py <run_dir>

Writes <run_dir>/tenders.json with fields: title, issue_date, closing_date, category.
"""
import asyncio
import json
import re
import sys
from pathlib import Path


URL = "https://www.bnm.gov.my/procurement-notices"


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).replace("\xa0", " ").strip()
    return text or None


def _split_date_range(issuance: str) -> tuple[str | None, str | None]:
    """'16 Jun 2026 - 26 Jun 2026' -> ('16 Jun 2026', '26 Jun 2026')"""
    m = re.match(r"(.+?)\s*-\s*(.+)", issuance or "")
    if m:
        return m.group(1).strip() or None, m.group(2).strip() or None
    return issuance or None, None


async def _extract() -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # BNM uses <table class="standard-table"> — one table per category.
        tables = await page.locator("table.standard-table").evaluate_all("""
            tables => tables.map(table => ({
                header: (table.querySelector('th b')?.innerText
                         || table.querySelector('th')?.innerText
                         || '').trim().replace(/\\s+/g, ' '),
                rows: Array.from(table.querySelectorAll('tr')).map(tr =>
                    Array.from(tr.cells).map(td =>
                        (td.innerText || '').trim().replace(/\\s+/g, ' ')
                    )
                )
            }))
        """)
        await browser.close()

    SKIP_TITLES = {
        "Request for Price Quotation",
        "Tender / Request for Proposal",
        "Request for Information",
        "No current record exists.",
    }
    results = []
    for table in tables:
        category = _clean(table.get("header")) or None
        for cells in table.get("rows", []):
            if len(cells) < 2:
                continue
            title = _clean(cells[0])
            if not title or title in SKIP_TITLES or title.startswith("| Subscribe"):
                continue
            issuance = _clean(cells[1]) if len(cells) > 1 else None
            closing = _clean(cells[2]) if len(cells) > 2 else None
            # issuance column is "DD Mon YYYY - DD Mon YYYY"; split into two.
            issue_date, _ = _split_date_range(issuance or "")
            # closing column is already a single date/time string.
            results.append({
                "title": title,
                "issue_date": issue_date,
                "closing_date": closing,
                "category": category,
            })
    return results


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    run_dir.mkdir(parents=True, exist_ok=True)
    tenders = asyncio.run(_extract())
    out = run_dir / "tenders.json"
    out.write_text(json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tenders)} tenders -> {out}")


if __name__ == "__main__":
    main()
