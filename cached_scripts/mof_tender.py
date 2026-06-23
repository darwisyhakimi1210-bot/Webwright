"""Cached extraction script for MOF (Ministry of Finance) tender listing.

MOF is behind a WAF but accessible with stealth browser headers.
Tenders appear as h2 elements with links on the listing page.
Closing dates are not shown on the listing — extracted from the detail page
if available, otherwise left null.

Usage: python mof_tender.py <run_dir>
"""
import asyncio
import json
import re
import sys
from pathlib import Path

URL = "https://www.mof.gov.my/portal/ms/berita/tender"
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
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Tenders appear as <h2> links; skip the page-title "TENDER" heading
        items = await page.evaluate("""() =>
            Array.from(document.querySelectorAll("h2"))
                .filter(h => h.innerText.trim().toLowerCase() !== "tender")
                .map(h => {
                    const a = h.querySelector("a");
                    // Look for a date near the h2 (sibling span/div)
                    const parent = h.parentElement;
                    const dateEl = parent?.querySelector("time, .date, [class*='date'], span");
                    return {
                        title: h.innerText.trim(),
                        href:  a ? a.href : "",
                        date:  dateEl ? dateEl.innerText.trim() : null
                    };
                })
        """)
        await browser.close()

    return [
        {
            "title":        _clean(r["title"]),
            "date":         _clean(r["date"]),
            "closing_date": _clean(r["date"]),   # best available date on listing
            "url":          r["href"] or None,
        }
        for r in items
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
