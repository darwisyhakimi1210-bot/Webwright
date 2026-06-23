"""Cached extraction script for DBKL ePerolehan public listing.

Flow:
  1. Load home page.
  2. Close the landing popup (#modalHomeLandingPopup) by clicking "Tutup".
  3. Click the "Iklan Tender/Sebutharga" tab button.
  4. Scrape the tender table (Table 1: Jenis, No.Tender, Tajuk, Tarikh Iklan,
     Tarikh Tutup, ...).
  5. Paginate through pages 1–3 via the pagination buttons (DataTables style).

Usage: python dbkl_eperolehan.py <run_dir>
"""
import asyncio
import json
import re
import sys
from pathlib import Path

URL = "https://eperolehan.dbkl.gov.my/public-user/home"
UA  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
MAX_PAGES = 3


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).replace("\xa0", " ").strip() or None


def _parse_table(rows_data: list[list[str]]) -> list[dict]:
    results = []
    for cells in rows_data:
        if len(cells) < 3:
            continue
        # cells: [Jenis, No.Tender/Sebutharga, Tajuk, Tarikh Iklan, Tarikh Tutup, ...]
        title        = _clean(cells[2]) if len(cells) > 2 else None
        reference_no = _clean(cells[1]) if len(cells) > 1 else None
        issue_date   = _clean(cells[3]) if len(cells) > 3 else None
        closing_date = _clean(cells[4]) if len(cells) > 4 else None
        if not title:
            continue
        # Skip table header rows echoed into tbody
        if title.lower() in {"tajuk", "title"}:
            continue
        results.append({
            "title":        title,
            "reference_no": reference_no,
            "issue_date":   issue_date,
            "closing_date": closing_date,
        })
    return results


async def _extract() -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
            timezone_id="Asia/Kuala_Lumpur",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # ---- Step 1: Close landing popup ----
        popup_close = page.locator("#modalHomeLandingPopup .btn-secondary")
        if await popup_close.count() > 0:
            await popup_close.click()
            await page.wait_for_timeout(800)
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)

        # ---- Step 2: Click "Iklan Tender/Sebutharga" tab ----
        tab = page.locator("button:has-text('Iklan Tender/Sebutharga')")
        if await tab.count() > 0:
            await tab.click()
            await page.wait_for_timeout(3000)

        # ---- Step 3: Scrape tender table across up to MAX_PAGES pages ----
        all_rows: list[dict] = []

        for page_num in range(1, MAX_PAGES + 1):
            rows_data = await page.evaluate("""() => {
                // Table 1 is the Iklan Tender/Sebutharga table
                const tables = document.querySelectorAll("table");
                const t = tables[1];
                if (!t) return [];
                return Array.from(t.querySelectorAll("tbody tr")).map(r =>
                    Array.from(r.querySelectorAll("td")).map(td => td.innerText.trim())
                );
            }""")
            page_parsed = _parse_table(rows_data)
            all_rows.extend(page_parsed)

            if page_num >= MAX_PAGES:
                break

            # Click "Seterusnya" for the Iklan Tender table specifically
            next_btn = page.locator("#TenderIklan-table_next:not(.disabled) a")
            if await next_btn.count() == 0:
                break
            await next_btn.click()
            await page.wait_for_timeout(2000)

        await browser.close()

    return all_rows


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    run_dir.mkdir(parents=True, exist_ok=True)
    tenders = asyncio.run(_extract())
    out = run_dir / "tenders.json"
    out.write_text(json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tenders)} tenders -> {out}")


if __name__ == "__main__":
    main()
