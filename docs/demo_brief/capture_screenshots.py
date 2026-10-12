"""Capture the screenshots used in the demo brief.

Start the app first (with a working API key), then run:

    streamlit run app.py                      # in one terminal
    python docs/demo_brief/capture_screenshots.py   # in another

Writes PNGs to docs/demo_brief/assets/. Set CHROMIUM_PATH if Playwright's own browser isn't installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = os.environ.get("APP_URL", "http://localhost:8501")
QUESTION = "Which expense categories are most over budget in 2026 YTD?"
OUT = Path(__file__).parent / "assets"


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        # 1. Landing page + positions of the elements the annotations point at
        await page.goto(BASE_URL + "/")
        await page.wait_for_selector('[data-testid="stChatInput"]')
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / "landing.png"))
        targets = {
            "nav_catalog": page.get_by_text("Data catalog", exact=True).first,
            "nav_upload": page.get_by_text("Bring your own data", exact=True).first,
            "dataset_select": page.locator('[data-testid="stSelectbox"]').first,
            "examples": page.locator(".st-key-examples").first,
            "chat_input": page.locator('[data-testid="stChatInput"]').first,
        }
        boxes = {k: await loc.bounding_box() for k, loc in targets.items()}
        (OUT / "landing_boxes.json").write_text(json.dumps(boxes, indent=1))

        # 2. Ask one finance question; the answer shows text, chart and the SQL tab
        await page.get_by_role("button", name=QUESTION).click()
        await page.locator(".ad-meta, [data-testid='stAlert']").last.wait_for(timeout=240_000)
        await page.wait_for_timeout(3000)
        msgs = page.locator('[data-testid="stChatMessage"]')
        answer = msgs.nth(await msgs.count() - 1)
        if "Couldn't" in await answer.inner_text():
            print("The app could not reach the AI provider - answer screenshot skipped.", file=sys.stderr)
            ok = False
        else:
            await page.set_viewport_size({"width": 1440, "height": 2400})
            await page.wait_for_timeout(2000)
            await answer.screenshot(path=str(OUT / "answer.png"))
            (OUT / "answer.txt").write_text(await answer.inner_text())
            ok = True

        # 3. Data catalog (finance is the default dataset)
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.get_by_text("Data catalog", exact=True).first.click()
        await page.wait_for_timeout(3500)
        await page.screenshot(path=str(OUT / "catalog.png"))
        await browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
