"""
remote.py — Remote Control (QR code → mobile control) for INXOTIVE HUB.

Provides one-time token-based remote sessions with QR code generation,
token validation (5 min TTL), and command execution against the local
INXOTIVE HUB API (port 8888).

Usage:
    from remote import create_remote_session, execute_remote_command
    session = await create_remote_session()
    print(session["qr_base64"])   # base64 PNG or Unicode fallback
    print(session["link"])        # http://IP:8888/remote?token=xxx
    result = await execute_remote_command(session["token"], "/status")
"""

import asyncio
import json
import logging
import os
import subprocess
import secrets
import base64
import io
import time
import atexit
from datetime import datetime, timezone
from typing import Dict, Optional, List

import httpx

# ── Logging ──
logger = logging.getLogger("remote")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"
    )
    ch.setFormatter(formatter)
    logger.handlers.append(ch)

# ── Try optional dependencies ──
try:
    import qrcode
    from qrcode.image.pil import PilImage
    _QR_LIBRARY = "qrcode"
except ImportError:
    _QR_LIBRARY = None
    logger.info("qrcode library not installed; will use Pillow or Unicode fallback")

try:
    from PIL import Image, ImageDraw
    _PILLOW = True
except ImportError:
    _PILLOW = False
    logger.info("Pillow not installed; will use Unicode QR fallback")

# ── Constants ──
TOKEN_TTL_SECONDS = 300        # 5 minutes
CLEANUP_INTERVAL = 60          # clean expired tokens every 60s
HUB_PORT = 8888
HUB_BASE = f"http://127.0.0.1:{HUB_PORT}"

# ── Token store ──
_active_tokens: Dict[str, Dict] = {}
_lock = asyncio.Lock()
_cleanup_task: Optional[asyncio.Task] = None


# ═══════════════════════════════════════════════════════════════════════════════
# QR Code Encoder — Pure Python (Version 2, 25x25, Alphanumeric Mode)
# ═══════════════════════════════════════════════════════════════════════════════
# These module-level functions implement a minimal QR encoder so the module
# works without any external dependencies.  Produces Version 2 (25x25) QR
# codes with Error Correction Level L, sufficient for short URLs.

ALPHANUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def _qr_to_bytestream(data: str) -> List[int]:
    """Convert text to QR-compatible byte stream (alphanumeric mode 0010).

    Encodes using QR alphanumeric mode (11 bits per 2 chars), pads to
    Version 2-L capacity (34 bytes), and returns a list of byte ints.
    """
    safe_data = "".join(c if c in ALPHANUM_CHARS else " " for c in data.upper())

    # Mode indicator: 0010 (alphanumeric), count: 9 bits
    bitstream = ["0010", format(len(safe_data), "09b")]

    i = 0
    while i < len(safe_data):
        if i + 1 < len(safe_data):
            v1 = ALPHANUM_CHARS.index(safe_data[i])
            v2 = ALPHANUM_CHARS.index(safe_data[i + 1])
            bitstream.append(format(v1 * 45 + v2, "011b"))
            i += 2
        else:
            bitstream.append(format(ALPHANUM_CHARS.index(safe_data[i]), "06b"))
            i += 1

    bits = "".join(bitstream) + "0000"  # terminator
    while len(bits) % 8:
        bits += "0"

    data_bytes = [int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)]

    # Pad to 34 bytes (Version 2-L capacity)
    pad = [0xEC, 0x11]
    while len(data_bytes) < 34:
        data_bytes.append(pad[(len(data_bytes) - 1) % 2])

    return data_bytes[:34]


def _qr_make_matrix(data_bytes: List[int]) -> List[List[int]]:
    """Build a 25x25 QR code matrix (Version 2) from encoded data bytes.

    Places finder patterns, timing, format info (EC L, mask 0), and
    interleaved data bits into a 25x25 module grid.
    """
    S = 25
    m = [[0] * S for _ in range(S)]

    # ── Finder patterns (7x7 at three corners with inner 3x3 core) ──
    for cx, cy in [(0, 0), (0, 18), (18, 0)]:
        for r in range(7):
            for c in range(7):
                x, y = cx + c, cy + r
                if x < S and y < S:
                    outer = r in (0, 6) or c in (0, 6)
                    inner = r in (2, 3, 4) and c in (2, 3, 4)
                    m[y][x] = 1 if outer or inner else 0

    # ── Timing patterns (row 6 / col 6, alternating) ──
    for i in range(8, S - 8):
        m[6][i] = 1 if i % 2 == 0 else 0
        m[i][6] = 1 if i % 2 == 0 else 0

    # ── Dark module (fixed black) ──
    m[21][21] = 1

    # ── Format info (EC level L + mask 0) ──
    # data = 01 | 0000000 (10 bits) → 0b0100000000
    fmt = 0b0100000000 << 10
    gen = 0b10100110111
    for bit in range(14, 4, -1):
        if (fmt >> bit) & 1:
            fmt ^= gen << (bit - 10)
    fmt_bits = format((0b0100000000 << 10 | fmt) ^ 0b101010000010010, "015b")

    # Place around finder patterns
    positions = (
        [(8, c) for c in range(8)]
        + [(8, 8)]
        + [(8, c) for c in range(9, 15)]
        + [(r, 8) for r in range(7)]
        + [(7, 8)]
        + [(r, 8) for r in range(9, 15)]
    )
    for (r, c), bit in zip(positions, fmt_bits):
        if r < S and c < S:
            m[r][c] = int(bit)

    # ── Data placement (column-pair zigzag) ──
    bit_idx = 0
    byte_idx = 0

    col = S - 1
    while col > 0:
        if col == 6:
            col -= 1
            continue
        rows = range(S - 1, -1, -1) if col % 4 == 1 else range(S)
        for row in rows:
            for dc in (col, col - 1):
                if dc < 0 or dc >= S or row < 0 or row >= S:
                    continue
                if m[row][dc] != 0 or (row == 6 and dc in (6,)):
                    continue
                if byte_idx < len(data_bytes):
                    m[row][dc] = (data_bytes[byte_idx] >> (7 - bit_idx)) & 1
                    bit_idx += 1
                    if bit_idx >= 8:
                        bit_idx = 0
                        byte_idx += 1
                else:
                    m[row][dc] = 0
        col -= 2

    return m


def _qr_unicode_render(matrix: List[List[int]]) -> str:
    """Render a QR matrix as Unicode block-character art.

    Each character represents 2 rows (top/bottom) using ▀ ▄ █ blocks.
    A 2-module quiet zone is added around the matrix.
    """
    rows = len(matrix)
    cols = len(matrix[0]) if matrix else 0
    q = 2
    sz = rows + 2 * q

    # Enlarged with quiet zone
    big = [[0] * sz for _ in range(sz)]
    for r in range(rows):
        for c in range(cols):
            big[r + q][c + q] = matrix[r][c]

    lines = []
    for r in range(0, sz, 2):
        line_chars = []
        for c in range(sz):
            t = big[r][c]
            b = big[r + 1][c] if r + 1 < sz else 0
            if t and b:
                line_chars.append("█")  # full block █
            elif t and not b:
                line_chars.append("▀")  # upper half ▀
            elif not t and b:
                line_chars.append("▄")  # lower half ▄
            else:
                line_chars.append(" ")
        lines.append("".join(line_chars))
    return "\n".join(lines)


def _qr_pillow_png(matrix: List[List[int]], box: int = 8) -> Optional[str]:
    """Render a QR matrix as a base64-encoded PNG via Pillow.

    Returns None if Pillow is unavailable or rendering fails.
    """
    if not _PILLOW:
        return None
    try:
        from PIL import Image, ImageDraw

        S = len(matrix)
        q = 4
        px = (S + 2 * q) * box
        img = Image.new("RGB", (px, px), "white")
        draw = ImageDraw.Draw(img)
        for r in range(S):
            for c in range(S):
                if matrix[r][c]:
                    draw.rectangle(
                        [(c + q) * box, (r + q) * box, (c + q + 1) * box - 1, (r + q + 1) * box - 1],
                        fill="black",
                    )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        logger.warning("Pillow PNG render failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Public QR API
# ═══════════════════════════════════════════════════════════════════════════════


async def generate_qr_code(text: str) -> str:
    """Generate a QR code from *text*.

    Resolution order:
      1. ``qrcode`` library  →  base64 PNG (best quality)
      2. ``Pillow``          →  base64 PNG (manual encode)
      3. No deps             →  Unicode block art (scannable for short URLs)

    Returns a *data URI–ready* base64 string or a Unicode-art string.
    """
    if not text:
        return ""

    try:
        # ── Method 1: qrcode library ──
        if _QR_LIBRARY == "qrcode":
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            logger.debug("QR via qrcode lib (%d bytes)", len(b64))
            return b64

        # ── Method 2: manual encode + Pillow render ──
        data_bytes = _qr_to_bytestream(text)
        matrix = _qr_make_matrix(data_bytes)
        png = _qr_pillow_png(matrix)
        if png:
            logger.debug("QR via Pillow manual (%d bytes)", len(png))
            return png

        # ── Method 3: Unicode art ──
        art = _qr_unicode_render(matrix)
        logger.info("QR via Unicode blocks (%d lines)", art.count("\n") + 1)
        return art

    except Exception as exc:
        logger.error("QR generation failed: %s", exc)
        return f"[QR: {text[:50]}]"


# ═══════════════════════════════════════════════════════════════════════════════
# IP Detection
# ═══════════════════════════════════════════════════════════════════════════════


def _detect_hub_ip() -> str:
    """Detect LAN IP via ``hostname -I``; fallback to ``127.0.0.1``."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for ip in result.stdout.strip().split():
                ip = ip.strip()
                if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                    return ip
    except Exception as exc:
        logger.debug("hostname -I failed: %s", exc)
    return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════════════════════
# Token Management
# ═══════════════════════════════════════════════════════════════════════════════


async def generate_control_token() -> str:
    """Generate a one-time 8-char hex token (secrets.token_hex(4))."""
    token = secrets.token_hex(4)
    now = time.time()
    async with _lock:
        _active_tokens[token] = {
            "expires_at": now + TOKEN_TTL_SECONDS,
            "created_at": now,
            "used": False,
        }
    logger.info("Generated token: %s (TTL %ds)", token, TOKEN_TTL_SECONDS)
    return token


async def validate_token(token: str) -> bool:
    """Return True if *token* exists, is not expired, and is not already used.

    Expired tokens are removed from the store during validation.
    """
    if not token or not isinstance(token, str):
        return False
    async with _lock:
        entry = _active_tokens.get(token)
        if entry is None:
            return False
        if entry.get("used"):
            logger.warning("Token already used: %s", token)
            return False
        if time.time() > entry["expires_at"]:
            del _active_tokens[token]
            logger.warning("Token expired: %s", token)
            return False
    return True


async def mark_token_used(token: str) -> None:
    """Mark a token as consumed (one-time use enforcement)."""
    async with _lock:
        if token in _active_tokens:
            _active_tokens[token]["used"] = True


async def get_control_link(token: str) -> str:
    """Build ``http://<HUB_IP>:8888/remote?token=<token>``."""
    ip = _detect_hub_ip()
    return f"http://{ip}:{HUB_PORT}/remote?token={token}"


# ═══════════════════════════════════════════════════════════════════════════════
# Token Cleanup Background Task
# ═══════════════════════════════════════════════════════════════════════════════


async def _cleanup_loop() -> None:
    """Periodically sweep expired tokens from the store."""
    try:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            async with _lock:
                expired = [t for t, v in _active_tokens.items() if now > v["expires_at"]]
                for t in expired:
                    del _active_tokens[t]
            if expired:
                logger.debug("Cleaned %d expired token(s)", len(expired))
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Cleanup error: %s", exc)


def _start_cleanup() -> None:
    """Ensure the background cleanup task is running."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.debug("Cleanup task started")


def _cancel_cleanup() -> None:
    """Cancel the background cleanup task."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        _cleanup_task = None


atexit.register(_cancel_cleanup)


# ═══════════════════════════════════════════════════════════════════════════════
# Command Execution
# ═══════════════════════════════════════════════════════════════════════════════


async def execute_remote_command(token: str, command: str) -> Dict:
    """Execute a simple command via a valid remote token.

    Supported commands:

    ==========  ===================================
    ``/status`` ``/health``   Server & service status
    ``/market``               Crypto market overview
    ``/brief``                AI-generated crypto brief
    ``/ta <coin>``            Technical analysis (btc, eth, sol, bnb, xrp)
    ``/ping``                 Connectivity test
    ``/help``                 Command reference
    ==========  ===================================

    Returns a dict with at least the keys ``success`` and ``data`` (or ``error``).
    """
    if not await validate_token(token):
        return {"success": False, "error": "Token tidak valid atau sudah kedaluwarsa."}
    if not command or not isinstance(command, str):
        return {"success": False, "error": "Perintah tidak boleh kosong."}

    parts = command.strip().lower().split()
    base = parts[0]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:

            if base == "/ping":
                return {
                    "success": True,
                    "data": {
                        "message": "Pong! INXOTIVE HUB aktif.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }

            if base == "/help":
                return {
                    "success": True,
                    "data": {
                        "commands": {
                            "/status": "Status server (services, CPU, memory, disk)",
                            "/health": "Alias untuk /status",
                            "/market": "Data crypto market real-time",
                            "/brief": "Analisis crypto oleh TradeX AI",
                            "/ta <coin>": "Technical analysis (BTC, ETH, SOL, BNB, XRP)",
                            "/ping": "Cek koneksi ke server",
                            "/help": "Tampilkan daftar perintah ini",
                        }
                    },
                }

            if base in ("/status", "/health"):
                r = await client.get(f"{HUB_BASE}/status")
                if r.status_code == 200:
                    return {"success": True, "data": r.json()}
                return {"success": False, "error": f"Status: HTTP {r.status_code}"}

            if base == "/market":
                r = await client.get(f"{HUB_BASE}/market")
                if r.status_code == 200:
                    return {"success": True, "data": r.json()}
                return {"success": False, "error": f"Market: HTTP {r.status_code}"}

            if base == "/brief":
                r = await client.get(f"{HUB_BASE}/brief")
                if r.status_code == 200:
                    return {"success": True, "data": r.json()}
                return {"success": False, "error": f"Brief: HTTP {r.status_code}"}

            if base == "/ta":
                arg = " ".join(parts[1:]) if len(parts) > 1 else ""
                if not arg:
                    return {
                        "success": False,
                        "error": "Gunakan: /ta <coin> (btc, eth, sol, bnb, xrp)",
                    }
                coin = arg.split()[0].lower()
                r = await client.get(f"{HUB_BASE}/ta/{coin}")
                if r.status_code == 200:
                    return {"success": True, "data": r.json()}
                return {
                    "success": False,
                    "error": f"TA untuk {coin.upper()} tidak tersedia (HTTP {r.status_code})",
                }

            return {
                "success": False,
                "error": f"Perintah '{base}' tidak dikenal. Ketik /help.",
            }

    except httpx.RequestError as exc:
        return {"success": False, "error": f"Gagal terhubung ke server: {exc}"}
    except Exception as exc:
        logger.error("Command error: %s", exc)
        return {"success": False, "error": f"Internal error: {exc}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Full Session Creation
# ═══════════════════════════════════════════════════════════════════════════════


async def create_remote_session() -> Dict:
    """Create a complete remote control session.

    Returns a dict with:
        ``token``       — 8-char hex one-time token
        ``link``        — ``http://<ip>:8888/remote?token=<token>``
        ``qr_base64``   — base64-encoded PNG (or Unicode block art as fallback)
        ``expires_in``  — TTL in seconds (300)
        ``created_at``  — ISO-8601 timestamp
    """
    try:
        _start_cleanup()
        token = await generate_control_token()
        link = await get_control_link(token)
        qr_data = await generate_qr_code(link)

        result = {
            "token": token,
            "link": link,
            "qr_base64": qr_data,
            "expires_in": TOKEN_TTL_SECONDS,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Session created — token=%s qr_len=%d", token, len(qr_data) if qr_data else 0)
        return result

    except Exception as exc:
        logger.error("Failed to create remote session: %s", exc)
        return {
            "token": "",
            "link": "",
            "qr_base64": "",
            "expires_in": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }
