"""Cached extraction script for KWSP tender listing.

KWSP is behind Cloudflare — requires stealth browser headers.
Tenders are rendered as card blocks with class *small-cards*.
Each card text has the pattern:
    <title>
    No. Tender
    <ref>
    Tarikh Dibuka
    <date>
    Tarikh Tutup
    <date>

Usage: python kwsp_perolehan.py <run_dir>
"""
import asyncio
import json
import re
import sys
from pathlib import Path

URL = "https://www.kwsp.gov.my/ms/korporat/perolehan/tender"
UA  = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/125.0.0.0 Safari/537.36")


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    # Remove non-breaking spaces and weird control chars from Liferay
    text = re.sub(r"[ ​�]", " ", text)
    return re.sub(r"\s+", " ", text).strip() or None


def _after_label(text: str, label: str) -> str | None:
    """Return the value that follows a label line in card text."""
    m = re.search(rf"{re.escape(label)}\s*\n\s*(.+)", text)
    return _clean(m.group(1)) if m else None


def _parse_cards(cards_text: list[str]) -> list[dict]:
    results = []
    for text in cards_text:
        # Skip the archive/navigation card (contains month names only)
        if re.search(r"Januari|Februari|Mac\s+20", text):
            continue
        # Title = first non-empty line before "No. Tender"
        before_ref = text.split("No. Tender")[0].strip()
        title = _clean(before_ref.split("\n")[0]) if before_ref else None
        if not title:
            continue
        reference_no  = _after_label(text, "No. Tender")
        closing_date  = _after_label(text, "Tarikh Tutup")
        issue_date    = _after_label(text, "Tarikh Dibuka")
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
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(7000)

        cards_text = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('[class*="small-cards"]'))
                 .map(c => c.innerText.trim())
        """)
        await browser.close()

    return _parse_cards(cards_text)


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    run_dir.mkdir(parents=True, exist_ok=True)
    tenders = asyncio.run(_extract())
    out = run_dir / "tenders.json"
    out.write_text(json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tenders)} tenders -> {out}")


if __name__ == "__main__":
    main()
