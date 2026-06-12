"""
Verification Loop — auto-testing with screenshots for INXOTIVE HUB.
Supports: Playwright screenshots, curl health checks, pytest running.

Usage:
    await verify_endpoint("http://localhost:8888/hub")
    await verify_with_screenshot("http://localhost:8888/hub", "hub-page")
    await run_verification_loop(url, actions)
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable

logger = logging.getLogger("verify")

# Try to detect if Playwright is available
PLAYWRIGHT_AVAILABLE = False
try:
    # Just check if the binary exists
    result = subprocess.run(
        ["which", "playwright"], capture_output=True, text=True, timeout=5
    )
    PLAYWRIGHT_AVAILABLE = result.returncode == 0
except Exception:
    pass


async def verify_endpoint(url: str, expected_status: int = 200, timeout: int = 10) -> Dict:
    """Verify an HTTP endpoint returns expected status code.

    Returns dict with success, status, time_ms, error.
    """
    import httpx
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            elapsed = int((time.time() - start) * 1000)
            return {
                "success": r.status_code == expected_status,
                "status": r.status_code,
                "expected": expected_status,
                "time_ms": elapsed,
                "url": url,
            }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {
            "success": False,
            "status": 0,
            "expected": expected_status,
            "time_ms": elapsed,
            "url": url,
            "error": str(e)[:200],
        }


async def verify_multiple(endpoints: List[Dict]) -> List[Dict]:
    """Verify multiple endpoints in parallel.

    endpoints: [{"name": "hub", "url": "http://localhost:8888/hub", "expected": 200}, ...]
    """
    tasks = []
    for ep in endpoints:
        tasks.append(verify_endpoint(
            ep.get("url", ""),
            ep.get("expected", 200),
            ep.get("timeout", 10),
        ))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for i, ep in enumerate(endpoints):
        result = results[i] if i < len(results) else {"success": False, "error": "No result"}
        if isinstance(result, Exception):
            result = {"success": False, "error": str(result)}
        output.append({**ep, **result})

    return output


async def verify_with_screenshot(url: str, name: str = "screenshot", timeout: int = 30) -> Dict:
    """Take a screenshot using Python + playwright if available.

    Returns dict with success, base64 image data, or error.
    """
    try:
        # Try using Python playwright
        from playwright.async_api import async_playwright
        output_path = f"/tmp/inxotive-verify/{name}_{int(time.time())}.png"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            await page.screenshot(path=output_path, full_page=False)
            await browser.close()

        if not Path(output_path).exists():
            return {"success": False, "error": "Screenshot not created"}

        import base64
        with open(output_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.unlink(output_path)

        return {"success": True, "image": f"data:image/png;base64,{b64}", "url": url}

    except ImportError:
        return {"success": False, "error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


async def run_verification_loop(
    base_url: str,
    actions: List[Dict],
    screenshot_after: Optional[List[int]] = None,
) -> List[Dict]:
    """Run a sequence of actions and optionally take screenshots.

    actions: [
        {"type": "get", "endpoint": "/api/mcp/servers"},
        {"type": "post", "endpoint": "/api/youtube/search", "body": {"query": "test"}},
    ]
    screenshot_after: list of action indices to take screenshots after.

    Returns list of action results with optional screenshots.
    """
    import httpx
    results = []
    screenshot_after = screenshot_after or []

    async with httpx.AsyncClient(timeout=30, base_url=base_url) as client:
        for i, action in enumerate(actions):
            try:
                if action["type"] == "get":
                    r = await client.get(action["endpoint"])
                elif action["type"] == "post":
                    r = await client.post(action["endpoint"], json=action.get("body", {}))
                else:
                    results.append({"index": i, "error": f"Unknown action type: {action['type']}"})
                    continue

                result = {
                    "index": i,
                    "action": f"{action['type']} {action['endpoint']}",
                    "status": r.status_code,
                    "ok": r.status_code < 500,
                    "time_ms": r.elapsed.total_seconds() * 1000 if hasattr(r, 'elapsed') else 0,
                }

                # Include body preview for debugging
                try:
                    body = r.json()
                    result["body_preview"] = json.dumps(body)[:300]
                except Exception:
                    result["body_preview"] = r.text[:200]

                results.append(result)

                # Screenshot after this action?
                if i in screenshot_after:
                    ss = await verify_with_screenshot(f"{base_url}{action['endpoint']}", f"step_{i}")
                    results[-1]["screenshot"] = ss.get("image") if ss.get("success") else None

            except Exception as e:
                results.append({"index": i, "action": f"{action['type']} {action['endpoint']}", "error": str(e)[:200]})

    return results


def format_verification_report(results: List[Dict]) -> str:
    """Format verification results as readable report."""
    total = len(results)
    passed = sum(1 for r in results if r.get("ok"))
    failed = total - passed

    lines = [
        f"## ✅ Verification Report",
        f"**Passed:** {passed}/{total} | **Failed:** {failed}",
        f"**Time:** {datetime.now().strftime('%H:%M:%S')}",
        "",
    ]

    for r in results:
        icon = "✅" if r.get("ok") else "❌"
        action = r.get("action", "?")
        status = r.get("status", "err")
        ms = r.get("time_ms", 0)
        lines.append(f"{icon} `{action}` → {status} ({ms:.0f}ms)")

    if failed:
        lines.extend(["", "**Failed:**"])
        for r in results:
            if not r.get("ok"):
                lines.append(f"- `{r.get('action')}`: {r.get('error', r.get('body_preview', 'Unknown'))[:200]}")

    return "\n".join(lines)
