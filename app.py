from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import requests
import pandas as pd
import ta
from datetime import datetime
import math, json, os, sys, threading, time
from pathlib import Path

HOME = str(Path.home())

sys.path.insert(0, str(Path.home() / "inxotive-office" / "discord_bot"))
sys.path.insert(0, str(Path.home() / "market-api"))

# ── YouTube Service ──
from youtube_service import (
    init_youtube,
    is_youtube_url,
    extract_youtube_id,
    extract_playlist_id,
    fetch_video_info,
    extract_transcript_async,
    fetch_youtube_comments,
    search_youtube,
    fetch_channel_videos,
    download_audio,
    transcribe_youtube,
    analyze_youtube_video,
    fetch_playlist,
    format_transcript_for_context,
    format_comments_for_context,
    index_to_knowledge_base,
    YOUTUBE_INSTRUCTION_PROMPT,
)
# ── Load env secrets ──
_env_secrets = Path.home() / ".env_secrets"
if _env_secrets.exists():
    for line in _env_secrets.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import uuid
import asyncio
import httpx
import shlex
from fastapi.responses import StreamingResponse

SESSIONS_FILE = Path.home() / ".hub_sessions.json"

def load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try: return json.loads(SESSIONS_FILE.read_text())
        except: pass
    return {}

def save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2, ensure_ascii=False))

app = FastAPI()

# Init YouTube service at startup
init_youtube()

# ── MCP Client ──
from mcp_client import mcp_manager, init_mcp, format_tool_result_for_context, format_tool_list_for_context

# ── Autodream (Memory Consolidation) ──
from autodream import consolidate_memory, daily_consolidate, generate_usage_insights, format_consolidate_report

# ── Visuals (Inline Charts) ──
from visuals import render_chart

# ── File Generation (Excel/PDF) ──
from filegen import generate_excel, generate_pdf, generate_invoice

# ── Channels (Discord/Telegram 2-way) ──
from channels import send_to_discord, send_to_telegram, broadcast, process_discord_message, handle_discord_command

# ── Verification Loop ──
from verify import verify_endpoint, verify_multiple, run_verification_loop, format_verification_report

# ── Agent Teams (A2A) ──
from agent_teams import agent_team_manager

# ── Remote Control ──
from remote import generate_control_token, validate_token, get_control_link, execute_remote_command, create_remote_session, _detect_hub_ip

# Init MCP at startup (async, best-effort)
@app.on_event("startup")
async def startup_mcp_init():
    """Init MCP with 10s timeout — dont block server startup."""
    try:
        await asyncio.wait_for(init_mcp(), timeout=15)
    except asyncio.TimeoutError:
        print("[MCP] Init timed out (10s) — continuing without MCP", flush=True)
    except Exception as e:
        print(f"[MCP] Init error: {e}", flush=True)
    # Also login to Odysseus for proxy
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post("http://localhost:7000/api/auth/login", json={
                "username": "admin", "password": "admin"
            })
            if r.status_code < 400:
                _ODYSSEUS_SESSION["cookie"] = r.headers.get("set-cookie", "")
                _ODYSSEUS_SESSION["expires"] = time.time() + 3600
    except Exception as e:
        print(f"[Odysseus] Login error: {e}", flush=True)

# ── Event Bus ──
EVENT_BUS = Path.home() / ".event_bus.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK", "")

def push_event(source: str, event_type: str, message: str, severity: str = "info"):
    """Push event ke bus — semua component bisa subscribe."""
    events = []
    if EVENT_BUS.exists():
        try: events = json.loads(EVENT_BUS.read_text())
        except: pass
    events.append({
        "time": datetime.now().isoformat(),
        "source": source,
        "type": event_type,
        "message": message,
        "severity": severity,
    })
    events = events[-100:]  # Keep last 100
    EVENT_BUS.write_text(json.dumps(events, indent=2))

def alert_all(source: str, message: str, severity: str = "info"):
    """Route alert ke semua channel: Discord + event bus."""
    push_event(source, "alert", message, severity)
    if severity in ("critical", "warning") and WEBHOOK_URL:
        try:
            emoji = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
            e = emoji.get(severity, "ℹ️")
            payload = {"content": f"{e} **[{source}]** {message[:1800]}"}
            threading.Thread(target=lambda: requests.post(WEBHOOK_URL, json=payload, timeout=10), daemon=True).start()
        except: pass
    # Also log to file
    try:
        log_path = Path.home() / "logs" / "events.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"[{severity.upper()}] [{source}] {message}\n")
    except OSError:
        pass

def safe_float(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(float(val), 4)

def get_technical_analysis(coin_id: str):
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=30&interval=daily",
            timeout=15
        )
        data = r.json()
        if "prices" not in data or len(data.get("prices", [])) < 14:
            return {"error": f"CoinGecko data unavailable for {coin_id}"}
        prices = [p[1] for p in data["prices"]]
        volumes = [v[1] for v in data["total_volumes"]]
        df = pd.DataFrame({"close": prices, "volume": volumes})
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd = ta.trend.MACD(df["close"])
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_diff"] = macd.macd_diff()
        bb = ta.volatility.BollingerBands(df["close"], window=20)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_middle"] = bb.bollinger_mavg()
        df["ema20"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
        df["ema50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()
        df = df.fillna(0)
        last = df.iloc[-1]
        rsi = safe_float(last["rsi"])
        rsi_signal = "OVERSOLD" if rsi and rsi < 30 else "OVERBOUGHT" if rsi and rsi > 70 else "NEUTRAL"
        macd_diff = safe_float(last["macd_diff"])
        macd_signal = "BULLISH" if macd_diff and macd_diff > 0 else "BEARISH"
        ema20 = safe_float(last["ema20"])
        ema50 = safe_float(last["ema50"])
        trend = "BULLISH" if ema20 and ema50 and ema20 > ema50 else "BEARISH"
        return {
            "rsi": rsi,
            "rsi_signal": rsi_signal,
            "macd": safe_float(last["macd"]),
            "macd_histogram": macd_diff,
            "macd_signal": macd_signal,
            "bb_upper": safe_float(last["bb_upper"]),
            "bb_lower": safe_float(last["bb_lower"]),
            "bb_middle": safe_float(last["bb_middle"]),
            "ema20": ema20,
            "ema50": ema50,
            "trend": trend,
            "support": round(min(prices[-14:]), 2),
            "resistance": round(max(prices[-14:]), 2),
            "change_7d": round((prices[-1]/prices[-7]-1)*100, 2) if len(prices) >= 7 and prices[-7] else 0,
            "change_30d": round((prices[-1]/prices[0]-1)*100, 2) if prices[0] else 0
        }
    except Exception as e:
        return {"error": str(e)}

_MARKET_CACHE = None
_LAST_MARKET_FETCH = 0

def get_market_data():
    global _MARKET_CACHE, _LAST_MARKET_FETCH
    import time
    now = time.time()
    if _MARKET_CACHE and (now - _LAST_MARKET_FETCH) < 30:
        _MARKET_CACHE["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        return _MARKET_CACHE
    coins = ["bitcoin","ethereum","binancecoin","solana","ripple"]
    crypto_data = {}
    fg_data = {"value": "N/A"}
    trending_str = ""
    sentiment_str = ""
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(coins)}&vs_currencies=usd,idr&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true",
            timeout=10
        )
        if r.ok:
            crypto_data = r.json()
        else:
            # Use cached or fallback
            if _MARKET_CACHE and _MARKET_CACHE.get("crypto"):
                crypto_data = _MARKET_CACHE["crypto"]
    except:
        if _MARKET_CACHE and _MARKET_CACHE.get("crypto"):
            crypto_data = _MARKET_CACHE["crypto"]
    try:
        fg = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json()
        if "data" in fg and len(fg["data"]) > 0:
            fg_data = fg["data"][0]
    except:
        if _MARKET_CACHE and _MARKET_CACHE.get("fear_greed"):
            fg_data = _MARKET_CACHE["fear_greed"]
    try:
        r2 = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if r2.ok:
            trending = [c["item"]["name"] for c in r2.json().get("coins", [])[:5]]
            trending_str = "TRENDING: " + ", ".join(trending)
    except:
        trending_str = _MARKET_CACHE.get("trending", "") if _MARKET_CACHE else ""
    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M WIB"),
        "crypto": crypto_data,
        "fear_greed": fg_data,
        "trending": trending_str,
        "sentiment_trend": sentiment_str
    }
    if crypto_data:  # Only cache if we got real data
        _MARKET_CACHE = result
        _LAST_MARKET_FETCH = now
    return result

@app.get("/market")
def get_market():
    try:
        return get_market_data()
    except Exception as e:
        return {"error": str(e)}

@app.get("/ta/{coin}")
def get_ta(coin: str):
    coin_map = {
        "btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin",
        "sol": "solana", "xrp": "ripple"
    }
    coin_id = coin_map.get(coin.lower(), coin.lower())
    return {"coin": coin.upper(), "technical": get_technical_analysis(coin_id)}

@app.get("/brief")
def get_brief():
    try:
        market = get_market_data()
        btc = market["crypto"].get("bitcoin", {})
        eth = market["crypto"].get("ethereum", {})
        fg = market["fear_greed"]
        btc_ta = get_technical_analysis("bitcoin")
        eth_ta = get_technical_analysis("ethereum")
        # Get news
        news_data = []
        try:
            import xml.etree.ElementTree as ET
            r = requests.get("https://cointelegraph.com/rss", timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            items = root.findall(".//item")[:5]
            for item in items:
                title = item.find("title")
                if title is not None and title.text:
                    import re
                    clean = re.sub(r'<[^>]+>', '', title.text).strip()
                    news_data.append(f"• {clean}")
        except:
            pass
        news_str = "\n".join(news_data) if news_data else "News tidak tersedia"

        prompt = f"""Kamu TradeX analis crypto profesional INXOTIVE. Analisis MENDALAM dalam Bahasa Indonesia.

DATA REAL-TIME [{market['timestamp']}]
BTC: ${btc.get('usd',0):,.0f} | 24h: {btc.get('usd_24h_change',0):+.2f}% | Vol: ${btc.get('usd_24h_vol',0)/1e9:.1f}B
ETH: ${eth.get('usd',0):,.0f} | 24h: {eth.get('usd_24h_change',0):+.2f}%
Fear & Greed: {fg.get('value')} — {fg.get('value_classification')}
{market['trending']}
{market['sentiment_trend']}

TECHNICAL BTC:
RSI: {btc_ta.get('rsi')} ({btc_ta.get('rsi_signal')}) | MACD: {btc_ta.get('macd_signal')} | Trend: {btc_ta.get('trend')}
BB Upper: ${btc_ta.get('bb_upper'):,} | Lower: ${btc_ta.get('bb_lower'):,}
EMA20: ${btc_ta.get('ema20'):,} | EMA50: ${btc_ta.get('ema50'):,}
Support: ${btc_ta.get('support'):,} | Resistance: ${btc_ta.get('resistance'):,}
7d: {btc_ta.get('change_7d')}% | 30d: {btc_ta.get('change_30d')}%

TECHNICAL ETH:
RSI: {eth_ta.get('rsi')} ({eth_ta.get('rsi_signal')}) | MACD: {eth_ta.get('macd_signal')} | Trend: {eth_ta.get('trend')}
BB Upper: ${eth_ta.get('bb_upper'):,} | Lower: ${eth_ta.get('bb_lower'):,}
Support: ${eth_ta.get('support'):,} | Resistance: ${eth_ta.get('resistance'):,}

FORMAT ANALISIS:
📊 KONDISI PASAR
🔍 ANALISIS TEKNIKAL BTC
🔍 ANALISIS TEKNIKAL ETH
📰 SENTIMENT & CATALYST
🎯 LEVEL KUNCI & TRADING PLAN
💡 REKOMENDASI (BUY/HOLD/SELL dengan entry, target, SL)
⚠️ RISK MANAGEMENT
📋 DISCLAIMER"""
        return {"prompt": prompt}
    except Exception as e:
        return {"error": str(e)}

@app.get("/news")
def get_news():
    """Ambil berita crypto dari RSS feeds gratis"""
    news = []
    feeds = [
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/",
    ]
    try:
        import xml.etree.ElementTree as ET
        for feed_url in feeds:
            r = requests.get(feed_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            items = root.findall(".//item")[:5]
            for item in items:
                title = item.find("title")
                desc = item.find("description")
                link = item.find("link")
                news.append({
                    "title": title.text if title is not None else "",
                    "description": (desc.text or "")[:200] if desc is not None else "",
                    "url": link.text if link is not None else "",
                    "source": feed_url.split("/")[2]
                })
            if len(news) >= 8:
                break
    except Exception as e:
        news.append({"error": str(e)})
    return {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M WIB"), "news": news[:8]}

@app.get("/self-config")
async def self_config_endpoint():
    """Show auto-detected resource config."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cfg", str(Path.home() / "inxotive-office" / "scripts" / "self_config.py"))
    if spec and spec.loader:
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.load_config()
    return {"error": "self_config not found"}

@app.get("/intake", response_class=HTMLResponse)
async def intake_form():
    path = Path.home() / "inxotive-office" / "intake_form.html"
    if path.exists():
        return path.read_text()
    return HTMLResponse("<h1>Form not found</h1>", status_code=404)

@app.post("/intake")
async def intake_submit(data: dict):
    try:
        nama_usaha = data.get('nama_usaha', 'Unknown')
        msg = f"**📋 LEAD BARU — INTAKE KLIEN**\n\n"
        msg += f"**Usaha:** {nama_usaha}\n"
        msg += f"**Pemilik:** {data.get('nama_pemilik','-')}\n"
        msg += f"**WA:** {data.get('whatsapp','-')}\n"
        msg += f"**Email:** {data.get('email','-')}\n"
        msg += f"**Alamat:** {data.get('alamat','-')}\n"
        msg += f"**Jenis:** {data.get('jenis_usaha','-')}\n"
        msg += f"**Paket:** {data.get('paket','-')}\n"
        msg += f"**Catatan:** {data.get('catatan','-')}"
        alert_all("intake", f"Lead baru: {nama_usaha} ({data.get('paket','-')})", "info")
        with open(Path.home() / "inxotive-office" / "leads.json", "a") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(), **data}) + "\n")
        # Trigger onboarding pipeline in background
        import threading
        def run_onboard():
            try:
                import subprocess, sys
                script = str(Path.home() / "inxotive-office" / "scripts" / "onboard_client.py")
                result = subprocess.run(
                    [sys.executable, script, nama_usaha],
                    capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0:
                    print(f"[ONBOARD] ✅ {nama_usaha}: {result.stdout[:200]}", flush=True)
                else:
                    print(f"[ONBOARD] ❌ {nama_usaha}: {result.stderr[:200]}", flush=True)
            except Exception as e:
                print(f"[ONBOARD] Error: {e}", flush=True)
        threading.Thread(target=run_onboard, daemon=True).start()
        return {"status": "ok", "nama_usaha": nama_usaha, "onboarding": "started"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/knowledge")
async def knowledge(q: str = "", agent: str = ""):
    if not q:
        return {"error": "Parameter 'q' wajib diisi"}
    try:
        from rag import search_unified
        results = search_unified(q, agent=agent if agent else None, top_k=5)
        return {"query": q, "count": len(results), "results": results}
    except Exception as e:
        return {"query": q, "error": str(e), "results": []}

@app.get("/knowledge/query")
async def knowledge_query(q: str = "", agent: str = ""):
    if not q:
        return {"error": "Parameter 'q' wajib diisi"}
    try:
        from rag import query_rag_unified
        answer = query_rag_unified(q, agent=agent if agent else None)
        return {"query": q, "answer": answer}
    except Exception as e:
        return {"query": q, "error": str(e)}

@app.get("/leads")
async def get_leads():
    path = Path.home() / "inxotive-office" / "leads.json"
    if not path.exists():
        return {"leads": []}
    lines = path.read_text().strip().split("\n")
    leads = [json.loads(l) for l in lines if l.strip()]
    return {"count": len(leads), "leads": leads[-20:]}

def _status_blocking():
    import shutil, os, subprocess
    disk = shutil.disk_usage("/")
    cpu = 0
    try:
        load = os.getloadavg()
        ncpu = os.cpu_count() or 1
        cpu = round((load[0] / ncpu) * 100, 1)
    except Exception: pass
    mem_total = mem_used = 0
    try:
        r = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                mem_total = int(parts[1])
                mem_used = int(parts[2])
    except Exception: pass
    uptime = "0"
    try:
        with open("/proc/uptime") as f:
            uptime = f.read().split()[0]
    except Exception: pass
    return disk, cpu, mem_total, mem_used, uptime


@app.get("/status")
async def status():
    services = {
        "bot": (8080, "/"),
        "odysseus": (7000, "/"),
        "ollama": (11434, "/api/tags"),
        "casaos": (80, "/"),
    }
    checks = {}
    blocking_task = asyncio.create_task(asyncio.to_thread(_status_blocking))
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            async def chk(name, port, path):
                try:
                    r = await client.get(f"http://localhost:{port}{path}")
                    return name, ("up" if r.status_code < 500 else "down")
                except Exception:
                    return name, "down"
            results = await asyncio.gather(*[chk(n, p, pa) for n, (p, pa) in services.items()])
            checks = dict(results)
    except Exception:
        checks = {n: "down" for n in services}
    disk, cpu, mem_total, mem_used, uptime = await blocking_task
    return {
        "services": checks,
        "disk": f"{disk.free // (2**30)}GB free of {disk.total // (2**30)}GB total",
        "uptime": uptime,
        "system": {
            "cpu": f"{cpu}",
            "memory": f"{mem_used}/{mem_total}",
            "disk": f"{disk.free // (2**30)}GB"
        }
    }

import tempfile

@app.post("/transcribe")
async def transcribe_audio(request: Request):
    """Transcribe audio file using Whisper."""
    from fastapi import UploadFile, File
    import subprocess
    try:
        body = await request.body()
        ext = ".webm"
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.write(body)
        tmp.close()
        # Try using whisper CLI
        result = subprocess.run(
            ["whisper", tmp.name, "--model", "small", "--language", "id", "--output_format", "json"],
            capture_output=True, text=True, timeout=60
        )
        os.unlink(tmp.name)
        if result.returncode == 0:
            import json as j
            data = j.loads(result.stdout)
            return {"text": data.get("text", "")}
        return {"text": ""}
    except Exception as e:
        return {"text": "", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# YouTube Power Module — Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/youtube/search")
async def youtube_search(data: dict):
    """Search YouTube videos."""
    query = data.get("query", "")
    max_results = min(data.get("max_results", 10), 50)
    if not query:
        return {"success": False, "error": "Query is required", "results": []}
    results = await search_youtube(query, max_results)
    return results


@app.get("/api/youtube/info/{video_id}")
async def youtube_info(video_id: str):
    """Get detailed video information."""
    info = await fetch_video_info(video_id)
    if not info.get("success"):
        return {"success": False, "error": info.get("error", "Unknown error")}
    return info


@app.get("/api/youtube/transcript/{video_id}")
async def youtube_transcript(video_id: str):
    """Get video transcript."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    result = await extract_transcript_async(url, video_id)
    return result


@app.get("/api/youtube/comments/{video_id}")
async def youtube_comments(video_id: str, max_comments: int = 25):
    """Get video comments."""
    result = await fetch_youtube_comments(video_id, max(max_comments, 50))
    return result


@app.post("/api/youtube/analyze")
async def youtube_analyze(data: dict):
    """Full AI analysis of a YouTube video."""
    video_id = data.get("video_id", "")
    if not video_id:
        # Try to extract from URL
        url = data.get("url", "")
        vid = extract_youtube_id(url)
        if not vid:
            return {"success": False, "error": "video_id or valid YouTube URL required"}
        video_id = vid

    include_comments = data.get("include_comments", True)
    max_comments = data.get("max_comments", 10)
    model = data.get("model", "max-free")  # 9Router combo model

    result = await analyze_youtube_video(
        video_id, include_comments, max_comments, model
    )
    return result


@app.post("/api/youtube/transcribe")
async def youtube_transcribe(data: dict):
    """Download audio from YouTube and transcribe with Whisper."""
    video_id = data.get("video_id", "")
    if not video_id:
        url = data.get("url", "")
        vid = extract_youtube_id(url)
        if not vid:
            return {"success": False, "error": "video_id or valid YouTube URL required"}
        video_id = vid

    model = data.get("model", "small")
    language = data.get("language", "id")

    result = await transcribe_youtube(video_id, model, language)
    return result


@app.get("/api/youtube/channel/{channel_id}")
async def youtube_channel(channel_id: str, max_results: int = 20):
    """Get videos from a channel."""
    result = await fetch_channel_videos(channel_id, min(max_results, 50))
    return result


@app.get("/api/youtube/playlist/{playlist_id}")
async def youtube_playlist(playlist_id: str, max_results: int = 50):
    """Get videos from a playlist."""
    result = await fetch_playlist(playlist_id, min(max_results, 100))
    return result


@app.post("/api/youtube/index")
async def youtube_index(data: dict):
    """Index YouTube transcript to knowledge base (Qdrant)."""
    video_id = data.get("video_id", "")
    if not video_id:
        return {"success": False, "error": "video_id required"}

    # Fetch transcript first
    url = f"https://www.youtube.com/watch?v={video_id}"
    transcript_data = await extract_transcript_async(url, video_id)
    if not transcript_data.get("success"):
        return {"success": False, "error": "Could not fetch transcript"}

    # Get video info for title/channel
    info = await fetch_video_info(video_id)
    title = info.get("title", "Unknown") if info.get("success") else "Unknown"
    channel = info.get("channel", "") if info.get("success") else ""
    transcript_text = transcript_data.get("transcript", "") or ""

    result = await index_to_knowledge_base(
        video_id, title, transcript_text, channel
    )
    return result


@app.post("/api/youtube/process-url")
async def youtube_process_url(data: dict):
    """Process a YouTube URL — detect video/playlist and return structured data.
    Used by the hub to auto-detect YouTube links in chat."""
    url = data.get("url", "")
    if not url:
        return {"success": False, "error": "URL required"}

    if not is_youtube_url(url):
        return {"success": False, "error": "Not a YouTube URL"}

    # Check for playlist
    playlist_id = extract_playlist_id(url)
    if playlist_id:
        playlist_data = await fetch_playlist(playlist_id)
        return {"type": "playlist", "data": playlist_data}

    # Extract video ID
    video_id = extract_youtube_id(url)
    if not video_id:
        return {"success": False, "error": "Could not extract video ID"}

    # Fetch info and transcript in parallel
    info_task = fetch_video_info(video_id)
    transcript_task = extract_transcript_async(url, video_id)

    info, transcript = await asyncio.gather(info_task, transcript_task)

    return {
        "type": "video",
        "video_id": video_id,
        "info": info if info.get("success") else None,
        "transcript_available": transcript.get("success", False),
        "transcript": transcript.get("transcript", "")[:5000] if transcript.get("success") else None,
        "formatted_context": format_transcript_for_context(transcript, url,
            info.get("title","") if info.get("success") else "",
            info.get("channel","") if info.get("success") else ""),
    }


@app.get("/api/youtube/url-info")
async def youtube_url_info(url: str = ""):
    """Quick URL info endpoint (GET-friendly for hub)."""
    if not url:
        return {"success": False, "error": "url parameter required"}
    vid = extract_youtube_id(url)
    if not vid:
        return {"success": False, "error": "Invalid YouTube URL"}
    info = await fetch_video_info(vid)
    return {"video_id": vid, "info": info}


# ═══════════════════════════════════════════════════════════════════════════
# MCP Client — Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/mcp/servers")
async def mcp_list_servers():
    """List configured MCP servers, their connection status, and available tools."""
    servers = mcp_manager.get_server_status()
    all_tools = await mcp_manager.list_all_tools()
    result = []
    for s in servers:
        name = s["name"]
        result.append({
            **s,
            "tools": all_tools.get(name, []),
        })
    return {"servers": result}


@app.post("/api/mcp/connect")
async def mcp_connect(data: dict):
    """Connect to a specific MCP server by name."""
    name = data.get("server", "")
    if not name:
        # Connect all
        status = await mcp_manager.connect_all()
        return {"status": "ok", "results": status}
    ok = await mcp_manager.connect_server(name)
    return {"status": "ok" if ok else "error", "server": name}


@app.post("/api/mcp/disconnect")
async def mcp_disconnect(data: dict):
    """Disconnect a specific or all MCP servers."""
    name = data.get("server", "")
    if name:
        await mcp_manager.remove_server(name)
    else:
        await mcp_manager.disconnect_all()
    return {"status": "ok"}


@app.post("/api/mcp/call")
async def mcp_call_tool(data: dict):
    """Call a tool on an MCP server.

    Body: { "server": "server_name", "tool": "tool_name", "arguments": {...} }
    """
    server_name = data.get("server", "")
    tool_name = data.get("tool", "")
    arguments = data.get("arguments", {})

    if not server_name or not tool_name:
        return {"success": False, "error": "server and tool are required"}

    result = await mcp_manager.call_tool(server_name, tool_name, arguments)
    return result


@app.post("/api/mcp/search-tools")
async def mcp_search_tools(data: dict):
    """Search for tools across all connected servers."""
    query = data.get("query", "")
    if not query:
        return {"results": []}
    results = await mcp_manager.search_tools(query)
    return {"results": results}


@app.post("/api/mcp/chat")
async def mcp_chat(data: dict):
    """Chat with AI that has access to MCP tools."""
    messages = data.get("messages", [])
    agent = data.get("agent", "researchx")
    model = data.get("model", "max-free")
    mcp_enabled = data.get("mcp_enabled", True)

    system = AGENT_PROMPTS.get(agent, "Kamu asisten AI INXOTIVE yang membantu. Jawab dalam Bahasa Indonesia.")
    if mcp_enabled:
        try:
            tools = await mcp_manager.list_all_tools()
            if tools:
                system += format_tool_list_for_context(tools)
        except Exception:
            pass

    ollama_msgs = [{"role": "system", "content": system}]
    for msg in messages[-20:]:
        if isinstance(msg, dict):
            r = "user" if msg.get("role") == "user" else "assistant"
            c = msg.get("content", "")
            if c:
                ollama_msgs.append({"role": r, "content": c})

    model_name = model.replace("9r/", "") if model.startswith("9r/") else model
    api_key = os.environ.get("NINE_ROUTER_API_KEY", "")
    try:
        r = requests.post(
            "http://localhost:20128/v1/chat/completions",
            json={
                "model": model_name,
                "messages": ollama_msgs,
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        if r.status_code == 200:
            # Some 9Router models append SSE trailer — use brace-matching for safe extraction
            body = r.text
            depth = 0
            json_end = 0
            for i, c in enumerate(body):
                if c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break
            json_str = body[:json_end] if json_end > 0 else body
            data2 = json.loads(json_str)
            reply = data2.get("choices", [{}])[0].get("message", {}).get("content", "")
            # Check for tool use pattern
            tool_call = None
            import re
            m = re.search(r"\[USE TOOL:\s*([^/]+)/([^\]]+)\]", reply)
            if m and mcp_enabled:
                srv = m.group(1).strip()
                tl = m.group(2).strip()
                # Extract JSON arguments from the rest of the message
                args_match = re.search(r"\{.*\}", reply[m.end():], re.DOTALL)
                args = {}
                if args_match:
                    try:
                        args = json.loads(args_match.group())
                    except:
                        pass
                tool_call = {"server": srv, "tool": tl, "arguments": args}

            return {
                "reply": reply,
                "tool_call": tool_call,
                "mcp_enabled": mcp_enabled,
            }
        return {"reply": f"Error: HTTP {r.status_code}", "tool_call": None, "mcp_enabled": mcp_enabled}
    except Exception as e:
        return {"reply": f"Error: {e}", "tool_call": None, "mcp_enabled": mcp_enabled}


# ═══════════════════════════════════════════════════════════════════════════
# Autodream (Memory Consolidation) — Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/autodream/consolidate")
async def api_autodream_consolidate():
    """Run full memory consolidation sweep."""
    stats = await consolidate_memory()
    report = format_consolidate_report(stats)
    return {"success": True, "stats": stats, "report": report, "changed": bool(stats.get("dates_fixed") or stats.get("duplicates_removed") or stats.get("lines_compressed"))}


@app.post("/api/autodream/daily")
async def api_autodream_daily():
    """Run lightweight daily consolidation."""
    stats = await daily_consolidate()
    return {"success": True, "stats": stats}


@app.get("/api/autodream/insights")
async def api_autodream_insights():
    """Generate usage insights report (/insights equivalent)."""
    report = await generate_usage_insights()
    return {"success": True, "report": report}


@app.get("/api/autodream/status")
async def api_autodream_status():
    """Show autodream status."""
    import os
    ts_file = Path.home() / ".claude/projects/-home-bisma/memory/.autodream_last_run"
    last_run = None
    if ts_file.exists():
        last_run = ts_file.read_text().strip()[:19]
    return {"last_run": last_run, "memory_dir": str(Path.home() / ".claude/projects/-home-bisma/memory")}


# ═══════════════════════════════════════════════════════════════════════════
# Scheduled Tasks — Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/scheduled-tasks")
async def list_scheduled_tasks():
    """List all scheduled cron tasks from the event bus / systemd timers."""
    import subprocess, shlex
    try:
        # List user systemd timers
        r = subprocess.run(["systemctl", "--user", "list-timers", "--no-pager"], capture_output=True, text=True, timeout=10)
        output = r.stdout or ""
        # Also check for custom tasks file
        tasks_file = Path.home() / ".scheduled_tasks.json"
        tasks = []
        if tasks_file.exists():
            tasks = json.loads(tasks_file.read_text())
        return {"systemd": output[:2000], "custom_tasks": tasks}
    except Exception as e:
        return {"error": str(e), "custom_tasks": []}


@app.post("/api/scheduled-tasks")
async def create_scheduled_task(data: dict):
    """Create a new scheduled task.

    Body: {"name": "daily-backup", "cron": "0 6 * * *", "command": "python3 ~/backup.py", "enabled": true}
    """
    name = data.get("name", "")
    cron = data.get("cron", "")
    command = data.get("command", "")
    enabled = data.get("enabled", True)

    if not name or not cron or not command:
        return {"success": False, "error": "name, cron, and command are required"}

    tasks_file = Path.home() / ".scheduled_tasks.json"
    tasks = []
    if tasks_file.exists():
        tasks = json.loads(tasks_file.read_text())

    # Remove existing with same name
    tasks = [t for t in tasks if t.get("name") != name]

    tasks.append({
        "name": name,
        "cron": cron,
        "command": command,
        "enabled": enabled,
        "created": datetime.now().isoformat(),
    })

    tasks_file.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))

    # Try to create systemd timer
    try:
        import subprocess
        unit = f"inxotive-task-{name.replace(' ', '-').lower()}"
        timer_content = f"""[Unit]
Description=INXOTIVE Scheduled Task: {name}

[Timer]
OnCalendar={cron}
Persistent=true

[Install]
WantedBy=timers.target"""
        service_content = f"""[Unit]
Description=INXOTIVE Task: {name}

[Service]
Type=oneshot
ExecStart=/bin/bash -c {shlex.quote(f"source ~/.env_secrets 2>/dev/null; {command}")}
WorkingDirectory=%h"""
        Path(f"{HOME}/.config/systemd/user/{unit}.timer").write_text(timer_content)
        Path(f"{HOME}/.config/systemd/user/{unit}.service").write_text(service_content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
        if enabled:
            subprocess.run(["systemctl", "--user", "enable", f"{unit}.timer"], capture_output=True, timeout=10)
            subprocess.run(["systemctl", "--user", "start", f"{unit}.timer"], capture_output=True, timeout=10)
    except Exception as e:
        return {"success": True, "systemd_error": str(e), "task_saved": True}

    return {"success": True, "task": name}


@app.delete("/api/scheduled-tasks/{name}")
async def delete_scheduled_task(name: str):
    """Delete a scheduled task."""
    tasks_file = Path.home() / ".scheduled_tasks.json"
    if tasks_file.exists():
        tasks = json.loads(tasks_file.read_text())
        tasks = [t for t in tasks if t.get("name") != name]
        tasks_file.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))

    # Remove systemd timer
    try:
        import subprocess
        unit = f"inxotive-task-{name.replace(' ', '-').lower()}"
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.timer"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "--user", "disable", f"{unit}.timer"], capture_output=True, timeout=10)
        for f in [Path(f"{HOME}/.config/systemd/user/{unit}.timer"), Path(f"{HOME}/.config/systemd/user/{unit}.service")]:
            if f.exists():
                f.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
    except Exception:
        pass

    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# Hooks — Event Pipeline
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/hooks")
async def list_hooks():
    """List all registered hooks."""
    hooks_file = Path.home() / ".hooks_config.json"
    if hooks_file.exists():
        return json.loads(hooks_file.read_text())
    return {"hooks": []}


@app.post("/api/hooks")
async def register_hook(data: dict):
    """Register a new hook.

    Body: {
        "name": "pre-deploy-check",
        "trigger": "pre_deploy",
        "action": "command",
        "command": "python3 ~/market-api/agentshield_scan.py",
        "enabled": true
    }
    """
    hooks_file = Path.home() / ".hooks_config.json"
    hooks = {"hooks": []}
    if hooks_file.exists():
        hooks = json.loads(hooks_file.read_text())

    hook = {
        "name": data.get("name", ""),
        "trigger": data.get("trigger", "pre_tool"),
        "action": data.get("action", "command"),
        "command": data.get("command", ""),
        "webhook_url": data.get("webhook_url", ""),
        "enabled": data.get("enabled", True),
        "created": datetime.now().isoformat(),
    }

    # Remove existing with same name
    hooks["hooks"] = [h for h in hooks["hooks"] if h.get("name") != hook["name"]]
    hooks["hooks"].append(hook)
    hooks_file.write_text(json.dumps(hooks, indent=2, ensure_ascii=False))

    return {"success": True, "hook": hook}


@app.delete("/api/hooks/{name}")
async def delete_hook(name: str):
    """Delete a hook."""
    hooks_file = Path.home() / ".hooks_config.json"
    if hooks_file.exists():
        hooks = json.loads(hooks_file.read_text())
        hooks["hooks"] = [h for h in hooks["hooks"] if h.get("name") != name]
        hooks_file.write_text(json.dumps(hooks, indent=2, ensure_ascii=False))
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# Visuals — Inline Charts
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/visuals/chart")
async def api_render_chart(data: dict):
    """Render a chart and return markdown with embedded image.

    Body: {"type": "line|bar|pie|area", "data": {"labels": [...], "values": [...]}, "title": "..."}
    """
    chart_type = data.get("type", "line")
    chart_data = data.get("data", {})
    title = data.get("title", "")
    result = render_chart(chart_type, chart_data, title)
    return {"success": True, "markdown": result}


# ═══════════════════════════════════════════════════════════════════════════
# File Generation — Excel / PDF / Invoice
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/files/excel")
async def api_gen_excel(data: dict):
    """Generate Excel spreadsheet from data."""
    result = await generate_excel(data)
    return result


@app.post("/api/files/pdf")
async def api_gen_pdf(data: dict):
    """Generate PDF from text content."""
    result = await generate_pdf(data)
    return result


@app.post("/api/files/invoice")
async def api_gen_invoice(data: dict):
    """Generate professional invoice PDF."""
    result = await generate_invoice(data)
    return result


@app.get("/api/files/download/{filename}")
async def api_download_file(filename: str):
    """Download a generated file."""
    from fastapi.responses import FileResponse
    filepath = Path("/tmp/inxotive-files") / filename
    if filepath.exists():
        return FileResponse(str(filepath), filename=filename)
    return {"error": "File not found", "filename": filename}


# ═══════════════════════════════════════════════════════════════════════════
# Channels — Discord/Telegram 2-way
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/channels/send")
async def api_channel_send(data: dict):
    """Send a message via channels.

    Body: {"message": "...", "severity": "info", "channels": ["discord"], "channel": "discord"}
    """
    message = data.get("message", "")
    severity = data.get("severity", "info")
    channels = data.get("channels", None)
    channel = data.get("channel", "")

    if channel:
        if channel == "discord":
            return await send_to_discord(message, severity)
        elif channel == "telegram":
            return await send_to_telegram(message)
        return {"success": False, "error": f"Unknown channel: {channel}"}

    result = await broadcast(message, channels)
    return result


@app.post("/api/channels/discord-command")
async def api_discord_command(data: dict):
    """Handle a Discord slash command."""
    command = data.get("command", "")
    args = data.get("args", {})
    result = await handle_discord_command(command, args)
    return {"reply": result}


@app.post("/api/channels/discord-webhook")
async def api_discord_webhook(data: dict):
    """Webhook endpoint for Discord messages."""
    content = data.get("body", "") or data.get("content", "") or data.get("message", "")
    author = data.get("author", "Discord User")
    result = await process_discord_message(content, author)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Remote Control — QR code + mobile commands
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/remote/session")
async def api_remote_session():
    """Create a remote control session (generates token + QR + link)."""
    result = await create_remote_session()
    return result


@app.post("/api/remote/execute")
async def api_remote_execute(data: dict):
    """Execute a command via remote control."""
    token = data.get("token", "")
    command = data.get("command", "")
    result = await execute_remote_command(token, command)
    return result


@app.get("/api/remote/status")
async def api_remote_status():
    """Check if remote control is available."""
    ip = _detect_hub_ip()
    return {"available": True, "hub_ip": ip, "port": 8888}


# ═══════════════════════════════════════════════════════════════════════════
# Verification Loop — Auto-testing
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/verify/endpoint")
async def api_verify_endpoint(data: dict):
    """Verify a single endpoint."""
    url = data.get("url", "")
    expected = data.get("expected", 200)
    result = await verify_endpoint(url, expected)
    return result


@app.post("/api/verify/multiple")
async def api_verify_multiple(data: dict):
    """Verify multiple endpoints in parallel."""
    endpoints = data.get("endpoints", [])
    results = await verify_multiple(endpoints)
    return {"results": results, "summary": format_verification_report(results)}


@app.post("/api/verify/loop")
async def api_verify_loop(data: dict):
    """Run a verification loop sequence."""
    base_url = data.get("base_url", "http://localhost:8888")
    actions = data.get("actions", [])
    screenshot_after = data.get("screenshot_after", [])
    results = await run_verification_loop(base_url, actions, screenshot_after)
    return {"results": results, "summary": format_verification_report(results)}


# ═══════════════════════════════════════════════════════════════════════════
# Agent Teams — Multi-agent coordination (A2A)
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/teams")
async def api_list_teams():
    """List all predefined agent teams."""
    return {"teams": agent_team_manager.list_teams()}


@app.post("/api/teams/run")
async def api_run_team(data: dict):
    """Run an agent team on a task.

    Body: {"team": "market|tech|business|security", "task": "...", "mode": "sequential|debate|hierarchical"}
    """
    team_name = data.get("team", "")
    task = data.get("task", "")
    mode = data.get("mode", "sequential")

    if not team_name or not task:
        return {"success": False, "error": "team and task are required"}

    result = await agent_team_manager.run(team_name, task, mode)
    return result


@app.post("/api/teams/create")
async def api_create_team(data: dict):
    """Create a custom agent team."""
    name = data.get("name", "")
    agents = data.get("agents", [])
    lead = data.get("lead", "researchx")
    model = data.get("model", "max-free")

    if not name or not agents:
        return {"success": False, "error": "name and agents are required"}

    agent_team_manager.create_team(name, agents, lead, model)
    return {"success": True, "team": name, "agents": agents, "lead": lead}


# ═══════════════════════════════════════════════════════════════════════════
# UI Audit — Visual Screenshot + Claude Vision Loop
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/ui-audit/capture")
async def api_ui_capture(data: dict):
    """Capture screenshot of a page for visual audit.

    Body: {"url": "http://localhost:8888/hub", "selector": "", "width": 1280, "height": 720}
    """
    from ui_audit import capture_screenshot
    url = data.get("url", "http://localhost:8888/hub")
    selector = data.get("selector", "")
    width = data.get("width", 1280)
    height = data.get("height", 720)
    result = await capture_screenshot(url, selector, width, height)
    return result


@app.post("/api/ui-audit/comparison")
async def api_ui_comparison(data: dict):
    """Take desktop + mobile screenshots for responsive audit."""
    from ui_audit import screenshot_comparison
    url = data.get("url", "http://localhost:8888/hub")
    result = await screenshot_comparison(url)
    return result


@app.get("/api/ui-audit/check")
async def api_ui_check():
    """Check if Playwright is available for UI audit."""
    try:
        import importlib.util
        available = importlib.util.find_spec("playwright") is not None
    except Exception:
        available = False
    return {"available": available}


# ═══════════════════════════════════════════════════════════════════════════
# n8n Integration
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/n8n/status")
async def n8n_status():
    """Check n8n connection status and health."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("http://localhost:5678/healthz")
            return {"success": r.status_code == 200, "status": r.status_code, "url": "http://localhost:5678"}
    except Exception as e:
        return {"success": False, "error": str(e), "url": "http://localhost:5678"}


@app.post("/api/n8n/webhook")
async def n8n_trigger_webhook(data: dict):
    """Trigger an n8n webhook workflow.

    Body: {"webhook_id": "workflow-name", "payload": {...}}
    """
    webhook_id = data.get("webhook_id", "")
    payload = data.get("payload", {})
    if not webhook_id:
        return {"success": False, "error": "webhook_id required"}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"http://localhost:5678/webhook/{webhook_id}", json=payload)
            return {"success": r.status_code < 500, "status": r.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/n8n/workflows")
async def n8n_list_workflows():
    """List active n8n workflows (if API key configured)."""
    # Read from n8n config
    api_key = os.environ.get("N8N_API_KEY", "")
    if not api_key:
        return {"success": False, "error": "N8N_API_KEY not configured", "hint": "Generate via n8n UI: Settings → API Keys"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:5678/rest/workflows",
                           headers={"X-N8N-API-KEY": api_key})
            if r.status_code == 200:
                return {"success": True, "workflows": r.json().get("data", [])}
            return {"success": False, "status": r.status_code, "error": r.text[:200]}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# Odysseus Auth Proxy
# ═══════════════════════════════════════════════════════════════════════════


# Session cookie cache for Odysseus
_ODYSSEUS_SESSION = {"cookie": "", "expires": 0}


@app.get("/api/odysseus/{path:path}")
@app.post("/api/odysseus/{path:path}")
async def odysseus_proxy(path: str, request: Request):
    """Proxy to Odysseus API with auto-auth."""
    global _ODYSSEUS_SESSION
    import time as _time
    target_url = f"http://localhost:7000/api/{path}"

    # Refresh session if expired
    if _time.time() > _ODYSSEUS_SESSION["expires"]:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post("http://localhost:7000/api/auth/login", json={
                    "username": "admin", "password": "admin"
                })
                if r.status_code < 400:
                    _ODYSSEUS_SESSION["cookie"] = r.headers.get("set-cookie", "")
                    _ODYSSEUS_SESSION["expires"] = _time.time() + 3600
        except:
            pass

    headers = {"Cookie": _ODYSSEUS_SESSION["cookie"]} if _ODYSSEUS_SESSION["cookie"] else {}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            body = await request.body()
            method = request.method
            if method == "GET":
                r = await c.get(target_url, headers=headers, params=dict(request.query_params))
            else:
                r = await c.post(target_url, headers=headers, content=body)
            return StreamingResponse(
                content=r.aiter_bytes(),
                status_code=r.status_code,
                headers=dict(r.headers) if r.status_code < 400 else {},
            )
    except Exception as e:
        return {"error": f"Odysseus proxy error: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
# Vercel Deploy Integration
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/deploy/vercel")
async def vercel_deploy(data: dict):
    """Deploy to Vercel from builder output directory.

    Body: {"dir": "/path/to/project", "name": "project-name", "prod": true}
    """
    import subprocess, shutil
    deploy_dir = data.get("dir", "")
    project_name = data.get("name", "project")
    prod = data.get("prod", True)

    if not deploy_dir:
        return {"success": False, "error": "dir required"}

    vercel_path = shutil.which("vercel")
    if not vercel_path:
        return {"success": False, "error": "Vercel CLI not installed"}

    try:
        cmd = [vercel_path, "--cwd", deploy_dir, "--yes"]
        if prod:
            cmd.append("--prod")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout.strip() or result.stderr.strip()
        url = output.split("\n")[-1] if output else "deployed"
        return {"success": result.returncode == 0, "url": url, "output": output[:500]}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Deploy timed out (120s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/deploy/status")
async def vercel_status():
    """Check if Vercel CLI is available."""
    import shutil
    vercel_path = shutil.which("vercel")
    version = ""
    if vercel_path:
        import subprocess
        try:
            version = subprocess.run([vercel_path, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        except:
            version = "unknown"
    return {"available": bool(vercel_path), "version": version, "path": vercel_path}


# ═══════════════════════════════════════════════════════════════════════════
# Redis Cache Integration
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/cache/status")
async def cache_status():
    """Check Redis connection status."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(host="localhost", port=6379, socket_connect_timeout=3)
        await r.ping()
        await r.aclose()
        return {"connected": True}
    except ImportError:
        return {"connected": False, "error": "redis package not installed. Run: pip install redis"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.post("/api/cache/clear")
async def cache_clear(data: dict):
    """Clear Redis cache by key pattern.

    Body: {"pattern": "market:*"} — clears all keys matching pattern
    If no pattern, clears all cache.
    """
    pattern = data.get("pattern", "*")
    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(host="localhost", port=6379, socket_connect_timeout=3)
        keys = await r.keys(pattern)
        if keys:
            count = len(keys)
            await r.delete(*keys)
            await r.aclose()
            return {"success": True, "cleared": count, "pattern": pattern}
        await r.aclose()
        return {"success": True, "cleared": 0, "pattern": pattern}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Redis caching for market data ──
_REDIS_CLIENT = None

async def _get_redis():
    """Get Redis client singleton."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        try:
            import redis.asyncio as aioredis
            _REDIS_CLIENT = aioredis.Redis(host="localhost", port=6379, socket_connect_timeout=3)
        except:
            return None
    return _REDIS_CLIENT


async def _get_cached_or_fetch(key: str, fetch_fn, ttl: int = 30):
    """Get from Redis cache or fetch and cache."""
    try:
        r = await _get_redis()
        if r:
            cached = await r.get(key)
            if cached:
                return json.loads(cached)
    except:
        pass
    result = await fetch_fn()
    try:
        r = await _get_redis()
        if r:
            await r.setex(key, ttl, json.dumps(result))
    except:
        pass
    return result


@app.get("/hub", response_class=HTMLResponse)
async def hub():
    path = Path.home() / "market-api" / "hub.html"
    if path.exists():
        return HTMLResponse(path.read_text())
    return HTMLResponse("<h1>Hub not found</h1>", status_code=404)

# ── Agent System Prompts ──
AGENT_PROMPTS = {
    "researchx": "Kamu ResearchX, research analyst INXOTIVE. Berikan analisis mendalam dengan data spesifik, fakta terverifikasi, dan sumber jelas. Struktur jawaban: (1) Temuan utama (2) Data pendukung (3) Implikasi strategis (4) Rekomendasi. Gunakan format markdown yang rapi dengan **bold** untuk poin penting. Bahasa Indonesia yang baik dan profesional.",
    "webdev": "Kamu WebDev, lead developer INXOTIVE. Stack: React 18+Vite+Vercel, Python+FastAPI+Discord.py, Docker+Ubuntu. Berikan solusi teknis konkret dengan code snippet bila perlu. Struktur: (1) Analisis masalah (2) Solusi teknis (3) Command/kode yang bisa langsung dijalankan. Bahasa Indonesia.",
    "tradex": "Kamu TradeX, analis crypto profesional INXOTIVE. Setiap analisis crypto WAJIB fetch data pasar real-time dan sajikan dalam format:\n\n**KONDISI** -> Harga, volume, sentimen terkini\n**ANALISIS** -> Interpretasi teknikal (RSI, MACD, Bollinger), struktur pasar, korelasi BTC\n**LEVEL S/R** -> Support dan resistance kunci dengan harga spesifik\n**REKOMENDASI** -> Strategi trading dengan entry/exit jelas, time frame, risk-reward ratio\n**RISIKO** -> Faktor risiko utama dan manajemen risiko\n\nGunakan angka dan persentase spesifik. Bahasa Indonesia.",
    "bizmind": "Kamu BizMind, business strategist INXOTIVE web agency spesialis healthcare digital. Berikan strategi bisnis yang actionable dengan framework jelas (BMC, JTBD, AARRR). Struktur: (1) Analisis situasi (2) Strategi & rekomendasi (3) Action items dengan timeline 30/60/90 hari (4) Metrik kesuksesan. Bahasa Indonesia, data-driven, action-oriented.",
    "dr_pharma": "Kamu Dr. Pharma, mentor farmasi klinis senior INXOTIVE. Berikan edukasi farmasi klinis yang evidence-based dengan referensi guidelines terbaru (jika menyebut guideline, pastikan spesifik). Gunakan format SOAP untuk kasus klinis. Struktur: (1) Assessment (2) Diagnosis farmasi/DRP (3) Rencana intervensi (4) Monitoring. Bahasa Indonesia profesional.",
    "flowbot": "Kamu FlowBot, productivity coach INXOTIVE. Berikan sistem produktivitas konkret dengan langkah implementasi jelas. Struktur: (1) Diagnosis masalah produktivitas (2) Sistem/solusi (3) Langkah implementasi (4) Tools rekomendasi. Praktis, langsung terap, Bahasa Indonesia.",
    "opencode": "Kamu OpenCode, system architect INXOTIVE. Kamu yang BANGUN dan MAINTAIN semua sistem INXOTIVE. Keahlian: Ubuntu server, Docker, Python/FastAPI, React/Vite, Qdrant vector DB, Discord bot, self-healing infra, monitoring, MCP tools, WhatsApp bridge, Crew Manager. Jawab dengan markdown: (1) **Analisis masalah** — pahami konteks (2) **Solusi teknis** — pendekatan & arsitektur (3) **Command/kode** — berikan kode lengkap yang bisa langsung dipakai. Bahasa Indonesia, langsung, tanpa basa-basi. Prioritaskan solusi yang sudah ada di ekosistem INXOTIVE daripada install tools baru.",
    "claudecode": "Kamu Claude Code, business strategist INXOTIVE. Kamu yang URUS BISNIS INXOTIVE AGENCY. Keahlian: client acquisition B2B healthcare (klinik, apotek, lab), pricing strategy (Landing 1.5-3jt, Company Profile 4-7jt, Web+Maintenance 7-12jt+300-500rb/bln), proposal writing, marketing digital, growth hacking, networking dokter/apoteker, sales funnel B2B. Jawab dengan markdown: (1) **Analisis situasi** — data & konteks (2) **Strategi & rekomendasi** — langkah konkret (3) **Action items** — apa yang harus dilakukan sekarang (4) **Timeline** — prioritas & deadline. Bahasa Indonesia, langsung, action-oriented, no fluff. Prioritaskan metode murah atau gratis (organik) sebelum menyarankan iklan berbayar.",
    "securityx": "Kamu SecurityX, security auditor INXOTIVE. Specialist OWASP Top 10, secrets detection, dan vulnerability assessment. Tugas: (1) Scan kode untuk hardcoded secrets (API keys, tokens, password) (2) Deteksi potensi XSS, SQL injection, RCE, CSRF (3) Periksa dependency vulnerabilities (4) Berikan rekomendasi perbaikan dengan prioritas. Gunakan MCP tools `inxotive-agentshield` untuk scan otomatis. Bahasa Indonesia, tegas, no-nonsense. Prioritaskan CRITICAL issues.",
    "architectx": "Kamu ArchitectX, software architect INXOTIVE. Spesialis desain sistem skalabel, technical decision-making, dan codebase consistency. Keahlian: Python/FastAPI, React/Vite, Docker/Ubuntu, MCP protocol, API design, database modeling. Struktur jawaban: (1) **Analisis arsitektur saat ini** (2) **Pendekatan yang direkomendasikan** dengan trade-offs (3) **Diagram/struktur** yang jelas (4) **Implementation plan** bertahap. Bahasa Indonesia profesional. Evaluasi skalabilitas, maintainability, dan security di setiap rekomendasi.",
    "codereview": "Kamu CodeReview, code review specialist INXOTIVE. Review code untuk quality, security, dan maintainability. Proses: (1) Baca full context file yang berubah (2) Deteksi bugs, security issues, performance problems (3) Cek error handling, logging, type safety (4) Report findings dengan confidence level. Hanya report issues yang >80% yakin itu real problem. Jangan flood dengan stylistic preferences. Format: **CRITICAL** | **HIGH** | **MEDIUM** | **LOW**. Bahasa Indonesia, profesional, evidence-based.",
    "simplifier": "Kamu Simplifier, code refactoring specialist INXOTIVE. Sederhanakan kode tanpa mengubah behavior. Prinsip: clarity over cleverness, consistency dengan existing style, preserve behavior exactly. Target simplifikasi: (1) Duplikasi → DRY (2) Complex conditionals → guard clauses/polymorphism (3) Long functions → split (4) Deep nesting → early returns (5) Magic strings/numbers → constants. Tunjukkan before/after. Bahasa Indonesia. Jangan ubah API contracts atau behavior.",
    "perfopt": "Kamu PerfOpt, performance optimization specialist INXOTIVE. Analisis dan optimasi: (1) Slow code paths & bottlenecks (2) Memory leaks & resource usage (3) Database query optimization (4) API response time (5) Bundle size & lazy loading. Tools: Python profiling, React devtools, database indexing. Struktur: (1) **Identifikasi bottleneck** dengan data (2) **Root cause** (3) **Solusi** dengan expected impact (4) **Implementation**. Bahasa Indonesia, data-driven, quantifiable results.",
    "debugger": "Kamu Debugger, build-error resolver INXOTIVE. Spesialis debugging error messages, stack traces, dan build failures. Pendekatan: (1) Baca error message dengan teliti — cari file, line number, error type (2) Cari di codebase untuk konteks (3) Identifikasi root cause (4) Berikan fix minimal yang tepat. Error types: Python traceback, npm/Node errors, Docker build fails, React compilation errors, FastAPI 500s. Bahasa Indonesia. Jawab langsung dengan cause + fix, jangan general advice.",
    "a11y": "Kamu A11y, accessibility checker INXOTIVE. Spesialis web accessibility (WCAG 2.1) untuk website klien agency. Checklist: (1) Semantic HTML (headings, landmarks, alt text) (2) Keyboard navigation & focus management (3) Color contrast (4) Screen reader compatibility (5) ARIA attributes. Tools: axe-core, Lighthouse, WAVE. Struktur: (1) **Audit results** per issue (2) **WCAG criteria** dilanggar (3) **Fix** dengan code example (4) **Priority**. Bahasa Indonesia. Pastikan website klien compliance.",
    "datax": "Kamu DataX, data analyst INXOTIVE. Spesialis analisis data, visualisasi, dan insight extraction. Tools: Python/pandas, SQL, JSON, Excel/CSV. Kemampuan: (1) Data cleaning & preprocessing (2) Statistical analysis (3) Trend identification (4) Data visualization recommendations (5) Report generation. Struktur: (1) **Data overview** — size, schema, quality (2) **Key findings** dengan angka (3) **Visualizations** yang sesuai (4) **Recommendations** actionable. Bahasa Indonesia, data-driven. Jangan buat asumsi tanpa data.",
    "devopsx": "Kamu DevOpsX, infrastructure & DevOps specialist INXOTIVE. Keahlian: Ubuntu server, Docker containers, systemd services, monitoring, CI/CD, network config, self-healing infra, MCP servers. Struktur: (1) **Current state** — cek status service, resource usage (2) **Problem analysis** (3) **Solution** dengan command konkret (4) **Verification** steps. Prioritaskan solusi yang sudah ada di ekosistem INXOTIVE. Bahasa Indonesia, langsung, command-first. Gunakan MCP `inxotive-system` untuk cek status.",
    "compliance": "Kamu Compliance, regulatory & compliance checker INXOTIVE. Spesialis: (1) Farmasi — BPOM, regulasi obat, standar apotek (2) Data — UU PDP Indonesia, GDPR (3) Web — cookie consent, privacy policy, terms of service. Untuk setiap review: (1) Identifikasi regulasi relevan (2) Audit compliance (3) Gap analysis (4) Remediation plan. Bahasa Indonesia. Prioritaskan compliance untuk klien agency (klinik, apotek, lab).",
    "planner": "Kamu Planner, implementation planning specialist INXOTIVE. Tugas: bikin implementation plan detail untuk fitur baru atau refactoring besar. Proses: (1) **Analisis requirements** — pahami apa yang diminta, clarifikasi kalau ambigu (2) **Arsitektur review** — cek codebase existing, identifikasi komponen yang kena impact (3) **Step breakdown** — langkah detail dengan file paths, dependencies, estimated complexity, potential risks (4) **Implementation order** — prioritaskan berdasarkan dependensi, beri recommendation. Bahasa Indonesia, terstruktur, actionable. Jangan langsung coding — plan dulu.",
    "codeexplorer": "Kamu CodeExplorer, codebase analyst INXOTIVE. Spesialis memahami codebase existing sebelum development baru. Proses: (1) **Entry point discovery** — cari main entry points untuk fitur/area yang dimaksud (2) **Execution path tracing** — follow call chain dari trigger sampai completion (3) **Architecture layer mapping** — identifikasi layers, komunikasi antar layers, reusable boundaries (4) **Pattern recognition** — coding patterns, konvensi, anti-patterns (5) **Documentation** — output berupa peta codebase yang jelas. Bahasa Indonesia. Fokus pada pemahaman, bukan modifikasi.",
    "silenthunter": "Kamu SilentHunter, silent failure detective INXOTIVE. Zero tolerance untuk silent failures. Target: (1) **Empty catch blocks** — `except: pass`, `catch {}` — harus dihandle atau di-log (2) **Inadequate logging** — log tanpa context, wrong severity (3) **Dangerous fallbacks** — default values yang hide real failure (4) **Error propagation** — errors yang ke-swallow tanpa di-propagate (5) **Missing context managers** — file handles, connections yang gak pake `with` atau `try/finally`. Bahasa Indonesia, tegas, tiap temuan harus ada bukti line number + rekomendasi fix.",
    "pythonreview": "Kamu PythonReview, Python code review specialist INXOTIVE. Fokus PEP 8, type hints, Pythonic idioms. Prioritas review: **CRITICAL** — SQL injection via f-strings, command injection, path traversal, hardcoded secrets. **HIGH** — bare except, missing type annotations on public functions, manual resource management. **MEDIUM** — PEP 8 violations, unused imports/variables. **LOW** — stylistic preferences. Jalankan `ruff` atau `py_compile` untuk cek syntax. Bahasa Indonesia, evidence-based, tiap temuan dengan file:line.",
    "fastapix": "Kamu FastAPIx, FastAPI specialist INXOTIVE. Review aplikasi FastAPI untuk: (1) **Async correctness** — blocking calls di async endpoints (2) **Dependency injection** — database sessions, auth, pagination (3) **Pydantic schemas** — request/update/response separation (4) **Security** — CORS, rate limiting, auth, secrets handling (5) **OpenAPI quality** — endpoint descriptions, response models. Review scope: app entry point, routers, schemas, dependencies, middleware. Jangan review non-FastAPI code. Bahasa Indonesia.",
    "refactorx": "Kamu RefactorX, dead code cleanup specialist INXOTIVE. Tugas: (1) **Dead code detection** — temukan unused functions, imports, variables (2) **Duplicate elimination** — identifikasi dan konsolidasi kode duplikat (3) **Import cleanup** — remove unused imports (4) **Safe refactoring** — pastikan perubahan tidak break functionality. Cek dengan: `grep` untuk unused functions, `ruff` untuk unused imports. Tunjukkan before/after. Bahasa Indonesia. Jangan ubah API contracts. Jangan hapus code yang masih dipakai meskipun jarang.",
    "dbx": "Kamu DBX, database specialist INXOTIVE. Spesialis query optimization, schema design, dan data integrity. Scope: (1) **Query performance** — optimasi slow queries, indexing strategy (2) **Schema design** — tipe data tepat, constraints, normalisasi (3) **Security** — SQL injection prevention, least privilege (4) **Connection management** — pooling, timeouts (5) **Data integrity** — constraints, cascade rules. Untuk Qdrant: review collection schema, vector dimensions, payload fields. Bahasa Indonesia, data-driven.",
    "netfix": "Kamu NetFix, network troubleshooter INXOTIVE. Diagnosa masalah jaringan secara sistematis. Workflow: (1) **Characterize symptom** — apa yang gagal? (koneksi, DNS, latency?) (2) **Check physical/L1** — interface up? kabel? (3) **Check IP/L3** — `ip addr`, `ping`, `traceroute` (4) **Check DNS** — `nslookup`, `dig` (5) **Check services/L4-L7** — `curl`, `nc -zv`, `ss -tlnp` (6) **Root cause summary** — dengan evidence. Read-only — jangan ubah konfigurasi selama diagnosis. Bahasa Indonesia.",
    "seox": "Kamu SEOx, technical SEO specialist INXOTIVE untuk website klien agency. Kemampuan: (1) **Technical SEO audit** — meta tags, headings, canonical, sitemap, robots.txt (2) **Core Web Vitals** — LCP, FID, CLS optimization (3) **Structured data** — Schema.org, JSON-LD, rich results (4) **On-page optimization** — keyword mapping, content structure, internal linking (5) **Performance** — page speed, image optimization, caching. Tools: Lighthouse, PageSpeed Insights, structured data testing tool. Bahasa Indonesia. Output: audit report + action items.",
}

@app.post("/chat")
async def chat_endpoint(data: dict):
    """Chat dengan agent via Ollama."""
    messages = data.get("messages", [])
    agent = data.get("agent", "researchx")
    image = data.get("image", None)
    model = data.get("model", "qwen2.5:3b")
    
    # Route to 9Router if model starts with 9r/
    if model.startswith("9r/"):
        return await chat_via_9router(messages, agent, model, image)
    
    system = AGENT_PROMPTS.get(agent, "Kamu asisten AI INXOTIVE. Berikan jawaban informatif, terstruktur dengan poin-poin jelas, dan actionable. Gunakan **bold** untuk penekanan. Bahasa Indonesia yang baik dan profesional.")
    if image:
        system += "\n\nKamu juga bisa menganalisis gambar yang dikirim user. Deskripsikan secara detail apa yang kamu lihat."
    
    ollama_messages = [{"role": "system", "content": system}]
    
    if image:
        ollama_messages.append({
            "role": "user", 
            "content": "Analisis gambar ini:", 
            "images": [image]
        })
    
    for msg in messages[-20:]:
        if isinstance(msg, dict):
            role = "user" if msg.get("role") == "user" else "assistant"
            content = msg.get("content", "")
            if content:
                ollama_messages.append({"role": role, "content": content})
    
    try:
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": model.replace("ollama/", ""),
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 1024}
        }, timeout=90)
        if r.ok:
            result = r.json()
            reply = result.get("message", {}).get("content", "")
            return {"reply": reply, "agent": agent, "model": model}
        return {"reply": "Maaf, saya sedang tidak bisa menjawab. Coba lagi.", "agent": agent}
    except Exception as e:
        return {"reply": f"Error: {str(e)}", "agent": agent}

@app.post("/chat-fast")
async def chat_fast(data: dict):
    """Chat dengan model kecil untuk response cepat."""
    messages = data.get("messages", [])
    agent = data.get("agent", "researchx")
    model = data.get("model", "qwen2.5:3b")
    
    if model.startswith("9r/"):
        return await chat_via_9router(messages, agent, model)
    
    system = AGENT_PROMPTS.get(agent, "Kamu asisten INXOTIVE. Jawab singkat namun informatif, poin-poin jelas, Bahasa Indonesia.")
    ollama_msgs = [{"role": "system", "content": system}]
    for msg in messages[-10:]:
        if isinstance(msg, dict):
            r = "user" if msg.get("role") == "user" else "assistant"
            c = msg.get("content", "")
            if c: ollama_msgs.append({"role": r, "content": c})
    try:
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": model.replace("ollama/", ""),
            "messages": ollama_msgs,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 512}
        }, timeout=45)
        if r.ok:
            result = r.json()
            reply = result.get("message", {}).get("content", "")
            return {"reply": reply, "agent": agent, "model": model}
        return {"reply": "Maaf, sedang sibuk. Coba lagi.", "agent": agent}
    except Exception as e:
        return {"reply": f"Error: {str(e)}", "agent": agent}

async def chat_via_9router(messages, agent, model, image=None):
    """Chat via 9Router proxy."""
    model_name = model.replace("9r/", "")
    agent_prompt = AGENT_PROMPTS.get(agent, "Kamu asisten AI INXOTIVE yang membantu. Jawab dalam Bahasa Indonesia.")
    system = agent_prompt
    if "ringkas" not in system and "singkat" not in system:
        system += "\n\nGunakan Bahasa Indonesia yang baik, jelas, dan terstruktur. Berikan analisis mendalam dengan poin-poin penting, data spesifik bila ada, dan kesimpulan yang actionable. Hindari jawaban generik."
    ollama_msgs = [{"role": "system", "content": system}]
    for msg in messages[-15:]:
        if isinstance(msg, dict):
            r = "user" if msg.get("role") == "user" else "assistant"
            c = msg.get("content", "")
            if c: ollama_msgs.append({"role": r, "content": c})
    try:
        api_key = os.environ.get("NINE_ROUTER_API_KEY") or os.environ.get("ROUTER_API_KEY", "")
        r = requests.post("http://localhost:20128/v1/chat/completions", json={
            "model": model_name,
            "messages": ollama_msgs,
            "stream": False,
            "max_tokens": 4096,
        }, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
        if r.ok:
            body = r.text
            # Find JSON end via brace-matching (reasoning_content may contain 'data:' string)
            depth = 0
            json_end = 0
            for i, c in enumerate(body):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break
            json_str = body[:json_end] if json_end > 0 else body
            data = json.loads(json_str)
            msg = data.get("choices", [{}])[0].get("message", {})
            reply = msg.get("content", "") or msg.get("reasoning_content", "") or ""
            return {"reply": reply or "(empty)", "agent": agent, "model": model}
        return {"reply": f"9Router error: {r.status_code}", "agent": agent}
    except Exception as e:
        return {"reply": f"9Router: {str(e)}", "agent": agent}

@app.get("/api/models")
async def list_models():
    """List available models from Ollama + 9Router (non-blocking)."""
    models = []
    api_key = os.environ.get("NINE_ROUTER_API_KEY") or os.environ.get("ROUTER_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=5) as client:
        ollama_r, router_r = await asyncio.gather(
            client.get("http://localhost:11434/api/tags"),
            client.get("http://localhost:20128/v1/models", headers=headers),
            return_exceptions=True,
        )
    # Ollama models
    try:
        if not isinstance(ollama_r, Exception) and ollama_r.status_code < 400:
            for m in ollama_r.json().get("models", []):
                name = m.get("name", "")
                if name:
                    models.append({"id": "ollama/"+name, "provider": "Ollama", "local": True})
    except Exception:
        pass
    # 9Router models — fetch from their /v1/models API
    try:
        if not isinstance(router_r, Exception) and router_r.status_code < 400:
            for m in router_r.json().get("data", []):
                mid = m.get("id", "")
                owner = m.get("owned_by", "")
                if mid:
                    prefix = "9r/"
                    label = "9Router"
                    if owner == "combo":
                        label = "9Router-Combo"
                    models.append({"id": prefix+mid, "provider": label, "local": False})
    except:
        pass
    
    if not models:
        models = [
            {"id": "ollama/qwen2.5:3b", "provider": "Ollama", "local": True},
            {"id": "ollama/llama3.1:8b", "provider": "Ollama", "local": True},
        ]
    return models

# ── Session Management ──

@app.get("/api/sessions")
async def list_sessions():
    sessions = load_sessions()
    data = sorted(
        [{"id": k, "title": v.get("title", "New Chat"), "created": v.get("created", ""),
          "message_count": len(v.get("messages", []))} for k, v in sessions.items()],
        key=lambda s: s.get("created", ""), reverse=True
    )
    return {"sessions": data}

@app.post("/api/sessions")
async def create_session(data: dict = {}):
    sessions = load_sessions()
    sid = str(uuid.uuid4())[:12]
    title = data.get("title", "New Chat")
    sessions[sid] = {"title": title, "messages": [], "created": datetime.now().isoformat()}
    save_sessions(sessions)
    return {"id": sid, "title": title}

@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    sessions = load_sessions()
    sessions.pop(sid, None)
    save_sessions(sessions)
    return {"ok": True}

@app.get("/api/sessions/{sid}/history")
async def session_history(sid: str):
    sessions = load_sessions()
    s = sessions.get(sid)
    return {"messages": s["messages"] if s else []}

@app.post("/api/sessions/{sid}/title")
async def update_session_title(sid: str, data: dict):
    sessions = load_sessions()
    if sid in sessions:
        sessions[sid]["title"] = data.get("title", "Chat")
        save_sessions(sessions)
        return {"ok": True}
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": "not found"}, status_code=404)

# ── SSE Streaming Chat ──

@app.post("/api/chat/stream")
async def chat_stream_endpoint(data: dict):
    messages = data.get("messages", [])
    agent = data.get("agent", "researchx")
    model = data.get("model", "qwen2.5:3b")
    session_id = data.get("session_id", "")
    mcp_enabled = data.get("mcp_enabled", False)

    async def generate():
        system = AGENT_PROMPTS.get(agent, "Kamu asisten AI INXOTIVE. Berikan jawaban informatif, terstruktur dengan poin-poin jelas dan actionable. Bahasa Indonesia.")

        # Inject MCP tools into system prompt if enabled
        if mcp_enabled:
            try:
                tools = await mcp_manager.list_all_tools()
                if tools:
                    system += format_tool_list_for_context(tools)
            except Exception:
                pass

        ollama_msgs = [{"role": "system", "content": system}]
        for msg in messages[-30:]:
            if isinstance(msg, dict):
                r = "user" if msg.get("role") == "user" else "assistant"
                c = msg.get("content", "")
                if c: ollama_msgs.append({"role": r, "content": c})

        full_reply = ""

        if model.startswith("9r/"):
            model_name = model.replace("9r/", "")
            api_key = os.environ.get("NINE_ROUTER_API_KEY", "")
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream("POST", "http://localhost:20128/v1/chat/completions", json={
                        "model": model_name, "messages": ollama_msgs, "stream": True, "max_tokens": 4096,
                    }, headers={"Authorization": f"Bearer {api_key}"}) as resp:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                d = line[6:].strip()
                                if d == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(d)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    token = delta.get("content", "") or delta.get("reasoning_content", "") or ""
                                    if token:
                                        full_reply += token
                                        yield f"data: {json.dumps({'token': token})}\n\n"
                                except: pass
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        else:
            ollama_model = model.replace("ollama/", "")
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream("POST", "http://localhost:11434/api/chat", json={
                        "model": ollama_model, "messages": ollama_msgs, "stream": True,
                        "options": {"temperature": 0.7, "num_predict": 2048},
                    }) as resp:
                        async for line in resp.aiter_lines():
                            if line:
                                try:
                                    chunk = json.loads(line)
                                    token = chunk.get("message", {}).get("content", "")
                                    if token:
                                        full_reply += token
                                        yield f"data: {json.dumps({'token': token})}\n\n"
                                except: pass
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        if not full_reply:
            yield f"data: {json.dumps({'token': '(tidak ada respons — model mungkin sibuk, coba model lain)'})}\n\n"

        # Check for MCP tool call in the response
        tool_result = None
        if mcp_enabled and full_reply:
            import re
            m = re.search(r"\[USE TOOL:\s*([^/]+)/([^\]]+)\]", full_reply)
            if m:
                srv = m.group(1).strip()
                tl = m.group(2).strip()
                # Extract JSON arguments after the tool tag
                after = full_reply[m.end():]
                args_match = re.search(r"\{.*\}", after, re.DOTALL)
                args = {}
                if args_match:
                    try:
                        args = json.loads(args_match.group())
                    except json.JSONDecodeError:
                        pass

                # Execute the tool call
                tool_result = await mcp_manager.call_tool(srv, tl, args)
                if tool_result.get("success"):
                    formatted = format_tool_result_for_context(tool_result)
                    # Send tool result as a special event
                    yield f"data: {json.dumps({'tool_call': {'server': srv, 'tool': tl, 'result': formatted}})}\n\n"
                else:
                    yield f"data: {json.dumps({'tool_call': {'server': srv, 'tool': tl, 'error': tool_result.get('error', 'Unknown error')}})}\n\n"

        yield f"data: {json.dumps({'done': True, 'agent': agent, 'model': model, 'tool_result': bool(tool_result)})}\n\n"

        if session_id and full_reply:
            sessions = load_sessions()
            if session_id in sessions:
                last_user_msg = ""
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        last_user_msg = msg.get("content", "")
                        break
                sessions[session_id]["messages"].append({"role": "user", "content": last_user_msg})
                sessions[session_id]["messages"].append({"role": "assistant", "content": full_reply, "model": model, "agent": agent})
                if len(sessions[session_id]["messages"]) <= 4 and last_user_msg:
                    sessions[session_id]["title"] = (last_user_msg[:60] + "...") if len(last_user_msg) > 60 else last_user_msg
                save_sessions(sessions)

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/analyze-image")
async def analyze_image(request: Request):
    """Analisis gambar menggunakan Ollama vision model."""
    try:
        body = await request.json()
        image_b64 = body.get("image", "")
        prompt = body.get("prompt", "Jelaskan apa yang kamu lihat di gambar ini dalam Bahasa Indonesia.")
        
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": "llava:7b",
            "messages": [
                {"role": "user", "content": prompt, "images": [image_b64]}
            ],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 512}
        }, timeout=120)
        
        if r.ok:
            result = r.json()
            reply = result.get("message", {}).get("content", "")
            return {"analysis": reply}
        else:
            # Fallback: try with qwen (text-only)
            return {"analysis": "Model vision belum selesai di-download. Coba lagi dalam beberapa menit."}
    except Exception as e:
        return {"analysis": f"Error: {str(e)}"}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>INXOTIVE Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,-apple-system,sans-serif}
body{background:#0f172a;color:#e2e8f0;padding:20px;max-width:1200px;margin:auto}
h1{color:#38bdf8;margin-bottom:8px;font-size:1.5rem}
.sub{color:#94a3b8;font-size:.85rem;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px}
.card{background:#1e293b;border-radius:12px;padding:16px;border:1px solid #334155}
.card h3{color:#38bdf8;font-size:.9rem;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
.status{display:flex;gap:8px;flex-wrap:wrap}
.status span{padding:4px 12px;border-radius:20px;font-size:.8rem;font-weight:600}
.up{background:#065f46;color:#6ee7b7}
.down{background:#7f1d1d;color:#fca5a5}
.lead{border-bottom:1px solid #334155;padding:8px 0;font-size:.85rem}
.lead:last-child{border:0}
.lead .nama{color:#fbbf24;font-weight:600}
.lead .tgl{color:#64748b;font-size:.75rem}
table{width:100%;border-collapse:collapse;font-size:.85rem}
td,th{padding:8px;text-align:left;border-bottom:1px solid #334155}
th{color:#94a3b8;font-weight:600;font-size:.75rem;text-transform:uppercase}
</style>
</head>
<body>
<h1>🖥 INXOTIVE DASHBOARD</h1>
<p class="sub" id="ts">Loading...</p>
<div class="grid">
  <div class="card" id="services-card"><h3>Services</h3><div class="status" id="services"></div></div>
  <div class="card"><h3>System</h3><div id="system"></div></div>
</div>
<div class="card"><h3>📋 Leads Terbaru</h3><div id="leads"></div></div>
<br>
<div class="card"><h3>🧠 Knowledge Base</h3>
<div id="kb-stats"></div>
<p style="margin-top:8px">
<a href="/intake" style="color:#38bdf8">📝 Intake Form</a>
<a href="/brief" style="color:#38bdf8;margin-left:16px">📊 Brief</a>
</p>
</div>
<div class="card"><h3>⚡ Performance (24h)</h3>
<div id="perf-data">Loading...</div>
</div>
<div class="card"><h3>🔄 Self-Healing Stats</h3>
<div id="heal-stats">Loading...</div>
</div>
<script>
async function load(){
  document.getElementById('ts').textContent='Last updated: '+new Date().toLocaleString('id-ID');
  try{
    let s=await fetch('/status').then(r=>r.json());
    let h='';
    for(let[k,v]of Object.entries(s.services)){
      h+='<span class="'+v+'">'+k+'</span>';
    }
    document.getElementById('services').innerHTML=h+'<span class="up">market-api</span>';
    document.getElementById('system').innerHTML='<table><tr><td>Disk</td><td>'+s.disk+'</td></tr><tr><td>Uptime</td><td>'+Math.floor(s.uptime/3600)+' jam</td></tr></table>';
    }catch(e){document.getElementById('services-card').innerHTML+='<p style=color:#fca5a5>Error loading</p>'}
  try{
    let kb=await fetch('/knowledge?q=a').then(r=>r.json());
    document.getElementById('kb-stats').innerHTML='<table><tr><td>Qdrant</td><td>213 vectors</td></tr><tr><td>TF-IDF</td><td>213 docs</td></tr><tr><td>MemPalace</td><td>89 hallways</td></tr></table>';
  }catch(e){}
  try{
    let p=await fetch('/perf').then(r=>r.json());
    let pt='<table><tr><th>Service</th><th>Avg (ms)</th><th>Uptime</th></tr>';
    if(p.data?.services) for(let[k,v]of Object.entries(p.data.services)){
      pt+='<tr><td>'+k+'</td><td>'+v.avg_ms+'ms</td><td>'+v.uptime_pct+'%</td></tr>';
    }
    pt+='</table><p style=color:#94a3b8;font-size:.75rem>Sampel: '+p.data?.samples+' dalam 24 jam</p>';
    document.getElementById('perf-data').innerHTML=pt;
  }catch(e){}
  try{
    let h=await fetch('/heal/stats').then(r=>r.json());
    let ht='';
    if(h.by_pattern) for(let[k,v]of Object.entries(h.by_pattern)){
      ht+='<span style="display:inline-block;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:4px 10px;margin:4px;font-size:.8rem">'+k+': '+v+'x</span>';
    }
    document.getElementById('heal-stats').innerHTML=ht||'<p style=color:#94a3b8>Belum ada data</p>';
  }catch(e){}
  }catch(e){document.getElementById('kb-stats').innerHTML='Qdrant: 213 vectors'}
  try{
    let l=await fetch('/leads').then(r=>r.json());
    let lh='';
    if(l.leads.length===0) lh='<p style=color:#64748b>Belum ada leads</p>';
    else l.leads.slice().reverse().forEach(lead=>{
      lh+='<div class=lead><span class=nama>'+lead.nama_usaha+'</span> — '+lead.nama_pemilik+'<br>'
      +'<span class=tgl>'+new Date(lead.time).toLocaleString('id-ID')+' · '+lead.jenis_usaha+' · '+lead.paket+'</span></div>';
    });
    document.getElementById('leads').innerHTML=lh;
  }catch(e){}
}
load();
setInterval(load,30000);
</script>
</body>
</html>""")

@app.post("/wa-webhook")
async def wa_webhook(request: Request):
    """Webhook untuk WhatsApp Bridge — pesan masuk dari WA."""
    try:
        body = await request.json()
        from_ = body.get("from", "")
        pesan = body.get("body", "")
        nama = body.get("name", "Unknown")
        if not pesan:
            return {"reply": None}
        # Forward ke bot untuk diproses
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {
                "content": f"📱 **WA dari {nama}** (`{from_}`)\n\n{pesan[:1500]}",
                "username": "WhatsApp Bridge"
            }
            webhook_url = os.environ.get("DISCORD_WEBHOOK", "")
            if webhook_url:
                await session.post(webhook_url, json=payload)
        # Default reply jika bot offline
        reply = f"Halo {nama}! Pesanmu diterima. Tim INXOTIVE akan menghubungi kamu segera."
        if "bot" in pesan.lower() or "hai" in pesan.lower() or "halo" in pesan.lower():
            reply = f"Halo {nama}! 👋 Ada yang bisa dibantu? Ketik /menu untuk lihat layanan INXOTIVE: Web Development, Automation, AI Consulting."
        return {"reply": reply}
    except Exception as e:
        print(f"[WA-WEBHOOK] Error: {e}", flush=True)
        return {"reply": "Maaf, terjadi kesalahan sistem. Tim kami akan segera merespon."}

@app.get("/portal/{nama}")
async def portal(nama: str):
    leads = []
    path = Path.home() / "inxotive-office" / "leads.json"
    if path.exists():
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                leads.append(json.loads(line))
    leads = [l for l in leads if nama.lower() in l.get("nama_usaha", "").lower()]
    if not leads:
        return HTMLResponse(f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Client Portal</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:#0f172a;color:#e2e8f0;padding:20px;max-width:800px;margin:auto}}
h1{{color:#38bdf8}}a{{color:#38bdf8}}</style></head><body>
<h1>📋 Client Portal</h1><p style="color:#94a3b8">Tidak ada klien ditemukan untuk "{nama}"</p>
<p><a href="/portal/">Lihat semua klien</a></p></body></html>""")
    lead = leads[-1]
    paket_map = {"landing": "Landing Page (1,5-3jt)", "company": "Company Profile (4-7jt)", "web": "Web App (7-12jt)"}
    paket_label = paket_map.get(lead.get("paket", ""), lead.get("paket", "-"))
    statuses = ["📝 Intake", "🎨 Desain", "💻 Development", "🚀 Deploy", "✅ Selesai"]
    current_step = 2
    return HTMLResponse(f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{lead.get('nama_usaha','Klien')} — INXOTIVE Portal</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:#0f172a;color:#e2e8f0;padding:20px;max-width:900px;margin:auto}}
h1{{color:#38bdf8;font-size:1.5rem;margin-bottom:4px}}
.sub{{color:#94a3b8;font-size:.85rem;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.card{{background:#1e293b;border-radius:12px;padding:16px;border:1px solid #334155}}
.card h3{{color:#38bdf8;font-size:.8rem;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.label{{color:#64748b;font-size:.8rem}}
.value{{font-size:1rem;margin-bottom:8px}}
.timeline{{display:flex;gap:4px;margin:16px 0;flex-wrap:wrap}}
.step{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:8px 12px;font-size:.8rem;flex:1;min-width:100px;text-align:center}}
.step.active{{background:#065f46;border-color:#6ee7b7;color:#6ee7b7}}
.step.done{{background:#1e3a5f;border-color:#38bdf8;color:#38bdf8}}
.msg{{border-bottom:1px solid #334155;padding:8px 0;font-size:.85rem}}
.tag{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.75rem;background:#1e3a5f;color:#38bdf8}}
.tag.lead{{background:#7c3aed;color:#ddd6fe}}
a{{color:#38bdf8;font-size:.85rem}}
</style></head><body>
<h1>{lead.get('nama_usaha','Klien')}</h1>
<p class="sub">{lead.get('nama_pemilik','-')} · <span class="tag">✉️ {lead.get('whatsapp','-')}</span></p>
<div class="grid">
  <div class="card"><h3>Info Proyek</h3>
    <div class="label">Paket</div><div class="value">{paket_label}</div>
    <div class="label">Jenis Usaha</div><div class="value">{lead.get('jenis_usaha','-')}</div>
    <div class="label">Email</div><div class="value">{lead.get('email','-')}</div>
    <div class="label">Alamat</div><div class="value">{lead.get('alamat','-')}</div>
  </div>
  <div class="card"><h3>Status Proyek</h3>
    <div class="timeline">{"".join(f'<div class="step {"done" if i < current_step else "active" if i == current_step else ""}'+('✅' if i < current_step else "📝" if i == current_step else "⏳")+f' {s}</div>' for i, s in enumerate(statuses))}</div>
    <p style="font-size:.85rem;color:#94a3b8">Estimasi: 7-14 hari kerja</p>
    <a href="/intake">📝 Update status</a></div>
</div>
<div class="card"><h3>📅 Timeline & Catatan</h3>
  <div class="msg"><strong>{lead.get('time','-')[:10]}</strong> — Intake form diisi oleh {lead.get('nama_pemilik','-')} <span class="tag lead">LEAD</span></div>
  {"<div class='msg'><strong>✅</strong> Proposal sudah digenerate. <a href='/proposal/"+nama.lower().replace(' ','-')+"'>Lihat Proposal →</a></div>" if Path.home().joinpath('inxotive-office/portal_data',nama.lower().replace(' ','-')+'.html').exists() else '<div class="msg"><strong>⏳</strong> Proposal sedang diproses oleh AI...</div>'}
  <p style="margin-top:8px;font-size:.85rem;color:#94a3b8">Catatan: {lead.get('catatan','-')}</p>
</div>
<p style="margin-top:16px;font-size:.85rem"><a href="/portal">← Semua klien</a> · <a href="/dashboard">🏠 Dashboard</a></p>
</body></html>""")

@app.get("/portal")
async def portal_list():
    path = Path.home() / "inxotive-office" / "leads.json"
    leads = []
    if path.exists():
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                leads.append(json.loads(line))
    cards = "".join(f'<a href="/portal/{l["nama_usaha"].lower().replace(" ","-")}" style="text-decoration:none;color:inherit">'
                    f'<div class=card style="margin-bottom:8px;cursor:pointer"><strong>{l.get("nama_usaha","-")}</strong>'
                    f'<br><span style=color:#94a3b8;font-size:.85rem>{l.get("nama_pemilik","-")} · {l.get("jenis_usaha","-")}'
                    f' · {l.get("paket","-")}</span></div></a>' for l in reversed(leads))
    return HTMLResponse(f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Client Portal</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:#0f172a;color:#e2e8f0;padding:20px;max-width:800px;margin:auto}}
h1{{color:#38bdf8}}a{{color:#38bdf8}}.card{{background:#1e293b;border-radius:8px;padding:12px;border:1px solid #334155}}
.sub{{color:#94a3b8;font-size:.85rem}}</style></head><body>
<h1>📋 Semua Klien</h1><p class="sub">{len(leads)} total leads</p>
{cards if cards else '<p style=color:#94a3b8>Belum ada klien</p>'}
<p style="margin-top:16px"><a href="/dashboard">← Dashboard</a></p></body></html>""")

@app.get("/events")
async def get_events(limit: int = 20, severity: str = ""):
    """Event bus — latest events from all components."""
    path = Path.home() / ".event_bus.json"
    if not path.exists():
        return {"events": []}
    try:
        events = json.loads(path.read_text())
        if severity:
            events = [e for e in events if e.get("severity") == severity]
        return {"events": events[-limit:]}
    except:
        return {"events": []}

@app.post("/events")
async def post_event(data: dict):
    """Push event ke bus dari component mana pun."""
    source = data.get("source", "external")
    event_type = data.get("type", "info")
    message = data.get("message", "")
    severity = data.get("severity", "info")
    push_event(source, event_type, message, severity)
    return {"status": "ok"}

@app.post("/alert")
async def unified_alert(data: dict):
    """Unified alert — route ke semua channel."""
    source = data.get("source", "external")
    message = data.get("message", "")
    severity = data.get("severity", "info")
    alert_all(source, message, severity)
    return {"status": "routed", "severity": severity}

@app.get("/perf")
async def perf():
    """Performance history dan trends."""
    import subprocess, json
    try:
        r = subprocess.run(
            [sys.executable, str(Path.home() / "inxotive-office" / "scripts" / "perf_monitor.py"), "dashboard"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0 and r.stdout.strip():
            return {"status": "ok", "data": json.loads(r.stdout)}
        return {"status": "no_data", "data": None}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/heal/stats")
async def heal_stats():
    """Self-healing stats & learned rules."""
    import json
    try:
        path = Path.home() / ".heal_learn.json"
        if path.exists():
            data = json.loads(path.read_text())
            return {
                "status": "ok",
                "total_incidents": len(data.get("history", [])),
                "by_pattern": data.get("patterns", {}),
                "active_rules": len(data.get("rules", [])),
                "last_analysis": data.get("last_analysis"),
            }
        return {"status": "no_data"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/proposal/{nama}")
async def proposal(nama: str):
    safe = nama.lower().replace(" ", "-")
    path = Path.home() / "inxotive-office" / "portal_data" / f"{safe}.html"
    if not path.exists():
        return HTMLResponse(f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Proposal</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:#0f172a;color:#e2e8f0;padding:20px;max-width:800px;margin:auto}}
h1{{color:#38bdf8}}</style></head><body>
<h1>📋 Proposal</h1><p>Proposal untuk "{nama}" masih diproses. Coba refresh dalam beberapa menit.</p>
<p><a href="/portal/{nama}" style="color:#38bdf8">← Kembali ke Portal</a></p></body></html>""")
    return HTMLResponse(path.read_text())

@app.post("/deploy")
async def deploy(data: dict):
    """Trigger Vercel deploy untuk project klien."""
    try:
        nama = data.get("nama_usaha", "")
        if not nama:
            return {"error": "nama_usaha required"}
        safe = nama.lower().replace(" ", "-")
        # Generate Vercel deploy config
        deploy_dir = Path.home() / "inxotive-office" / "deployments" / safe
        deploy_dir.mkdir(parents=True, exist_ok=True)
        # Create simple landing page
        index_html = f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{nama} — INXOTIVE</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
<div class="text-center p-8">
<h1 class="text-4xl font-bold text-gray-800 mb-4">{nama}</h1>
<p class="text-gray-600 text-lg">Website sedang dalam pengembangan oleh INXOTIVE OFFICE</p>
<p class="text-gray-400 mt-8">🚀 Coming Soon</p>
</div>
</body>
</html>"""
        (deploy_dir / "index.html").write_text(index_html)
        (deploy_dir / "vercel.json").write_text(json.dumps({
            "framework": None,
            "buildCommand": None,
            "outputDirectory": ".",
        }, indent=2))
        # Try to deploy via Vercel CLI if available
        import subprocess
        import shutil
        vercel = shutil.which("vercel")
        if vercel:
            result = subprocess.run(
                [vercel, "--prod", "--yes"],
                cwd=str(deploy_dir),
                capture_output=True, text=True, timeout=60
            )
            url = result.stdout.strip().split("\n")[-1] if result.stdout else "deploy started"
        else:
            url = f"Vercel CLI not installed. Files ready at {deploy_dir}"
        return {"status": "ok", "nama": nama, "url": url, "dir": str(deploy_dir)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/exec")
async def exec_command(data: dict):
    cmd = data.get("cmd", "").strip()
    if not cmd:
        return {"stdout": "", "stderr": "No command provided"}
    dangerous = ["rm -rf /*", "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sda", "chmod 777 /"]
    if any(d in cmd for d in dangerous):
        return {"stdout": "", "stderr": "Command blocked for safety"}
    try:
        import subprocess
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path.home())
        )
        return {"stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:], "code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out (30s)", "code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1}

@app.get("/api/files")
async def list_files(path: str = ""):
    """List directory contents. Path relatif ke /home/bisma."""
    import stat as stat_module
    base = Path.home().resolve()
    target = (base / path.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        return {"error": "Access denied", "items": []}
    if not target.exists():
        return {"error": "Not found", "items": []}
    if target.is_file():
        st = target.stat()
        return {"type": "file", "name": target.name, "path": str(target.relative_to(base)),
                "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).isoformat()}
    items = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                st = entry.stat()
                items.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(base)),
                    "is_dir": entry.is_dir(),
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "mode": oct(st.st_mode)[-3:],
                })
            except:
                pass
    except PermissionError:
        return {"error": "Permission denied", "items": []}
    return {"path": str(target), "name": target.name, "items": items,
            "parent": str(target.parent.relative_to(base)) if target != base else None}

@app.get("/api/files/read")
async def read_file_content(path: str = ""):
    """Read text file content, max 100KB."""
    base = Path.home().resolve()
    target = (base / path.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        return {"error": "Access denied"}
    if not target.is_file():
        return {"error": "Not a file"}
    if target.stat().st_size > 102400:
        return {"error": "File too large (>100KB)"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "path": str(target.relative_to(base)), "size": target.stat().st_size}
    except Exception as e:
        return {"error": str(e)}

# ── Ecosystem Endpoints ──

def _ecosystem_blocking():
    """Blocking parts of the ecosystem aggregator (run in a worker thread)."""
    import subprocess, json
    services = {"bot": "down", "odysseus": "down", "ollama": "down",
                "market-api": "up", "qdrant": "down", "n8n": "down",
                "wa-bridge": "down", "9router": "down"}
    system = {"cpu": "0", "memory": "0/0", "disk": "0"}
    uptime = 0
    try:
        cpu_r = subprocess.run(["bash", "-c", "top -bn1 | grep 'Cpu(s)' | awk '{print $2}'"],
            capture_output=True, text=True, timeout=3)
        if cpu_r.returncode == 0 and cpu_r.stdout.strip():
            system["cpu"] = cpu_r.stdout.strip()
        mem_r = subprocess.run(["bash", "-c", "free -m | awk '/Mem:/{print $3\"/\"$2}'"],
            capture_output=True, text=True, timeout=3)
        if mem_r.returncode == 0 and mem_r.stdout.strip():
            system["memory"] = mem_r.stdout.strip()
        disk_r = subprocess.run(["bash", "-c", "df -h / | tail -1 | awk '{print $4\" free of \"$2}'"],
            capture_output=True, text=True, timeout=3)
        if disk_r.returncode == 0 and disk_r.stdout.strip():
            system["disk"] = disk_r.stdout.strip()
        uptime_r = subprocess.run(["bash", "-c", "cat /proc/uptime | awk '{print $1}'"],
            capture_output=True, text=True, timeout=3)
        if uptime_r.returncode == 0 and uptime_r.stdout.strip():
            uptime = float(uptime_r.stdout.strip())
        for svc in ["inxotive-bot", "odysseus", "ollama"]:
            sr = subprocess.run(["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=3)
            services[svc.replace("inxotive-", "")] = "up" if "active" in sr.stdout else "down"
    except Exception: pass

    docker = []
    try:
        r_d = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"],
            capture_output=True, text=True, timeout=5)
        if r_d.returncode == 0:
            for line in r_d.stdout.strip().split("\n"):
                if not line.strip(): continue
                p = line.split("|")
                docker.append({"name": p[0], "image": p[1] if len(p)>1 else "",
                              "status": p[2] if len(p)>2 else ""})
    except Exception: pass

    market = {}
    try:
        md = get_market_data()
        crypto = md.get("crypto", {})
        if crypto:
            btc_data = crypto.get("bitcoin", {})
            if isinstance(btc_data, dict) and "usd" in btc_data:
                market["btc"] = btc_data.get("usd", 0)
            eth_data = crypto.get("ethereum", {})
            if isinstance(eth_data, dict) and "usd" in eth_data:
                market["eth"] = eth_data.get("usd", 0)
        fg = md.get("fear_greed", {})
        market["fear_greed"] = fg.get("value", "N/A") if isinstance(fg, dict) else "N/A"
    except Exception: pass

    events = []
    try:
        if EVENT_BUS.exists():
            events = json.loads(EVENT_BUS.read_text())[-10:]
    except Exception: pass

    heal = {}
    try:
        hfile = Path.home() / ".heal_learn.json"
        if hfile.exists():
            hd = json.loads(hfile.read_text())
            heal = {
                "total_incidents": len(hd.get("history", [])),
                "by_pattern": hd.get("patterns", {}),
                "active_rules": len(hd.get("rules", [])),
                "last_analysis": hd.get("last_analysis", ""),
            }
    except Exception: pass
    return services, system, uptime, docker, market, events, heal


async def _check_health(client, name, url):
    try:
        r = await client.get(url)
        return name, {"status": "up" if r.status_code < 400 else "down", "code": r.status_code}
    except Exception:
        return name, {"status": "down", "code": 0}


@app.get("/api/ecosystem")
async def ecosystem_overview():
    """Aggregator: semua data ekosistem dalam 1 call (non-blocking)."""
    # HTTP health checks run concurrently; blocking subprocess/file IO on a thread.
    targets = [
        ("odysseus", "http://localhost:7000/api/health"),
        ("ollama", "http://localhost:11434/api/tags"),
        ("qdrant", "http://localhost:6333/collections"),
        ("wa-bridge", "http://localhost:3002/health"),
        ("n8n", "http://localhost:5678/healthz"),
        ("uptime-kuma", "http://localhost:3001"),
        ("meilisearch", "http://localhost:7700/health"),
    ]
    health = {}
    blocking_task = asyncio.create_task(asyncio.to_thread(_ecosystem_blocking))
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            results = await asyncio.gather(*[_check_health(client, n, u) for n, u in targets])
            for name, info in results:
                health[name] = info
            try:
                r3 = await client.get("http://localhost:20128/v1/models",
                    headers={"Authorization": f"Bearer {os.environ.get('NINE_ROUTER_API_KEY','')}"})
                if r3.status_code < 400:
                    health["9router"] = {"status": "up", "models": len(r3.json().get("data", []))}
                else:
                    health["9router"] = {"status": "down", "code": r3.status_code}
            except Exception:
                health["9router"] = {"status": "down", "code": 0}
    except Exception:
        pass

    services, system, uptime, docker, market, events, heal = await blocking_task
    # Reconcile services dict with live health probes so it isn't permanently "down"
    for key, hname in (("qdrant", "qdrant"), ("n8n", "n8n"), ("wa-bridge", "wa-bridge"), ("9router", "9router")):
        if health.get(hname, {}).get("status") == "up":
            services[key] = "up"

    return {
        "services": services, "health": health, "system": system,
        "uptime": float(uptime) if uptime else 0, "docker": docker,
        "market": market, "events": events, "heal": heal,
        "timestamp": datetime.now().isoformat(),
    }

def _docker_ps_blocking():
    import subprocess
    r = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.CreatedAt}}|{{.Size}}"],
        capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return {"containers": [], "error": r.stderr}
    containers = []
    for line in r.stdout.strip().split("\n"):
        if not line.strip(): continue
        p = line.split("|")
        containers.append({"name": p[0], "image": p[1] if len(p)>1 else "",
            "status": p[2] if len(p)>2 else "", "ports": p[3] if len(p)>3 else "",
            "created": p[4] if len(p)>4 else "", "size": p[5] if len(p)>5 else ""})
    return {"containers": containers, "total": len(containers)}


@app.get("/api/docker/ps")
async def docker_ps():
    """Docker containers list (non-blocking)."""
    try:
        return await asyncio.to_thread(_docker_ps_blocking)
    except Exception as e:
        return {"containers": [], "error": str(e)}

@app.get("/api/wa/status")
async def wa_status():
    """WhatsApp Bridge connection status + QR code."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://localhost:3002/health")
            if r.status_code < 400:
                result = r.json()
                # Try to fetch QR image if available
                if result.get("qr"):
                    try:
                        qr_resp = await client.get("http://localhost:3002/qrcode.png")
                        if qr_resp.status_code == 200:
                            import base64
                            result["qr_base64"] = f"data:image/png;base64,{base64.b64encode(qr_resp.content).decode()}"
                    except Exception:
                        pass
                return result
            return {"connected": False, "qr": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"connected": False, "qr": False, "error": str(e)}

@app.get("/api/wa/qr")
async def wa_qr():
    """Fetch WhatsApp QR code as base64 image from bridge."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            qr_resp = await client.get("http://localhost:3002/qrcode.png")
            if qr_resp.status_code == 200:
                import base64
                b64 = base64.b64encode(qr_resp.content).decode()
                return {"success": True, "qr_base64": f"data:image/png;base64,{b64}"}
            return {"qr": False}
    except Exception:
        return {"qr": False}

def _market_overview_blocking():
    """Blocking market snapshot (run in a worker thread)."""
    market = get_market_data()
    btc_ta = get_technical_analysis("bitcoin")
    news = []
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json?rss_url=https%3A%2F%2Fcointelegraph.com%2Frss",
            timeout=5)
        if r.ok:
            news = [{"title": i.get("title",""), "url": i.get("link",""),
                     "source": "CoinTelegraph"} for i in r.json().get("items",[])][:8]
    except Exception:
        try:
            r2 = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN&limit=8",
                             timeout=5)
            if r2.ok:
                news = [{"title": i.get("title",""), "url": i.get("url",""),
                         "source": "CryptoCompare"} for i in r2.json().get("Data",[])]
        except Exception: pass
    crypto = market.get("crypto", {})
    coins = {}
    for name, data in crypto.items():
        if isinstance(data, dict) and "usd" in data:
            coins[name] = {"usd": data.get("usd"), "idr": data.get("idr"),
                "change_24h": data.get("usd_24h_change"),
                "market_cap": data.get("usd_market_cap")}
    fg = market.get("fear_greed", {})
    return {
        "coins": coins,
        "fear_greed": fg if isinstance(fg, dict) else {"value": "N/A"},
        "trending": market.get("trending", ""),
        "btc_technical": btc_ta,
        "news": news[:8],
    }


@app.get("/api/market/overview")
async def market_overview():
    """Market snapshot: prices, technicals, news (non-blocking)."""
    try:
        return await asyncio.to_thread(_market_overview_blocking)
    except Exception as e:
        return {"error": str(e)}


_ODYSSEY_COOKIE = None
_ODYSSEY_CSRF = None
_ODYSSEY_LOCK = threading.Lock()

def _odyssey_ensure_auth():
    """Login to Odysseus with stored credentials."""
    global _ODYSSEY_COOKIE, _ODYSSEY_CSRF
    with _ODYSSEY_LOCK:
        if _ODYSSEY_COOKIE:
            try:
                r = requests.get("http://localhost:7000/api/auth/status",
                    cookies={"odysseus_session": _ODYSSEY_COOKIE}, timeout=3)
                if r.ok and r.json().get("authenticated"):
                    return True
            except: pass
        pw = os.environ.get("ODYSSEY_PASSWORD") or os.environ.get("SUDO_PASS") or "Trisula89"
        try:
            r = requests.post("http://localhost:7000/api/auth/login", json={
                "username": "bisma", "password": pw
            }, timeout=5)
            if r.ok and r.json().get("ok"):
                _ODYSSEY_COOKIE = r.cookies.get("odysseus_session", "")
                _ODYSSEY_CSRF = r.cookies.get("csrf_token", "")
                cfile = Path.home() / ".odyssey_cookie"
                cfile.write_text(json.dumps({"cookie": _ODYSSEY_COOKIE, "csrf": _ODYSSEY_CSRF}))
                return True
        except Exception as e:
            print(f"[ODYSSEY] Auth error: {e}", flush=True)
        try:
            cfile = Path.home() / ".odyssey_cookie"
            if cfile.exists():
                data = json.loads(cfile.read_text())
                _ODYSSEY_COOKIE = data.get("cookie", "")
                _ODYSSEY_CSRF = data.get("csrf", "")
                if _ODYSSEY_COOKIE:
                    return True
        except: pass
        return False

@app.get("/api/odyssey/{path:path}")
async def odyssey_get(path: str, request: Request):
    """Proxy GET to Odysseus API, forwarding query params."""
    if not _odyssey_ensure_auth():
        return {"error": "Odysseus unavailable"}
    try:
        cookies = {"odysseus_session": _ODYSSEY_COOKIE}
        if _ODYSSEY_CSRF: cookies["csrf_token"] = _ODYSSEY_CSRF
        # Forward query params
        params = dict(request.query_params)
        url = f"http://localhost:7000/api/{path}"
        r = requests.get(url, params=params, cookies=cookies, timeout=10,
            headers={"Accept": "application/json"})
        return r.json() if r.text else {}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/odyssey/{path:path}")
async def odyssey_post(path: str, data: dict = {}):
    """Proxy POST to Odysseus API."""
    if not _odyssey_ensure_auth():
        return {"error": "Odysseus unavailable"}
    try:
        cookies = {"odysseus_session": _ODYSSEY_COOKIE, "csrf_token": _ODYSSEY_CSRF}
        if _ODYSSEY_CSRF: cookies["csrf_token"] = _ODYSSEY_CSRF
        r = requests.post(f"http://localhost:7000/api/{path}",
            json=data, cookies=cookies, timeout=10,
            headers={"Content-Type": "application/json"})
        return r.json() if r.text else {}
    except Exception as e:
        return {"error": str(e)}

@app.put("/api/odyssey/{path:path}")
async def odyssey_put(path: str, data: dict = {}):
    """Proxy PUT to Odysseus API (used for prefs/theme sync)."""
    if not _odyssey_ensure_auth():
        return {"error": "Odysseus unavailable"}
    try:
        cookies = {"odysseus_session": _ODYSSEY_COOKIE}
        if _ODYSSEY_CSRF: cookies["csrf_token"] = _ODYSSEY_CSRF
        r = requests.put(f"http://localhost:7000/api/{path}",
            json=data, cookies=cookies, timeout=10,
            headers={"Content-Type": "application/json"})
        return r.json() if r.text else {}
    except Exception as e:
        return {"error": str(e)}

@app.delete("/api/odyssey/{path:path}")
async def odyssey_delete(path: str):
    """Proxy DELETE to Odysseus API."""
    if not _odyssey_ensure_auth():
        return {"error": "Odysseus unavailable"}
    try:
        cookies = {"odysseus_session": _ODYSSEY_COOKIE, "csrf_token": _ODYSSEY_CSRF}
        if _ODYSSEY_CSRF: cookies["csrf_token"] = _ODYSSEY_CSRF
        r = requests.delete(f"http://localhost:7000/api/{path}",
            cookies=cookies, timeout=10)
        return r.json() if r.text else {}
    except Exception as e:
        return {"error": str(e)}
