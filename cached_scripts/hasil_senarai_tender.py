"""Cached extraction script for HASIL (LHDN) senarai tender.

The page has a "Tahun" dropdown and a "Cari" button. We select 2026,
click Cari, then parse the results table.

Usage (called by tender_orchestrator.py in cached mode):
    python hasil_senarai_tender.py <run_dir>

Writes <run_dir>/tenders.json with fields: title, issue_date, closing_date, status.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

URL = "https://www.hasil.gov.my/pautan-pantas/perolehan/senarai-tender-sebutharga"
UA  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
TARGET_YEAR = "2026"


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).replace("\xa0", " ").strip() or None


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
            locale="ms-MY",
            timezone_id="Asia/Kuala_Lumpur",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # ---- Select year "2026" — try several possible selector names ----
        selected = False
        for sel in [
            "select[name='year']",
            "select[name='tahun']",
            "select[name='Tahun']",
            "select[name='TAHUN']",
        ]:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    await page.select_option(sel, TARGET_YEAR)
                    selected = True
                    break
            except Exception:
                continue

        if not selected:
            # Fallback: find any <select> that has an option with "2026"
            await page.evaluate(f"""() => {{
                const yr = "{TARGET_YEAR}";
                for (const s of document.querySelectorAll('select')) {{
                    const opt = Array.from(s.options).find(
                        o => o.value === yr || o.text.trim() === yr || o.text.includes(yr)
                    );
                    if (opt) {{
                        s.value = opt.value;
                        s.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return;
                    }}
                }}
            }}""")

        await page.wait_for_timeout(500)

        # ---- Click "Cari" submit button ----
        clicked = False
        for btn_sel in [
            "input[type='submit'][value='Cari']",
            "button:text('Cari')",
            "input[value='Cari']",
            "input[value='CARI']",
        ]:
            try:
                if await page.locator(btn_sel).count() > 0:
                    await page.click(btn_sel)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Fallback: click the first submit button on the form
            await page.evaluate(
                "() => { const b = document.querySelector(\"input[type='submit'],button[type='submit']\"); if(b) b.click(); }"
            )

        await page.wait_for_load_state("networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # ---- Extract table ----
        # Columns: Tajuk | Tarikh Paparan | Tarikh Tutup | Lampiran | Kaedah | Status
        rows = await page.evaluate("""() => {
            const table = document.querySelector("table");
            if (!table) return [];
            return Array.from(table.querySelectorAll("tbody tr")).map(r => ({
                title:        (r.cells[0]?.innerText || "").trim(),
                issue_date:   (r.cells[1]?.innerText || "").trim(),
                closing_date: (r.cells[2]?.innerText || "").trim(),
                status:       (r.cells[5]?.innerText || "").trim()
            }));
        }""")
        await browser.close()

    return [
        {
            "title":        _clean(r["title"]),
            "issue_date":   _clean(r["issue_date"]),
            "closing_date": _clean(r["closing_date"]),
            "status":       _clean(r["status"]),
        }
        for r in rows
        if r.get("title")
    ]


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    run_dir.mkdir(parents=True, exist_ok=True)
    tenders = asyncio.run(_extract())
    out = run_dir / "tenders.json"
    out.write_text(json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tenders)} tenders -> {out}")


if __name__ == "__main__":
    main()
