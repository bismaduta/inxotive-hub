# INXOTIVE HUB — Full System Audit & Review

> **Dibuat:** 13 Juni 2026 (Updated: 14 Juni 2026)
> **Server:** inxotive-server (HP ENVY x360, Ubuntu 24.04)
> **Owner:** Bisma (bismaduta)
> **URL:** `http://localhost:8888/hub`

---

## 📋 Daftar Isi

1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Arsitektur Sistem](#2-arsitektur-sistem)
3. [Dashboard Design & UI](#3-dashboard-design--ui)
4. [20 Halaman Dashboard](#4-20-halaman-dashboard)
5. [API Endpoints](#5-api-endpoints)
6. [Ekosistem Services](#6-ekosistem-services)
7. [MCP Servers](#7-mcp-servers)
8. [9Router AI Models](#8-9router-ai-models)
9. [Status & Metrik](#9-status--metrik)
10. [Bug Tracker & Fixes](#10-bug-tracker--fixes)
11. [Cara Menggunakan](#11-cara-menggunakan)

---

## 1. Ringkasan Eksekutif

INXOTIVE HUB adalah **single-file dashboard aplikasi web** yang menggantikan terminal untuk mengelola seluruh ekosistem INXOTIVE. Satu halaman HTML (~3720 baris) berisi semua CSS, JS, dan HTML yang berfungsi sebagai command center untuk:

- **20 halaman** dalam 1 file HTML — termasuk System Dashboard baru
- **8 service health checks** real-time via `/api/ecosystem`
- **52 AI models** via 9Router (6 kategori: Fast, Claude, Key, Reasoning, Gemini, Groq, Ollama)
- **8 Docker containers** monitoring (termasuk redis)
- **7 MCP servers** dengan 55 total tools (inxotive-market, youtube, knowledge, system, agentshield, web, github)
- **24 AI agents** (8 original + 16 ECC)
- **8 fitur Odyssey** (Brain, Notes, Tasks, Calendar, Compare, Cookbook, Library, Themes)
- **Frontend**: Material Design 3, Plus Jakarta Sans, Material Symbols, ApexCharts

### Teknologi Stack

| Layer | Teknologi |
|-------|-----------|
| Frontend | HTML + CSS (inline) + Vanilla JS |
| Design System | Material Design 3 (Google) |
| Font | Plus Jakarta Sans (Google Fonts) |
| Icons | Material Symbols (Google) + FontAwesome 6 |
| Charts | ApexCharts |
| Styling | TailwindCSS CDN (forms + container-queries) |
| Backend | FastAPI (Python), 2627 baris |
| Proxy Auth | HTTP Basic + Session Cookie |
| MCP Client | 7 servers, 55 tools |
| Vector DB | Qdrant (port 6333) |

---

## 2. Arsitektur Sistem

```
                         ┌──────────────────────┐
                         │    Browser (User)     │
                         │  :8888/hub (3720 ln) │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │   Market API (FastAPI)│
                         │   localhost:8888      │
                         │   ~/market-api/       │
                         │   2627 baris, 120KB   │
                         └──────────┬───────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
   ┌────────────┐            ┌────────────┐            ┌────────────┐
   │  Direct    │            │  Proxy     │            │  MCP       │
   │  Endpoints │            │  Endpoints │            │  Client    │
   │  /status   │            │  /api/     │            │  7 servers │
   │  /market   │            │  odyssey/* │            │  55 tools  │
   │  /events   │            │            │            └────────────┘
   │  /exec     │            └─────┬──────┘
   └────────────┘                  │
                            ┌──────▼──────┐
                            │  Odysseus   │
                            │  :7000      │
                            │  Auth Proxy │
                            └─────────────┘
```

### File Utama

| File | Lokasi | Ukuran | Baris |
|------|--------|--------|-------|
| Dashboard | `~/market-api/hub.html` | 188KB | 3720 |
| API Server | `~/market-api/app.py` | 120KB | 2627 |
| MCP Client | `~/market-api/mcp_client.py` | 12KB | 317 |
| YouTube Service | `~/market-api/youtube_service.py` | ~25KB | 994 |
| Autodream | `~/market-api/autodream.py` | ~8KB | 200+ |
| Master Context | `~/INXOTIVE_MASTER_CONTEXT_v3.md` | 14KB | - |

---

## 3. Dashboard Design & UI

### 3.1 Layout Structure

```
┌──────┬──────┬──────────────────────────────────────────────────┐
│ Rail │ Side │              Main Content Area                    │
│ 80px │ 260px│  ┌──────────────────────────────────────────────┐│
│      │      │  │  TopBar (56px) — agent tabs, model select,   ││
│      │      │  │  YouTube btn, MCP ▾ dropdown, theme toggle   ││
│      │      │  ├──────────────────────────────────────────────┤│
│  🚀  │ Home │  │                                              ││
│  💬  │ Chat │  │  Page Sections (scrollable, 20 pages)       ││
│  🧠  │ Brain│  │                                              ││
│  📝  │Notes │  │  - Overview (hero + 4 stats + chart + bars) ││
│  ✅  │Tasks │  │  - Chat (welcome + 24 agents + SSE stream)  ││
│  📊  │Market│  │  - System Dashboard (services, docker, MCP,  ││
│  💻  │Term  │  │    resources, heal stats, tasks, actions)    ││
│  ⚙️  │Setng │  │  - Brainstorm (3 mode forum)                 ││
│      │      │  │  - Pipeline (leads tracking)                 ││
│      │      │  │  - Knowledge (Qdrant search)                 ││
│ [BI] │      │  │  - Market (5 crypto + fear&greed + news)     ││
│      │      │  │  - Docker (8 containers)                     ││
│      │      │  │  - WA Bridge (QR + status)                   ││
│      │      │  │  - Terminal, Files, Settings                 ││
│      │      │  │  - Brain, Notes, Tasks, Calendar             ││
│      │      │  │  - Compare, Cookbook, Library, Themes        ││
│      │      │  └──────────────────────────────────────────────┘│
│      │      │  ┌──────────────────────────────────────────────┐│
│      │      │  │  Status Bar — statusText | 🟢 8/8 online     ││
│      │      │  │    | modelInfo | TTS toggle                  ││
│      │      │  └──────────────────────────────────────────────┘│
└──────┴──────┴──────────────────────────────────────────────────┘
```

### 3.2 Design Tokens (Material Design 3)

**Light Theme:**
- Background: `#F4F5FA`
- Surface: `#f8f9ff`
- Primary: `#1f53c9`
- Primary Container: `#406de4`
- Secondary: `#006b58`
- Outline: `#747685`
- Error: `#ba1a1a`
- Font: `Plus Jakarta Sans`

**Dark Theme:**
- Background: `#10131a`
- Surface: `#10131a`
- Primary: `#b4c5ff`
- Primary Container: `#628aff`
- Secondary: `#b1cccc`
- Outline: `#8d909f`
- Error: `#ffb4ab`

### 3.3 Komponen UI

| Komponen | Style | Detail |
|----------|-------|--------|
| Mini Rail | 80px, icon-only | 7 shortcut buttons (overview, chat, brain, market, terminal, settings, avatar) |
| Sidebar | 260px, grouped | 4 sections: Main (5), Odyssey (8), Tools (6), System (3) |
| TopBar | 56px, dynamic | Content berubah per page + agent tabs + model select + YouTube + MCP |
| Stat Cards | 4-col grid, pastel bg | Background blob, icon box, value, label, trend |
| Cards | rounded-2xl (16px), shadow-ambient | `box-shadow: 0px 8px 24px` |
| Chart Card | Gradient area chart | ApexCharts, primary + secondary-fixed-dim |
| Hero Banner | Gradient #5D87FF → #A5C0FF | Welcome text + status badges |
| Buttons | rounded-xl, 14px | Primary + Ghost (border) styles |
| Timer | Smooth cubic-bezier | Page transitions .3s ease |
| MCP Dropdown | absolute, 360px, blur bg | Toggle from topbar, auto-close on click outside |
| Service Badge | inline-flex, 11px | Pill di status bar — count services online |

### 3.4 Icons

Menggunakan **Material Symbols** (Google) + **FontAwesome 6** — kombinasi ~60+ icon di dashboard:

```
Material Symbols: dashboard, chat, psychology, sticky_note_2, task_alt,
monitoring, terminal, settings, hub, cloud_done, view_in_ar, smart_toy,
groups, account_tree, menu_book, compare_arrows, local_dining,
library_books, palette, folder, smartphone, construction, add,
search, dark_mode, notifications, schedule, language, restart_alt,
description, storefront, warning, update, memory, storage, verified,
more_vert, arrow_upward, check_circle, monitor_heart, refresh, timer

FontAwesome: microphone, paperclip, arrow-up, volume-off, volume-up,
search, youtube, whatsapp, qrcode, comment, coins, chart-line, clock
```

---

## 4. 20 Halaman Dashboard

### 4.1 Overview (Halaman Utama)

**Layout:**
```
Hero Banner Gradient → "Welcome back, Bisma 👋"
  ├── 4 Stat Cards (Services Up, AI Models, Containers, Bot Status)
  ├── System Performance Chart (ApexCharts area — CPU/RAM 12-month)
  ├── Resource Usage (radial + uptime ring 98%)
  ├── System Resources (progress bars: CPU, RAM, Disk)
  ├── Active Agents (Bisma, ResearchX, TradeX, WebDev)
  ├── Satisfaction (SVG donut 85%)
  ├── Quick Actions (Restart Bot, Briefing, Docker, Market)
  └── Recent Events (timeline from event bus, 10 terakhir)
```

**API:** `GET /api/ecosystem` — aggregator data: health, services, system, docker, heal, events

### 4.2 Chat

**Fitur:**
- 24 AI Agents: researchx, opencode, claudecode, tradex, webdev, bizmind, dr_pharma, flowbot + 16 ECC agents
- SSE streaming via `POST /api/chat/stream`
- Session management (CRUD sidebar, auto-title)
- Model selector grouped (52 models via 9Router in optgroups)
- YouTube panel (search, transcript, analyze via 9Router AI)
- File attachment (image, txt, py, js, md, json, csv, html, css)
- Markdown rendering
- MCP toggle button → dropdown panel
- Voice input (Web Speech API, id-ID)

### 4.3 System Dashboard **(NEW — 14 Juni)**

Halaman monitoring komprehensif dengan auto-refresh 15 detik.

```
┌──────────────────────────────────────────────────────────┐
│ System Dashboard                    [Refresh button]     │
├──────────────────────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                    │
│ │8/8   │ │15GB  │ │316G  │ │890h  │                    │
│ │Svc Up│ │RAM   │ │Free  │ │Uptime│                    │
│ └──────┘ └──────┘ └──────┘ └──────┘                    │
├──────────────────┬───────────────────────────────────────┤
│ Services          │ Docker Containers                    │
│ 🟢 odysseus up    │ 🟢 redis Up 3h                      │
│ 🟢 ollama up      │ 🟢 qdrant Up 17h                    │
│ 🟢 qdrant up      │ 🟢 meilisearch Up 25h               │
│ 🟢 n8n up         │ 🟢 open-notebook Up 25h             │
│ 🟢 wa-bridge up   │ 🟢 n8n Up 24h                      │
│ ...               │ ...                                 │
├──────────────────┼───────────────────────────────────────┤
│ Resource Usage    │ MCP Servers                          │
│ CPU ██████░░ 6%   │ 🟢 inxotive-market 6 tools          │
│ RAM ████████ 84%  │ 🟢 inxotive-youtube 6 tools         │
│ Disk █████░░░ 32% │ 🟢 inxotive-knowledge 5 tools       │
│ Uptime: 890h      │ 🟢 inxotive-system 7 tools          │
├──────────────────┼───────────────────────────────────────┤
│ Self-Healing Stats│ Scheduled Tasks                      │
│ Incidents: 47     │ (systemd timers)                     │
│ Rules Active: 12  │                                      │
├──────────────────────────────────────────────────────────┤
│ Quick Actions: [Restart Bot] [Health Check] [Disk]      │
│               [Uptime] [Memory]                          │
└──────────────────────────────────────────────────────────┘
```

### 4.4 Brainstorm

- 3 modes: Sequential, Debate, Hierarchical
- Multi-agent orchestration via SSE streaming
- Export sebagai Markdown file
- Pilih agent: ResearchX, BizMind, WebDev, FlowBot

### 4.5 Pipeline

- Client leads tracking dari `leads.json`
- 5-stage pipeline: Intake → Analysis → Proposal → Deploy → Live
- Quick intake form (`/intake`) + deploy link

### 4.6 Knowledge

- Qdrant semantic search via `/knowledge`
- Score bars show match percentage
- Detail modal (showModal)
- Tab: Search Results + Ask AI

### 4.7 Market (Crypto)

**Data real-time (CoinGecko via cache 30s):**
- 5 coins: Bitcoin, Ethereum, BNB, Solana, XRP
- Fear & Greed Index (nilai + klasifikasi)
- BTC Technical: RSI (signal), Trend, MACD, Support/Resistance
- News feed (8+ articles)
- Auto-refresh 60 detik

### 4.8 Docker

- 8 containers: redis, qdrant, meilisearch, open-notebook (2), n8n, portainer, uptime-kuma
- Status (running/stopped), image, ports display
- Auto-refresh 30 detik

### 4.9 WhatsApp Bridge

- Connection status (connected/disconnected)
- QR code viewer (base64 image)
- Link ke WA Bridge Web UI (`:3002`)
- Auto-refresh 10 detik

### 4.10 Terminal

- Web shell via `POST /exec` (bash, timeout 30s)
- History (arrow up/down)
- Special commands: `help`, `clear`, `inxo` (system status), `history`
- Safety block: rm -rf /*, mkfs, dd if=

### 4.11 Files

- File browser breadcrumb
- Directory listing with size/date
- Click directory to navigate, file to view
- Path relative ke `/home/bisma`

### 4.12 Settings

- Theme toggle (sync ke Odyssey)
- Default Agent selector (24 agents)
- Default Model selector
- Refresh interval (15s/30s/60s)
- System Info (OS, host, uptime, services)
- Autodream — Memory Consolidation (Consolidate/Daily/Insights)
- Hooks — Event Pipeline (list/refresh)
- Scheduled Tasks (list systemd timers)

### 4.13-4.20 Odyssey Features (via Proxy)

| Halaman | Fitur | API Endpoint |
|---------|-------|-------------|
| Brain | Memory CRUD + search | `/api/odyssey/memory` |
| Notes | Keep-style notes + create | `/api/odyssey/notes` |
| Tasks | Scheduled tasks + status | `/api/odyssey/tasks` |
| Calendar | Events by month | `/api/odyssey/calendar/events` |
| Compare | A/B model comparison (placeholder) | - |
| Cookbook | Model download/serve state | `/api/odyssey/cookbook/state` |
| Library | Documents list | `/api/odyssey/documents` |
| Themes | 6 themes + apply | `/api/odyssey/prefs` |

---

## 5. API Endpoints

### 5.1 Direct (Market API)

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/hub` | Dashboard HTML (190KB, 3720 baris) |
| GET | `/api/ecosystem` | Aggregator: 8 health + services + system + 8 docker + market + heal + events |
| GET | `/api/market/overview` | 5 crypto prices + Fear&Greed + BTC technical + news |
| GET | `/api/docker/ps` | Container list (name, image, status, ports, created, size) |
| GET | `/api/wa/status` | WhatsApp Bridge status (connected, qr, qr_base64) |
| GET | `/api/files` | Directory listing (path relatif, sort: dirs first) |
| GET | `/api/files/read` | Read text file (max 100KB) |
| GET | `/api/models` | 52 models (3 Ollama + 49 9Router) |
| POST | `/api/chat/stream` | SSE streaming chat (agent, model, mcp_enabled) |
| GET | `/status` | Services status + disk + uptime |
| POST | `/exec` | Command execution (bash, timeout 30s, safety block) |
| GET | `/events` | Event bus (10 last events) |
| GET | `/heal/stats` | Self-healing stats (incidents, patterns, rules) |
| GET | `/knowledge` | Qdrant unified search |
| GET | `/brief` | Market brief (crypto + TA + news) |
| GET | `/leads` | Client intake leads (last 20) |
| POST | `/intake` | Client intake form |
| GET | `/portal/{nama}` | Client portal per-lead |

### 5.2 MCP Endpoints

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/api/mcp/servers` | List 7 servers + connection status + tools |
| POST | `/api/mcp/connect` | Connect server(s) |
| POST | `/api/mcp/disconnect` | Disconnect server(s) |
| POST | `/api/mcp/call` | Call MCP tool |
| POST | `/api/mcp/search-tools` | Search tools by name/description |
| POST | `/api/mcp/chat` | Chat with MCP context |

### 5.3 Autodream Endpoints

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/api/autodream/status` | Last consolidation run time |
| POST | `/api/autodream/consolidate` | Full memory consolidation |
| POST | `/api/autodream/daily` | Lightweight daily consolidation |
| GET | `/api/autodream/insights` | Usage insights report |

### 5.4 Hooks & Tasks

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/api/hooks` | List registered hooks |
| POST | `/api/hooks` | Register new hook |
| DELETE | `/api/hooks/{name}` | Delete hook |
| GET | `/api/scheduled-tasks` | List systemd timers + custom tasks |
| POST | `/api/scheduled-tasks` | Create scheduled task |
| DELETE | `/api/scheduled-tasks/{id}` | Delete task |

### 5.5 Session Management

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/api/sessions` | List sessions (sorted newest) |
| POST | `/api/sessions` | Create session |
| DELETE | `/api/sessions/{id}` | Delete session |
| GET | `/api/sessions/{id}/history` | Chat history |
| POST | `/api/sessions/{id}/title` | Update title |

### 5.6 Odyssey Proxy

| Method | Endpoint | Forward ke |
|--------|----------|------------|
| GET | `/api/odyssey/{path}` | `GET :7000/api/{path}` |
| POST | `/api/odyssey/{path}` | `POST :7000/api/{path}` |
| DELETE | `/api/odyssey/{path}` | `DELETE :7000/api/{path}` |

Auth: Auto-login `bisma` + session cookie persist ke `~/.odyssey_cookie`
CSRF token handling untuk POST/DELETE

---

## 6. Ekosistem Services

### 6.1 Systemd Services

| Service | Port | Status | Fungsi |
|---------|------|--------|--------|
| `market-api` | 8888 | ✅ UP | Backend hub + proxy |
| `odysseus` | 7000 | ✅ UP | AI Workspace (6 persona) |
| `ollama` | 11434 | ✅ UP | Local LLMs (3 models) |
| `inxotive-bot` | 8080 | ✅ UP | Discord Bot (38 commands) |
| `ssh` | 22 | ✅ UP | SSH access |
| `casaos` | 80 | ✅ UP | CasaOS management |

### 6.2 Docker Containers (8)

| Container | Port | Status | Fungsi |
|-----------|------|--------|--------|
| redis | 6379 | ✅ UP 3h | Market data cache |
| qdrant | 6333-6334 | ✅ UP 17h | Vector database |
| meilisearch | 7700 | ✅ UP 25h | Full-text search |
| open-notebook | 8502, 5055 | ✅ UP 25h | AI notebook |
| n8n | 5678 | ✅ UP 24h | Workflow automation |
| portainer | 9000 | ✅ UP 25h | Docker management |
| uptime-kuma | 3001 | ✅ UP 25h (healthy) | Uptime monitoring |
| open-notebook (surrealdb) | - | ✅ UP 25h | SurrealDB untuk notebook |

### 6.3 Additional Services

| Service | Port | Status | Deskripsi |
|---------|------|--------|-----------|
| 9Router | 20128 | ✅ UP | Multi-model AI routing (49 models) |
| WA Bridge | 3002 | ✅ UP | WhatsApp Web bridge |
| INXOTIVE Builder | 7777 | ✅ UP | Web Agency Builder (systemd user) |

---

## 7. MCP Servers

### 7.1 Active Connections (7 servers, 55 tools)

| Server | Port | Tools | Status | Fungsi |
|--------|------|-------|--------|--------|
| **inxotive-market** | 8101 | 6 | 🟢 Connected | Crypto prices, TA, news, fear-greed index |
| **inxotive-youtube** | 8102 | 6 | 🟢 Connected | YouTube search, transcript, AI analysis via 9Router |
| **inxotive-knowledge** | 8103 | 5 | 🟢 Connected | Qdrant semantic search, collections management |
| **inxotive-system** | 8104 | 7 | 🟢 Connected | Server status, services, Docker, journalctl, events |
| **inxotive-agentshield** | 8105 | 5 | 🟢 Connected | Security audit: secrets scan, OWASP vuln scan, dependency check |
| **github** | stdio | 26 | 🟢 Connected | GitHub API: repos, issues, PRs, commits, search |
| **inxotive-web** | - | 0 | ⚪ Disconnected | Web tools (not configured) |

### 7.2 MCP in Hub UI

- **Toggle button** `MCP ▾` di topbar (sebelah YouTube button)
- **Dropdown panel** (360px, backdrop blur) — muncul saat diklik, close otomatis klik luar
- Tampilkan: server name, connection status, command, tools list
- **Toggle switch** enable/disable MCP di chat streaming
- Auto-load via `refreshMCPPanel()` 1 detik setelah load
- Dot indicator: 🟢 connected, 🔴 all down

---

## 8. 9Router AI Models

### 8.1 Model Inventory (52 total)

| Kategori | Model Count | Prefix | Contoh |
|----------|-------------|--------|--------|
| 🔥 **Fast Routes** | 6 | `9r/` | maximize-claude, power-max, max-free, bo |
| 🔮 **Claude Direct (CC)** | 8 | `9r/cc/` | claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5 |
| 🤖 **Key-based Claude (KC)** | 8 | `9r/kc/` | anthropic/claude-sonnet-4, google/gemini-2.5-pro, openai/gpt-4.1 |
| 🔄 **Koyeb Router (KR)** | 12 | `9r/kr/` | claude-sonnet-4.5, deepseek-3.2, qwen3-coder-next |
| 💎 **Gemini** | 5 | `9r/gemini/` | gemini-3.1-pro-preview, gemini-3.1-flash-lite, gemma-4-31b |
| ⚡ **Groq** | 4 | `9r/groq/` | llama-3.3-70b, llama-4-maverick, qwen3-32b |
| 🖥️ **9Router Ollama** | 6 | `9r/ollama/` | gpt-oss:120b, kimi-k2.5, glm-5, minimax-m2.5 |
| 🦙 **Local Ollama** | 3 | `ollama/` | qwen2.5:3b, llama3.1:8b, hermes3:8b |

### 8.2 Model Selector UI

- **Optgroup dropdown** di topbar (max-width 220px)
- Helper: group labels + simplified names
- Save pilihan ke `localStorage` (`hub_model`)
- Default: `9r/max-free`
- Update model info di status bar (`9R max-free`)

---

## 9. Status & Metrik

### 9.1 Real-time Metrics (14 Juni 2026)

| Metrik | Value | Sumber |
|--------|-------|--------|
| Services Up | 8/8 (100%) | `/api/ecosystem` |
| AI Models | 52 | `/api/models` |
| Docker Containers | 8 | `/api/docker/ps` |
| MCP Servers | 7 (6 connected) | `/api/mcp/servers` |
| MCP Tools | 55 | `/api/mcp/servers` |
| Hub Agents | 24 | `AGENTS` constant |
| RAM Usage | ~13.5GB / 15.3GB (88%) | `/api/ecosystem` |
| CPU Load | ~6% | `/api/ecosystem` |
| Disk Free | 316G / 468G (67%) | `/api/ecosystem` |
| Uptime | ~890 jam (37 hari) | `/proc/uptime` |

### 9.2 Heal Patterns (Self-Healing Stats)

| Pattern | Count |
|---------|-------|
| bot_crash | 25x |
| ollama_timeout | 16x |
| market_api_5xx | 0 |
| disk_low | 0 |
| memory_pressure | 0 |

### 9.3 Status Bar

```
┌──────────────────────────────────────────────────────────────┐
│ Ready  🟢 8/8 online    |    9R max-free    [🔇]           │
└──────────────────────────────────────────────────────────────┘
```

- **Left**: Status text ("Ready", "Mengetik...", "Error")
- **Badge**: Live service count (`updateSvcBadge()` — hijau jika semua up, kuning jika sebagian, merah jika banyak down)
- **Center**: Spacer
- **Model info**: Prefix + short name (dari `updateModelInfo()`)
- **TTS toggle**: Text-to-Speech on/off

### 9.4 File Stats (Akurat per 14 Juni)

| File | Baris | Ukuran |
|------|-------|--------|
| `hub.html` | 3720 | 188KB |
| `app.py` | 2627 | 120KB |
| `mcp_client.py` | 317 | 12KB |
| `youtube_service.py` | 994 | ~25KB |

---

## 10. Bug Tracker & Fixes

### 10.1 Critical Bugs Fixed (14 Juni 2026)

| # | Bug | Root Cause | Fix |
|---|-----|------------|-----|
| C1 | **Hub mati total — gak bisa klik apapun** | `loadWA()` function missing 2 closing braces (`});` dan `}`) — semua 133 fungsi JS setelahnya dianggap bagian dari catch block | Added `}); }` di `loadWA()` — semua fungsi balanced (verified: 133/133) |
| C2 | **System content numpuk di semua halaman** | Unclosed HTML comment `<!-- Second Row: Resource Bars + MCP` tanpa `-->` — semua content setelahnya jadi komentar | Closed comment → `-->` — 57/57 comments matched |
| C3 | **Welcome page tdk responsive** | `currentPage = 'overview'` bikin navigateTo gak jalan di first load, `.welcome` missing flex layout | `currentPage = null`, `.welcome` properti flex:1 + flex-direction:column |
| C4 | **Voice btn green glow di luar textbox** | `display: flex` tanpa `overflow: hidden` + animasi `voicePulse` dengan box-shadow | `display: inline-flex`, `overflow: hidden`, hapus animasi `voicePulse` |
| C5 | **TTS green overflow** | Sama dengan voice — kurang `overflow: hidden` | Added `overflow: hidden` |
| C6 | **Model select gak muncul dropdown** | Missing `appearance: auto` + dropdown arrow SVG | Added `appearance: auto; cursor: pointer;` + SVG chevron (light + dark) |
| C7 | **6 missing DOM elements** | `sessionList`, `sessionsSection`, `eventsList`, `overviewStatus`, `tasksList`, `uptimeRing` hanya di JS, gak ada di HTML | Added semua element ke HTML |

### 10.2 Previous Bugs (from v1)

| # | Bug | Status | Fix |
|---|-----|--------|-----|
| P1 | CSS typo `grid-template-cols` | ✅ Fixed | → `grid-template-columns` |
| P2 | Market `name 'time' is not defined` | ✅ Fixed | Added `import time` |
| P3 | Calendar query params missing | ✅ Fixed | Using `request.query_params` |
| P4 | Ecosystem self-blocking HTTP | ✅ Fixed | Subprocess + file reads |
| P5 | Duplicate `initBrain` function | ✅ Fixed (false positive) | Substring match fix |
| P6 | MCP startup timeout (Gmail OAuth) | ✅ Fixed | `asyncio.wait_for(10s)` |

### 10.3 Enhancement (14 Juni 2026)

| # | Enhancement | Detail |
|---|-------------|--------|
| E1 | **System Dashboard page** | Halaman baru #20 — services, docker, resources, MCP, heal, tasks + quick actions (auto-refresh 15s) |
| E2 | **MCP Panel → Dropdown** | Floating panel dihapus, ganti dropdown di topbar (close on click outside) |
| E3 | **Model grouped select** | 52 models di-organize dalam 8 optgroups dengan label icon |
| E4 | **Service badge** | `svcBadge` di status bar — live count services online |
| E5 | **Sidebar sessions** | `sessionsSection` + `sessionList` di sidebar bawah (hidden saat bukan chat page) |

---

## 11. Cara Menggunakan

### 11.1 Akses Dashboard

```
http://localhost:8888/hub
```

### 11.2 Navigasi

1. **Mini Rail** (kiri, 80px) — 7 shortcut ke halaman utama: Overview, Chat, Brain, Market, Terminal, Settings
2. **Sidebar** (kiri, 260px) — semua 20 halaman dalam 4 grup: Main (5), Odyssey (8), Tools (6), System (3)
3. **TopBar** (atas, 56px) — dinamis per halaman: agent tabs, model select, YouTube, MCP toggle
4. **Theme toggle** — pojok kanan topbar (light/dark, sync ke Odyssey)

### 11.3 Restart Services

```bash
# Restart dashboard (user service)
systemctl --user restart market-api

# Restart via sudo
sudo systemctl restart market-api
sudo systemctl restart inxotive-bot

# Check logs
sudo journalctl -u market-api -n 30 --no-pager
sudo journalctl -u inxotive-bot -n 30 --no-pager
```

### 11.4 Development

```bash
# Edit dashboard
nano ~/market-api/hub.html

# Edit backend
nano ~/market-api/app.py

# Restart after changes
pkill -f "uvicorn.*8888"
cd ~/market-api && python3 -m uvicorn app:app --host 0.0.0.0 --port 8888
```

### 11.5 Quick Commands

```bash
# Test dashboard
curl localhost:8888/hub | head -5

# Test ecosystem API
curl -s localhost:8888/api/ecosystem | python3 -m json.tool

# Test market
curl -s localhost:8888/api/market/overview | python3 -m json.tool

# Test Docker
curl -s localhost:8888/api/docker/ps | python3 -m json.tool

# Test MCP servers
curl -s localhost:8888/api/mcp/servers | python3 -m json.tool

# Test models
curl -s localhost:8888/api/models | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'{len(d)} models')
for m in d: print(f'  {m[\"id\"]}')
"

# Check service logs
tail -30 /tmp/market-api.log
```

### 11.6 Claude Code Web Integration

Dokumen ini bisa digunakan oleh **Claude Code Web** untuk memahami arsitektur INXOTIVE HUB sebelum melakukan perubahan. Langkah:

1. Upload file ini ke Claude Code Web
2. Katakan: "Update INXOTIVE HUB berdasarkan review ini"
3. Claude akan membaca struktur, endpoints, dan bug tracker
4. Semua perbaikan bisa dilakukan langsung

---

## Appendix A: Kode CSS Variables (Design Tokens)

```css
--primary: #1f53c9;
--surface: #f8f9ff;
--outline: #747685;
--on-surface: #061c34;
--on-surface-variant: #434654;
--surface-container: #e6eeff;
--surface-container-lowest: #ffffff;
--surface-container-high: #dde9ff;
--surface-container-highest: #d3e3ff;
--primary-fixed-dim: #b4c5ff;
--secondary-fixed-dim: #18dfba;

/* INXOTIVE Brand */
--sidebar-bg: #ffffff;
--sidebar-text: #434654;
--sidebar-icon: #7C8FAC;
--primary-hover: #628aff;
```

## Appendix B: 24 AI Agents

### Original (8)
| Agent | Role |
|-------|------|
| ResearchX | Data gathering & research |
| OpenCode | System architect |
| ClaudeCode | General coding assistant |
| TradeX | Crypto market analysis |
| WebDev | Web development & UI |
| BizMind | Business strategy |
| Dr.Pharma | Pharmaceutical consultation |
| FlowBot | Workflow automation |

### ECC Extended (16)
| Agent | Role |
|-------|------|
| SecurityX | Security audit |
| ArchitectX | Software architecture |
| CodeReview | Code review |
| Simplifier | Code simplification |
| PerfOpt | Performance optimization |
| Debugger | Debugging |
| A11y | Accessibility |
| DataX | Data analysis |
| DevOpsX | Infrastructure |
| Compliance | Compliance checking |
| Planner | Project planning |
| CodeExplorer | Codebase exploration |
| SilentHunter | Silent bug hunting |
| PythonReview | Python-specific review |
| FastAPIX | FastAPI optimization |
| RefactorX | Code refactoring |
| DbX | Database optimization |
| NetFix | Network troubleshooting |
| SeoX | SEO optimization |

## Appendix C: Auto-Discovery (Self-Healing)

Sistem self-healing otomatis berjalan setiap 5 menit:
1. Cek semua 8 service health endpoints
2. Jika service down → coba restart via systemctl
3. Log incident ke `~/.heal_learn.json`
4. Pattern matching untuk root cause
5. Update `patterns` + `rules` di heal stats

Rules aktif saat ini:
- bot_crash → restart inxotive-bot
- ollama_timeout → restart ollama
- market_api_5xx → restart market-api

## Appendix D: Responden untuk Review

Mohon review:

1. **UI/UX** — Apakah layout 3-level (rail + sidebar + content) sudah optimal?
2. **Fitur** — Fitur apa yang perlu ditambah? (Research, Email, Gallery?)
3. **Kinerja** — Single 188KB file vs multi-file approach?
4. **Keamanan** — Auth proxy + command safety block sudah cukup?
5. **MCP Integration** — 55 tools di dropdown — perlu search/filter?
6. **Odyssey Integration** — Perlu tambah fitur dari Odyssey?

---

*Dokumen ini digenerate oleh Claude Code untuk review INXOTIVE HUB dashboard v2.2*
*Server: inxotive-server | Owner: Bisma | 14 Juni 2026*
