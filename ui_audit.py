"""
UI Audit & Fix Loop — Take screenshots, Claude analyzes visually, fix issues.

Usage:
    await capture_screenshot(url) -> base64 image
    await audit_page(url) -> dict with issues + screenshot
    await audit_and_fix(url, fix_dir) -> before/after screenshots
"""

import asyncio
import base64
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("ui-audit")

SCREENSHOT_DIR = Path("/tmp/inxotive-uis")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
    # Set browser path explicitly
    _browsers_path = str(Path.home() / ".cache" / "ms-playwright")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers_path)
except ImportError:
    logger.warning("Playwright not installed")


async def capture_screenshot(url: str, selector: str = "", width: int = 1280, height: int = 720) -> Dict:
    """Capture a screenshot of a page or element.

    Args:
        url: Full URL to capture (e.g. http://localhost:8888/hub)
        selector: Optional CSS selector to capture specific element
        width: Viewport width
        height: Viewport height

    Returns: {"success": bool, "screenshot": "data:image/png;base64,...", "path": str, "error": str}
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {"success": False, "error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}

    timestamp = int(time.time())
    filename = f"screenshot_{timestamp}.png"
    filepath = SCREENSHOT_DIR / filename

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = await browser.new_page(viewport={"width": width, "height": height})

            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
            except Exception:
                try:
                    await page.goto(url, wait_until="load", timeout=10000)
                except Exception as e:
                    await browser.close()
                    return {"success": False, "error": f"Page load failed: {e}"}

            await asyncio.sleep(0.5)  # Let animations settle

            if selector:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        await element.screenshot(path=str(filepath))
                    else:
                        await page.screenshot(path=str(filepath))
                except Exception:
                    await page.screenshot(path=str(filepath))
            else:
                await page.screenshot(path=str(filepath), full_page=False)

            await browser.close()

        if not filepath.exists():
            return {"success": False, "error": "Screenshot file not created"}

        # Read as base64
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        # Cleanup file (keep in memory)
        os.unlink(filepath)

        return {"success": True, "screenshot": f"data:image/png;base64,{b64}", "url": url}

    except Exception as e:
        logger.error("Screenshot failed: %s", e)
        return {"success": False, "error": str(e)}


async def screenshot_comparison(url: str, before_dir: Optional[str] = None, after_dir: Optional[str] = None) -> Dict:
    """Take full-page and mobile screenshots for comparison.

    Returns both desktop and mobile views.
    """
    desktop = await capture_screenshot(url, width=1280, height=720)
    mobile = await capture_screenshot(url, width=375, height=812)

    issues = []
    if desktop.get("success"):
        issues.append("✅ Desktop view captured")
    if mobile.get("success"):
        issues.append("✅ Mobile view captured")

    return {
        "success": desktop.get("success"),
        "desktop": desktop.get("screenshot"),
        "mobile": mobile.get("screenshot"),
        "url": url,
        "notes": "\n".join(issues),
    }


async def audit_and_fix_loop(
    url: str,
    description: str = "",
    fix_dir: Optional[str] = None,
    desktop_width: int = 1280,
    mobile_width: int = 375,
) -> Dict:
    """Full audit loop: capture → Claude sees → fix → recapture.

    This function captures a screenshot and returns structured data
    for Claude to analyze and fix.

    Returns: {"success": bool, "screenshot_desktop": str, "screenshot_mobile": str,
              "url": str, "viewport": str, "timestamp": str}
    """
    result = await screenshot_comparison(url)

    return {
        **result,
        "viewport": f"{desktop_width}x720 desktop / {mobile_width}x812 mobile",
        "timestamp": datetime.now().isoformat(),
        "description": description,
        "instructions": (
            "Claude, lihat screenshot ini. Identifikasi masalah UI:\n"
            "1. Layout issues (overlap, overflow, broken grid)\n"
            "2. Responsive issues (mobile view)\n"
            "3. Color/contrast issues\n"
            "4. Typography issues\n"
            "5. Missing elements\n\n"
            "Setelah identifikasi, fix kodenya langsung."
        ),
    }
