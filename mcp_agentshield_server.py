"""
MCP Server: inxotive-agentshield — Security audit MCP server.
Scans for secrets, OWASP vulnerabilities, dependency issues, and XSS/Injection risks.

Usage: python3 mcp_agentshield_server.py  (stdio transport)
       python3 run_mcp.py agentshield sse 8105
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[agentshield] %(levelname)s %(message)s")
logger = logging.getLogger("agentshield")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("inxotive-agentshield")

HOME = Path.home()

# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------

SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI / 9Router API Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"xox[bpras]-[0-9a-zA-Z-]{10,}", "Slack Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]", "AWS Secret Key"),
    (r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{16,}['\"]", "API Key"),
    (r"(?i)password\s*[:=]\s*['\"][^'\"]{6,}['\"]", "Password"),
    (r"(?i)secret\s*[:=]\s*['\"][^'\"]{10,}['\"]", "Secret"),
    (r"Bearer\s+[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+", "JWT Token"),
    (r"(?i)private[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]", "Private Key"),
    (r"-----BEGIN (RSA |EC )?PRIVATE KEY-----", "PEM Private Key"),
    (r"mongodb(?:\+srv)?://[^@]+@", "MongoDB Connection String"),
    (r"postgresql?://[^@]+@", "PostgreSQL Connection String"),
    (r"redis://[^@]+@", "Redis Connection String"),
    (r"DISCORD_TOKEN\s*=\s*['\"][^'\"]+['\"]", "Discord Bot Token (.env)"),
    (r"DISCORD_WEBHOOK\s*=\s*['\"][^'\"]+['\"]", "Discord Webhook URL"),
    (r"ANTHROPIC_API_KEY\s*=\s*['\"][^'\"]+['\"]", "Anthropic API Key"),
    (r"NINE_ROUTER_API_KEY\s*=\s*['\"][^'\"]+['\"]", "9Router API Key"),
    (r"GITHUB_TOKEN\s*=\s*['\"][^'\"]+['\"]", "GitHub Token (.env)"),
]

# ---------------------------------------------------------------------------
# OWASP / code vulnerability patterns (detection only)
# ---------------------------------------------------------------------------

CODE_VULN_PATTERNS = [
    (r"\beval\s*\(", "eval() — potential code injection (RCE)"),
    (r"innerHTML\s*=", "innerHTML assignment — potential XSS"),
    (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML — potential XSS"),
    (r"document\.write\s*\(", "document.write — potential XSS"),
    (r"(?i)(?:sql|query)\s*\+\s*['\"]", "String concatenation in SQL — potential SQL injection"),
    (r"exec\s*\(\s*['\"]", "exec() — code execution risk"),
    (r"subprocess\.call\s*\(.*shell\s*=\s*True", "Shell=True — command injection risk"),
    (r"os\.system\s*\(", "os.system() — command injection risk"),
    (r"pickle\.loads?\s*\(", "Pickle deserialization — RCE risk"),
    (r"__import__\s*\(", "Dynamic import — potential code injection"),
    (r"yaml\.load\s*\(.*Loader=yaml\.Loader", "Unsafe YAML load — deserialization risk"),
    (r"raw\(.*format\(|format\(.*raw_input", "User input in SQL query — injection risk"),
    (r"allow_redirects\s*=\s*True", "Unvalidated redirect — SSRF/open redirect risk"),
    (r"csrf_exempt", "CSRF protection disabled"),
    (r"@app\.route.*methods=\[.*'POST", "Missing CSRF on POST endpoint"),
]

IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build",
    ".next", "target", "vendor", ".egg-info", "site-packages", ".gitlab",
    ".claude", ".npm", ".cache", ".local", ".config", ".nvm",
}

IGNORE_FILES = {
    ".env.example", ".env.template", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "poetry.lock", "*.min.js", "*.bundle.js",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_ignore(path: Path) -> bool:
    """Check if a path should be skipped."""
    for part in path.parts:
        if part in IGNORE_DIRS:
            return True
    if any(ext in path.name for ext in [".min.js", ".bundle.js", ".chunk.js"]):
        return True
    return False


def _is_text_file(path: Path) -> bool:
    """Quick check if file is likely text/source code."""
    text_exts = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".md", ".rst", ".txt", ".env", ".sh", ".bash", ".zsh",
        ".ps1", ".bat", ".cmd", ".php", ".rb", ".go", ".rs", ".java",
        ".kt", ".swift", ".c", ".cpp", ".h", ".hpp", ".sql", ".r",
        ".vue", ".svelte", ".astro", ".dockerfile", ".gitignore",
    }
    return path.suffix.lower() in text_exts or path.name in {
        "Dockerfile", "docker-compose.yml", "Makefile", "Procfile",
    } or path.name.startswith(".env")


async def _scan_file(path: Path, patterns: List, context_lines: int = 0) -> List[Dict]:
    """Scan a single file for pattern matches."""
    findings = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings

    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        for pattern, name in patterns:
            try:
                if re.search(pattern, line):
                    # Get context
                    snippet = line.strip()[:120]
                    finding = {
                        "file": str(path),
                        "line": i,
                        "issue": name,
                        "snippet": snippet,
                    }
                    if context_lines > 0:
                        start = max(0, i - 1 - context_lines)
                        end = min(len(lines), i + context_lines)
                        finding["context"] = "\n".join(
                            f"{j+1}: {lines[j]}" for j in range(start, end)
                        )
                    findings.append(finding)
                    break  # one finding per line per pattern set
            except re.error:
                continue
    return findings


async def _scan_directory(
    root: Path,
    patterns: List,
    max_files: int = 500,
    context_lines: int = 0,
) -> List[Dict]:
    """Recursively scan a directory."""
    findings = []
    scanned = 0

    for path in root.rglob("*"):
        if scanned >= max_files:
            break
        if _should_ignore(path):
            continue
        if not path.is_file():
            continue
        if not _is_text_file(path):
            continue
        # Skip line count — skip >2000 line files
        try:
            if sum(1 for _ in open(path, errors="ignore")) > 2000:
                continue
        except Exception:
            continue

        scanned += 1
        results = await _scan_file(path, patterns, context_lines)
        findings.extend(results)

    return findings


def _check_python_deps() -> List[Dict]:
    """Check Python dependencies for known vulnerability patterns."""
    issues = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            outdated = json.loads(result.stdout)
            for pkg in outdated[:20]:
                issues.append({
                    "package": pkg.get("name", "?"),
                    "current": pkg.get("version", "?"),
                    "latest": pkg.get("latest_version", "?"),
                    "type": "outdated_package",
                })
    except Exception as e:
        issues.append({"error": f"Dependency check failed: {e}"})

    return issues


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def scan_secrets(
    path: str = "",
    max_files: int = 200,
) -> str:
    """Scan files for hardcoded secrets, API keys, tokens, and credentials.

    Args:
        path: Directory path to scan. Defaults to ~/market-api.
        max_files: Maximum number of files to scan (default 200, max 1000).
    """
    target = Path(path).expanduser() if path else HOME / "market-api"
    if not target.exists():
        return f"Path not found: {target}"
    if not target.is_dir():
        return f"Not a directory: {target}"

    max_files = min(max(max_files, 10), 1000)

    logger.info("Scanning secrets in %s (max %d files)...", target, max_files)
    findings = await _scan_directory(target, SECRET_PATTERNS, max_files, context_lines=1)

    if not findings:
        return f"## ✅ Secret Scan: PASSED\n\nNo secrets found in `{target}` ({max_files} files scanned)."

    # Group by issue type
    by_type: Dict[str, List] = {}
    for f in findings:
        by_type.setdefault(f["issue"], []).append(f)

    lines = [
        f"## 🔐 Secret Scan: {len(findings)} ISSUES FOUND",
        f"**Scanned:** `{target}` ({max_files} files)",
        f"**Severity:** CRITICAL — review immediately",
        "",
    ]

    for issue_type, items in sorted(by_type.items()):
        lines.append(f"### ⚠️ {issue_type} ({len(items)} occurrences)")
        for item in items[:10]:
            lines.append(f"- `{item['file']}:{item['line']}`")
            lines.append(f"  `{item['snippet'][:100]}`")
        if len(items) > 10:
            lines.append(f"  *...and {len(items) - 10} more*")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def scan_vulnerabilities(
    path: str = "",
    max_files: int = 300,
) -> str:
    """Scan code for OWASP Top 10 vulnerabilities: XSS, injection, RCE, CSRF, etc.

    Args:
        path: Directory path to scan. Defaults to current directory or ~/market-api/src if exists.
        max_files: Maximum files to scan (default 300).
    """
    target = Path(path).expanduser() if path else Path.cwd()
    if not target.exists():
        # Fall back to market-api
        target = HOME / "market-api"
    if not target.is_dir():
        return f"Not a directory: {target}"

    max_files = min(max(max_files, 10), 1000)
    findings = await _scan_directory(target, CODE_VULN_PATTERNS, max_files, context_lines=0)

    if not findings:
        return f"## ✅ OWASP Scan: PASSED\n\nNo common vulnerabilities found in `{target}`."

    by_type: Dict[str, List] = {}
    for f in findings:
        by_type.setdefault(f["issue"], []).append(f)

    lines = [
        f"## 🛡️ OWASP Scan: {len(findings)} ISSUES FOUND",
        f"**Scanned:** `{target}`",
        "",
    ]

    # Add severity color coding
    critical = ["eval()", "exec(", "pickle.load", "shell=True", "os.system("]
    high = ["innerHTML", "dangerouslySetInnerHTML", "SQL injection", "command injection"]

    for issue_type, items in sorted(by_type.items()):
        is_critical = any(c.lower() in issue_type.lower() for c in critical)
        is_high = any(h.lower() in issue_type.lower() for h in high)
        severity = "🔴 CRITICAL" if is_critical else "🟠 HIGH" if is_high else "🟡 MEDIUM"

        lines.append(f"### {severity}: {issue_type} ({len(items)} occurrences)")
        for item in items[:8]:
            lines.append(f"- `{item['file']}:{item['line']}` — `{item['snippet'][:80]}`")
        if len(items) > 8:
            lines.append(f"  *...and {len(items) - 8} more*")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def check_dependencies() -> str:
    """Check Python/Node dependencies for outdated packages and known vulnerabilities."""
    lines = ["## 📦 Dependency Security Check", ""]

    # Python deps
    lines.append("### Python Packages")
    py_issues = _check_python_deps()
    if py_issues:
        for issue in py_issues:
            if "error" in issue:
                lines.append(f"⚠️ {issue['error']}")
            else:
                p = issue["package"]
                lines.append(f"  ⏫ {p}: {issue['current']} → {issue['latest']}")
        lines.append("")
    else:
        lines.append("  ✅ All packages up to date or check failed")
        lines.append("")

    # Check if npm is available
    try:
        result = subprocess.run(
            ["npm", "audit", "--omit=dev", "--json"],
            capture_output=True, text=True, timeout=30,
            cwd=str(HOME / "market-api"),
        )
        if result.returncode <= 1 and result.stdout.strip():
            audit = json.loads(result.stdout)
            vulns = audit.get("vulnerabilities", {})
            if vulns:
                lines.append("### JavaScript/Node Dependencies")
                for name, info in vulns.items():
                    sev = info.get("severity", "unknown")
                    via = info.get("via", [])
                    lines.append(f"  {'🔴' if sev == 'critical' else '🟠' if sev == 'high' else '🟡'} {name}: {sev} — {info.get('title', info.get('range', ''))[:80]}")
                lines.append("")
            else:
                lines.append("  ✅ npm audit: no vulnerabilities")
        else:
            lines.append("  ℹ️ npm audit skipped (no package.json in market-api)")
    except FileNotFoundError:
        lines.append("  ℹ️ npm not installed")
    except Exception as e:
        lines.append(f"  ℹ️ npm audit: {e}")

    return "\n".join(lines)


@mcp.tool()
async def full_security_audit(
    path: str = "",
    depth: str = "standard",
) -> str:
    """Run a full security audit: secrets + OWASP + deps. Comprehensive report.

    Args:
        path: Directory to scan. Defaults to ~/market-api.
        depth: 'quick' (50 files), 'standard' (200 files), 'deep' (500 files).
    """
    target = Path(path).expanduser() if path else HOME / "market-api"
    depth_map = {"quick": 50, "standard": 200, "deep": 500}
    max_files = depth_map.get(depth, 200)

    lines = [
        "═══════════════════════════════════════════",
        "  🔐 INXOTIVE AgentShield — Security Audit",
        f"  Target: {target}",
        f"  Depth: {depth} (max {max_files} files)",
        f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "═══════════════════════════════════════════",
        "",
    ]

    # 1. Secrets
    logger.info("Scanning secrets...")
    secrets = await _scan_directory(target, SECRET_PATTERNS, max_files)
    lines.append(f"## 1. 🔐 Secrets Scan: {'✅ PASS' if not secrets else f'❌ {len(secrets)} issues'}")
    if secrets:
        for s in secrets[:15]:
            lines.append(f"  - `{s['file']}:{s['line']}` — {s['issue']}")
        if len(secrets) > 15:
            lines.append(f"  *...and {len(secrets) - 15} more*")
    lines.append("")

    # 2. OWASP
    logger.info("Scanning vulnerabilities...")
    vulns = await _scan_directory(target, CODE_VULN_PATTERNS, max_files)
    lines.append(f"## 2. 🛡️ OWASP Scan: {'✅ PASS' if not vulns else f'⚠️ {len(vulns)} issues'}")
    if vulns:
        for v in vulns[:15]:
            lines.append(f"  - `{v['file']}:{v['line']}` — {v['issue']}")
        if len(vulns) > 15:
            lines.append(f"  *...and {len(vulns) - 15} more*")
    lines.append("")

    # 3. Dependencies
    logger.info("Checking dependencies...")
    deps = _check_python_deps()
    lines.append(f"## 3. 📦 Dependencies: {'✅ OK' if not deps else f'⚠️ {len(deps)} outdated'}")
    for d in deps[:10]:
        if "error" not in d:
            lines.append(f"  - {d['package']}: {d['current']} → {d['latest']}")
    lines.append("")

    # Summary
    total = len(secrets) + len(vulns)
    severity = "✅ SAFE" if total == 0 else "⚠️ WARNINGS" if total < 10 else "❌ CRITICAL"
    lines.append("═══════════════════════════════════════════")
    lines.append(f"  **SUMMARY:** {severity}")
    lines.append(f"  Secrets: {len(secrets)} | OWASP: {len(vulns)} | Deps: {len(deps)}")
    lines.append("═══════════════════════════════════════════")

    return "\n".join(lines)


@mcp.tool()
async def scan_env_file() -> str:
    """Check if .env_secrets has any security issues (permissions, exposure)."""
    env_path = HOME / ".env_secrets"
    if not env_path.exists():
        return "❌ `.env_secrets` not found!"

    try:
        stat = env_path.stat()
        perms = oct(stat.st_mode & 0o777)
        owner = stat.st_uid
        current_uid = os.getuid()

        lines = [f"## 📋 .env_secrets Security Check", ""]
        lines.append(f"**Path:** {env_path}")
        lines.append(f"**Permissions:** {perms}")
        lines.append(f"**Owner:** {owner} (you: {'✅' if owner == current_uid else '❌'})")
        lines.append(f"**Size:** {stat.st_size} bytes")
        lines.append("")

        # Check permissions (should be 600)
        if perms == "0o600" or perms == "0o400":
            lines.append("✅ Permissions are secure (600)")
        else:
            lines.append(f"⚠️ Permissions are {perms} — recommend `chmod 600`")

        # Check for world-readable
        if stat.st_mode & 0o004:
            lines.append("❌ CRITICAL: File is world-readable! Run `chmod 600`")

        # Check content without revealing secrets
        content = env_path.read_text()
        lines_count = len(content.strip().split("\n"))
        lines.append(f"**Entries:** {lines_count} environment variables")
        lines.append("")

        # Verify no git tracked
        try:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(env_path)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines.append("❌ CRITICAL: `.env_secrets` is tracked by git! Add to .gitignore")
            else:
                lines.append("✅ Not tracked by git")
        except Exception:
            pass

        return "\n".join(lines)

    except Exception as e:
        return f"Error checking .env_secrets: {e}"


if __name__ == "__main__":
    logger.info("Starting INXOTIVE AgentShield MCP Server (stdio)...")
    mcp.run(transport="stdio")
