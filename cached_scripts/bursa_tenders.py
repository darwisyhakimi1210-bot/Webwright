"""Cached extraction script for Bursa Malaysia tenders.

Bursa is behind Cloudflare — requires stealth browser headers.
Table index 1 holds the tender listing (Posting Date, Closing Date,
Tender Overview, Tender Notice).

Usage: python bursa_tenders.py <run_dir>
"""
import asyncio
import json
import re
import sys
from pathlib import Path

URL = "https://www.bursamalaysia.com/about_bursa/about_us/tenders"
UA  = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/125.0.0.0 Safari/537.36")


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).replace("\xa0", " ").strip() or None


async def _extract() -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1366,768",
            ],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
            },
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
            Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
            window.chrome={runtime:{}};
        """)
        page = await ctx.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(8000)

        # Table index 1: Posting Date | Closing Date | Tender Overview | Notice
        rows = await page.evaluate("""() => {
            const table = document.querySelectorAll("table")[1];
            if (!table) return [];
            return Array.from(table.querySelectorAll("tbody tr")).map(r => ({
                issue_date:   (r.cells[0]?.innerText || "").trim(),
                closing_date: (r.cells[1]?.innerText || "").trim(),
                title:        (r.cells[2]?.innerText || "").trim(),
            }));
        }""")
        await browser.close()

    return [
        {
            "title":        _clean(r["title"]),
            "issue_date":   _clean(r["issue_date"]),
            "closing_date": _clean(r["closing_date"]),
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
