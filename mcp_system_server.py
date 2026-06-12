"""
MCP Server: inxotive-system — Server status, services, logs, Docker, events.
Run: python3 mcp_system_server.py
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="[mcp-system] %(levelname)s %(message)s")
logger = logging.getLogger("mcp-system")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("inxotive-system")

HOME = Path.home()
EVENT_BUS = HOME / ".event_bus.json"
SERVICE_PORTS = {
    "market-api": 8888,
    "bot (discord)": 8080,
    "odysseus": 7000,
    "ollama": 11434,
    "casaos": 80,
}


async def _http_get(url: str, timeout: int = 5) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url)
            return r.status_code < 500
    except Exception:
        return False


def _run_cmd(cmd: list, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def get_server_status() -> str:
    """Get comprehensive server status: CPU, memory, disk, uptime."""
    try:
        # CPU load
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        cpu_pct = round((load[0] / cpu_count) * 100, 1)

        # Memory
        mem = _run_cmd(["free", "-h"]).split("\n")
        mem_line = ""
        for line in mem:
            if line.startswith("Mem:"):
                mem_line = " ".join(line.split())
                break

        # Disk
        du = shutil.disk_usage("/")
        disk_free_gb = du.free // (2**30)
        disk_total_gb = du.total // (2**30)
        disk_pct = round((du.used / du.total) * 100, 1)

        # Uptime
        uptime_s = open("/proc/uptime").read().split()[0]
        uptime_h = round(float(uptime_s) / 3600, 1)

        # Process count
        procs = _run_cmd(["ps", "--no-headers", "-e"]).count("\n") + 1

        # Temperature (if available)
        temp = ""
        for p in [Path("/sys/class/thermal/thermal_zone0/temp")]:
            if p.exists():
                t = int(p.read_text().strip()) / 1000
                temp = f"{t:.1f}°C"
                break

        lines = [
            "## 🖥️ Server Status — INXOTIVE SERVER",
            "",
            f"**CPU:** {cpu_pct}% load (avg 1m: {load[0]:.2f}, {cpu_count} cores)",
            f"**Memory:** {mem_line}",
            f"**Disk:** {disk_free_gb}GB free / {disk_total_gb}GB total ({disk_pct}% used)",
            f"**Uptime:** {uptime_h} hours",
            f"**Processes:** {procs} running",
        ]
        if temp:
            lines.append(f"**Temperature:** {temp}")

        # Top processes by CPU
        top = _run_cmd(["ps", "aux", "--sort=-%cpu", "--no-headers"])[:500]
        lines.extend(["", "**Top Processes (CPU):**", "```", top, "```"])

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting server status: {e}"


@mcp.tool()
async def check_services() -> str:
    """Check status of all INXOTIVE services: market-api, bot, odysseus, ollama, casaos."""
    try:
        results = []
        for name, port in SERVICE_PORTS.items():
            ok = await _http_get(f"http://localhost:{port}/")
            status = "✅ UP" if ok else "❌ DOWN"
            results.append(f"  {status} — {name} (port {port})")

        # Docker containers
        docker = _run_cmd(["docker", "ps", "--format", "{{.Names}} {{.Status}}"])
        docker_lines = docker.split("\n") if docker else []
        docker_status = "\n".join(f"  🐳 {d}" for d in docker_lines) if docker_lines else "  (no containers or Docker not running)"

        lines = [
            "## 📡 INXOTIVE Services",
            "",
            "**System Services:**",
            *results,
            "",
            "**Docker Containers:**",
            docker_status,
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error checking services: {e}"


@mcp.tool()
async def get_service_logs(service: str = "inxotive-bot", lines: int = 20) -> str:
    """Get recent journalctl logs for an INXOTIVE service.

    Args:
        service: systemd service name (inxotive-bot, odysseus, market-api, ollama)
        lines: number of recent log lines (default 20, max 100)
    """
    lines = min(max(lines, 5), 100)
    try:
        cmd = ["journalctl", "-u", service, "-n", str(lines), "--no-pager"]
        log = _run_cmd(cmd, timeout=15)
        if not log or "Error:" in log:
            # Fallback: try checking if service exists
            exists = _run_cmd(["systemctl", "is-active", service], timeout=5)
            return f"Service '{service}' status: {exists}\n\nCould not fetch logs. Try: inxotive-bot, odysseus, market-api, ollama"

        return f"## 📋 Logs: {service} (last {lines} lines)\n\n```\n{log[:3000]}\n```"
    except Exception as e:
        return f"Error fetching logs for '{service}': {e}"


@mcp.tool()
async def check_docker_status() -> str:
    """List running Docker containers and their status."""
    try:
        ps = _run_cmd(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"])
        if not ps or "Error:" in ps:
            return "Docker not running or not installed."

        # Also get container count
        all_count = _run_cmd(["docker", "ps", "-a", "-q"]).count("\n") or 0
        running = ps.count("\n") or 0

        # Disk usage
        disk = _run_cmd(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}\t{{.Reclaimable}}"])

        lines = [
            "## 🐳 Docker Status",
            f"**Containers:** {running} running / {all_count} total",
            "",
            "```",
            ps[:2000],
            "```",
        ]
        if disk:
            lines.extend(["", "**Disk Usage:**", "```", disk[:800], "```"])

        return "\n".join(lines)
    except Exception as e:
        return f"Error checking Docker: {e}"


@mcp.tool()
async def get_recent_events(limit: int = 10) -> str:
    """Get recent events from the INXOTIVE event bus.

    Args:
        limit: number of events to show (default 10, max 50)
    """
    limit = min(max(limit, 1), 50)
    try:
        if not EVENT_BUS.exists():
            return "Event bus file not found yet."

        events = json.loads(EVENT_BUS.read_text())
        if not events:
            return "No events recorded yet."

        events = events[-limit:]

        lines = [f"## 📊 Recent Events (last {len(events)})", ""]
        for e in reversed(events):
            sev = e.get("severity", "info")
            icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(sev, "ℹ️")
            ts = e.get("time", "")[:19]
            src = e.get("source", "?")
            msg = e.get("message", "")
            lines.append(f"{icon} [{ts}] **{src}:** {msg[:200]}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading events: {e}"


@mcp.tool()
async def get_network_info() -> str:
    """Get network info: IP addresses, interfaces."""
    try:
        hostname = _run_cmd(["hostname"])
        ip = _run_cmd(["hostname", "-I"])
        interfaces = _run_cmd(["ip", "-br", "addr", "show"])
        gateway = _run_cmd(["ip", "route", "show", "default"])

        lines = [
            f"## 🌐 Network Info — {hostname}",
            f"**IP Addresses:** {ip}",
            f"**Gateway:** {gateway[:200]}",
            "",
            "**Interfaces:**",
            "```",
            interfaces[:1500],
            "```",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting network info: {e}"


@mcp.tool()
async def get_performance_stats() -> str:
    """Get performance metrics from the perf monitor."""
    try:
        result = _run_cmd(
            [sys.executable, str(HOME / "inxotive-office" / "scripts" / "perf_monitor.py"), "dashboard"],
            timeout=15,
        )
        if result and "Error:" not in result:
            try:
                data = json.loads(result)
                lines = ["## ⚡ Performance Stats (24h)", ""]
                if "services" in data.get("data", {}):
                    for svc, stats in data["data"]["services"].items():
                        lines.append(f"  **{svc}:** avg {stats.get('avg_ms','?')}ms, uptime {stats.get('uptime_pct','?')}%")
                else:
                    lines.append(result[:1000])
                return "\n".join(lines)
            except json.JSONDecodeError:
                return result[:2000] if result else "No performance data available."

        # Fallback: check /perf endpoint
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("http://localhost:8888/perf")
                if r.status_code == 200:
                    data = r.json()
                    return json.dumps(data.get("data", data), indent=2)[:2000]
        except Exception:
            pass

        return "Performance monitor not available. Install perf_monitor.py or check localhost:8888/perf."
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    logger.info("Starting INXOTIVE System MCP Server...")
    mcp.run(transport="stdio")
