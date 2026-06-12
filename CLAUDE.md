# INXOTIVE HUB — Claude Code Web Context

INXOTIVE HUB adalah **single-page dashboard** untuk mengelola ekosistem server INXOTIVE.
Satu file `hub.html` (~3720 baris) berisi SEMUA CSS, JS, dan HTML.

## Stack
- **Frontend:** HTML + CSS inline + Vanilla JS, TailwindCSS CDN, ApexCharts, FontAwesome 6, Material Symbols
- **Backend:** Python FastAPI (`app.py`, 2627 baris), port 8888
- **Design:** Material Design 3, Plus Jakarta Sans
- **Deploy:** `python3 -m uvicorn app:app --host 0.0.0.0 --port 8888`

## Struktur File
- `hub.html` (188KB) — dashboard utama: 20 pages, 24 AI agents, MCP dropdown, model selector, dll
- `app.py` (120KB) — FastAPI backend: 50+ endpoints, SSE streaming, MCP client, proxy
- `mcp_client.py` — JSON-RPC MCP client manager (7 servers, 55 tools)
- `mcp_*_server.py` — 5 MCP server implementations (market, youtube, knowledge, system, agentshield)
- `youtube_service.py` — YouTube search/transcript/analysis via 9Router
- `autodream.py` — Memory consolidation engine
- `channels.py` — Discord/Telegram 2-way communication
- `visuals.py` — Inline chart generation (matplotlib)
- `filegen.py` — Excel/PDF/Invoice generation
- `remote.py` — QR code remote control
- `verify.py` — Endpoint auto-testing with Playwright screenshots
- `agent_teams.py` — A2A multi-agent coordination
- `ui_audit.py` — Visual UI audit & fix
- `run_mcp.py` — Unified MCP server runner

## 20 Pages (in hub.html sidebar)
Main: Chat, Overview, Brainstorm, Pipeline, Knowledge
Odyssey: Brain, Notes, Tasks, Calendar, Compare, Cookbook, Library, Themes
Tools: Market, Docker, Terminal, Files, WA Bridge
System: System Dashboard, Settings

## Key Backend Endpoints
- `GET /hub` — Dashboard HTML (188KB)
- `GET /api/ecosystem` — Aggregator: 8 health checks + system + docker + market + heal
- `GET /api/models` — 52 AI models (3 Ollama + 49 9Router)
- `POST /api/chat/stream` — SSE streaming chat (agent, model, mcp_enabled)
- `GET /api/mcp/servers` — 7 MCP servers + tools
- `POST /exec` — Command execution (bash, safety-blocked)
- `GET /api/autodream/status` — Memory consolidation status

## Critical Rules (JANGAN dilanggar)
1. **Jangan edit `app.py` line 1518-1520** — ini endpoint `/hub` yang serve `hub.html`
2. **Semua style ada INLINE** di `<style>` tag `hub.html` — jangan pindahin ke file CSS
3. **JS functions** di `<script>` block kedua `hub.html` — 133 functions total
4. **Jangan tambah library baru** — gunakan yang sudah ada (Tailwind CDN, FontAwesome CDN, Material Symbols)
5. **CSS class naming** — gunakan Tailwind utility classes untuk layout, custom classes untuk komponen spesifik
6. **Pastikan div & comment balance** setelah edit: `opens/closes` harus sama
7. **Test setelah edit:** `cd ~/market-api && python3 -m uvicorn app:app --host 0.0.0.0 --port 8888`

## 24 AI Agents
Original: researchx, opencode, claudecode, tradex, webdev, bizmind, dr_pharma, flowbot
ECC: securityx, architectx, codereview, simplifier, perfopt, debugger, a11y, datax, devopsx, compliance, planner, codeexplorer, silenthunter, pythonreview, fastapix, refactorx, dbx, netfix, seox

## MCP Servers (7)
inxotive-market (6 tools) · inxotive-youtube (6 tools) · inxotive-knowledge (5 tools)
inxotive-system (7 tools) · inxotive-agentshield (5 tools) · github (26 tools) · inxotive-web (0 tools)

## Deploy
```bash
cd ~/market-api && python3 -m uvicorn app:app --host 0.0.0.0 --port 8888
```
Atau via systemd: `sudo systemctl restart market-api`

## Dependencies
Python: fastapi, uvicorn, httpx, aiofiles, aiohttp, requests, Pillow, openpyxl, reportlab, matplotlib, beautifulsoup4, yt-dlp, websockets

## File lengkap ada di
- Server: `~/market-api/`
- Dokumen review: `~/Documents/INXOTIVE_HUB_REVIEW.md`
- GitHub: `bismaduta/inxotive-hub`
