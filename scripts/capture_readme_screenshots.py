"""One-off script to capture README screenshots — uses the exact same
Playwright method the collectors themselves use (see backend/collectors/
website.py: sync_playwright, headless chromium, a real UA/viewport, goto with
wait_until=domcontentloaded, a settle timeout, then a screenshot).

Run against a live local server: uvicorn backend.main:app --port 8123
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from backend.collectors.website import UA  # noqa: E402

BASE = "http://localhost:8123"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

# A single real, coherent, already-completed job (all-NASA sources) — good
# demonstration of scale (15 sources) without the deliberately mixed-entity
# stress-test noise.
DEMO_JOB_ID = "ABC0001"


def shot_full(page, name: str) -> None:
    """Crop to actual content height (avoids a big dead-grey footer once
    #recentwrap is hidden) instead of a fixed full_page screenshot."""
    height = page.evaluate("document.body.scrollHeight")
    page.set_viewport_size({"width": 1360, "height": min(height + 20, 4000)})
    path = OUT / name
    page.screenshot(path=str(path), full_page=False)
    print(f"saved {path}  ({path.stat().st_size // 1024} KB)")


def shot_clip(page, name: str, top_box: dict, bottom_box: dict, pad: int = 12) -> None:
    y0 = max(0, top_box["y"] - pad)
    y1 = bottom_box["y"] - pad
    clip = {"x": 0, "y": y0, "width": 1360, "height": max(50, y1 - y0)}
    path = OUT / name
    page.screenshot(path=str(path), clip=clip)
    print(f"saved {path}  ({path.stat().st_size // 1024} KB)")


def hide_recent_jobs(page) -> None:
    """Recent jobs shows real personal usage history — not for public screenshots."""
    page.evaluate(
        "document.querySelector('#recentwrap')?.style.setProperty('display','none')"
    )


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent=UA, viewport={"width": 1360, "height": 900}
        ).new_page()

        # 1. Landing page: empty intake form.
        page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(800)
        hide_recent_jobs(page)
        shot_full(page, "01-intake.png")

        # 2. Fill a few links so the platform auto-detect badges show.
        sample_links = [
            "https://www.nasa.gov",
            "https://www.youtube.com/@nasa",
            "https://github.com/nasa",
            "https://www.linkedin.com/company/nasa",
        ]
        page.fill("#entity", "NASA")
        inputs = page.query_selector_all(".lurl")
        for i, url in enumerate(sample_links):
            if i >= len(inputs):
                page.click("#addlink")
            inputs = page.query_selector_all(".lurl")
            inputs[i].fill(url)
            inputs[i].dispatch_event("input")
        page.wait_for_timeout(300)
        hide_recent_jobs(page)
        shot_full(page, "02-intake-filled.png")

        # 3. Load a real, completed job (Recent jobs stays visible just long
        #    enough to click into it, then hidden before any screenshot).
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        page.click(f'#recent a[data-id="{DEMO_JOB_ID}"]')
        page.wait_for_timeout(1500)
        hide_recent_jobs(page)

        # clip() needs viewport-relative coordinates to match bounding_box(),
        # so resize the viewport to cover the whole page BEFORE reading any
        # bounding boxes below — otherwise clip regions land outside the
        # (still-900px-tall) viewport and Playwright rejects them.
        height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": 1360, "height": min(height + 20, 8000)})

        # 3a. Dossier header + Overview only (not the whole multi-page dossier).
        top = page.locator("h2", has_text="Dossier").first.bounding_box()
        bottom = page.locator("#dossier h2", has_text="Findings by Source").first.bounding_box()
        shot_clip(page, "03-dossier-overview.png", top, bottom)

        # 3b. The Inferred Insights section — the standout grounded-synthesis
        #     feature (every claim carries a confidence + cited evidence).
        top = page.locator("#dossier h2", has_text="Inferred Insights").first.bounding_box()
        bottom = page.locator("#dossier h2", has_text="Gaps & Caveats").first.bounding_box()
        shot_clip(page, "04-inferred-insights.png", top, bottom)

        # 4. One expanded source card (extracted text + real vision notes) —
        #    the "everything is inspectable" feature. Only expand the FIRST
        #    card so the page doesn't balloon with every source open.
        # Only expand "Vision notes" (not "Extracted text") — showing all 8
        # notes/screenshots would make this absurdly tall. One good example
        # (a 404 page the vision model described with actual personality) is
        # worth more than a wall of thumbnails.
        first_card = page.locator(".src").first
        for summary in first_card.locator("details summary").all():
            if "Vision notes" in (summary.text_content() or ""):
                summary.click()
        page.wait_for_timeout(1500)  # let the real screenshot <img> tags load
        height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": 1360, "height": min(height + 20, 8000)})
        top = page.locator("h2", has_text="Sources").first.bounding_box()
        second_note = first_card.locator(".vnote").nth(1).bounding_box()
        shot_clip(page, "05-source-inspection.png", top, second_note)

        browser.close()


if __name__ == "__main__":
    main()
