# INXOTIVE HUB — Development Workflow (CLI ↔ Web)

> **Dibuat:** 14 Juni 2026
> **Tujuan:** Workflow collaboration antara Claude Code (terminal) dan Claude Code Web (claude.ai/code)
> **Repo:** `github.com/bismaduta/inxotive-hub`

---

## 🔄 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     DEVELOPMENT CYCLE                               │
│                                                                     │
│  ┌──────────────┐                ┌──────────────┐                  │
│  │  Claude Code │  git push      │   GitHub      │                  │
│  │  (Terminal)  │ ─────────────► │  inxotive-hub │                  │
│  │  ~/market-api│                │  main branch  │                  │
│  └──────────────┘                └──────┬───────┘                  │
│       ▲                                 │                           │
│       │ sync                            │ read code                 │
│       │ cp + restart                    ▼                           │
│       │                        ┌──────────────┐                    │
│       │                        │ Claude Code  │                    │
│       │                        │ Web          │                    │
│       │                        │ claude.ai/code│                   │
│       │                        └──────┬───────┘                    │
│       │                               │                             │
│       │                    edit + commit + push                     │
│       │                               │                             │
│       │                               ▼                             │
│       │                        ┌──────────────┐                    │
│       │                        │  .bundle     │                    │
│       │                        │  file        │                    │
│       │                        └──────┬───────┘                    │
│       │                               │                             │
│       │       download + fetch + merge                              │
│       ◄───────────────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Repositories

| Repo | URL | Fungsi |
|------|-----|--------|
| **inxotive-hub** | `github.com/bismaduta/inxotive-hub` | Source of truth untuk semua kode dashboard |
| **market-api** | `github.com/bismaduta/market-api` | Server production (copy dari inxotive-hub) |

### Sync Policy
- **inxotive-hub** ← semua perubahan masuk sini dulu
- **market-api** ← copy file dari inxotive-hub setelah verified & tested

---

## 📋 Langkah Workflow Detail

### Langkah 1: CLI Audit (terminal ini)
```bash
# Lihat semua halaman + balance check
cd ~/market-api
python3 << 'EOF'
import re
html = open('hub.html').read()
pages = re.findall(r'id="page-(\w+)"', html)
opens = html.count('<div')
closes = html.count('</div>')
copens = html.count('<!--')
ccloses = html.count('-->')
print(f'Pages: {len(pages)} ({", ".join(pages)})')
print(f'Divs: {opens}/{closes} ({"✅" if opens==closes else "❌"})')
print(f'Comments: {copens}/{ccloses} ({"✅" if copens==ccloses else "❌"})')
EOF
```

### Langkah 2: Push perubahan ke GitHub
```bash
cd ~/market-api
git add hub.html app.py
git commit -m "fix: [deskripsi perubahan]"
git push origin main
```

### Langkah 3: Buka Claude Code Web
1. Buka `https://claude.ai/code`
2. Pilih repo `bismaduta/inxotive-hub`
3. Claude akan baca `CLAUDE.md` (di repo) + `INXOTIVE_HUB_REVIEW.md` untuk konteks
4. Beri task dengan format di bawah
5. Claude kerja, commit, push ke branch `claude/*`

### Langkah 4: Generate bundle dari Web
Di claude.ai/code, minta:
```
Generate a git bundle: git bundle create fixes.bundle claude/*
```

Download file `.bundle` dari web ke lokal.

### Langkah 5: Apply bundle di CLI
```bash
BUNDLE="/path/to/file.bundle"
cd /tmp && rm -rf inxotive-hub
git clone "https://bismaduta:TOKEN@github.com/bismaduta/inxotive-hub.git" /tmp/inxotive-hub
cd /tmp/inxotive-hub

# Verify bundle
git bundle verify "$BUNDLE"

# Fetch branch dari bundle
git fetch "$BUNDLE" claude/*:claude/*

# Lihat perubahan
git diff main..claude/* --stat

# Merge
git checkout main
git merge claude/* --no-edit

# Push ke GitHub
git push origin main
```

### Langkah 6: Deploy ke Production
```bash
# Copy ke server
cp /tmp/inxotive-hub/hub.html ~/market-api/hub.html
cp /tmp/inxotive-hub/app.py ~/market-api/app.py

# Restart
kill -9 $(lsof -ti:8888); sleep 2
cd ~/market-api && python3 -m uvicorn app:app --host 0.0.0.0 --port 8888 &
sleep 3

# Verify
curl -s -o /dev/null -w "HTTP: %{http_code}\n" http://localhost:8888/hub
```

### Langkah 7: Verify
```bash
curl -s http://localhost:8888/hub | python3 -c "
import sys, re
html = sys.stdin.read()
opens = html.count('<div')
closes = html.count('</div>')
pages = re.findall(r'id=\"page-(\w+)\"', html)
print(f'✅ HTTP 200, {len(html)} bytes')
print(f'✅ Pages: {len(pages)}')
print(f'✅ Divs: {opens}/{closes}')
"
```

---

## 📝 Task Template untuk Claude Code Web

Gunakan format ini saat memberi task ke claude.ai/code:

```
Tugas: [deskripsi singkat]

File: hub.html / app.py / both
Halaman: [nama page yang terpengaruh, misal: overview, system, settings]
Severity: critical / high / medium / low

Detail:
1. [langkah spesifik 1]
2. [langkah spesifik 2]
3. ...

Constraints:
- Jangan hapus [feature X]
- Pastikan [requirement Y]
- CSS harus inline di <style> tag
- Jangan tambah library baru

Testing:
- Pastikan div balance
- Pastikan semua onclick handler jalan
- Cek dark mode

Setelah selesai, generate .bundle file untuk saya download.
```

---

## 🏗️ Repo File Structure

```
inxotive-hub/
├── CLAUDE.md                      ← Context untuk Claude Code (di repo)
├── INXOTIVE_HUB_REVIEW.md         ← Full system audit document
├── hub.html                       ← Dashboard utama (4038 baris)
├── app.py                         ← FastAPI backend (2718 baris)
├── mcp_client.py                  ← MCP client manager
├── mcp_market_server.py           ← MCP server: market
├── mcp_youtube_server.py          ← MCP server: youtube
├── mcp_knowledge_server.py        ← MCP server: knowledge
├── mcp_system_server.py           ← MCP server: system
├── mcp_agentshield_server.py      ← MCP server: agentshield
├── run_mcp.py                     ← MCP runner
├── youtube_service.py             ← YouTube module
├── autodream.py                   ← Memory consolidation
├── channels.py                    ← Discord/Telegram
├── visuals.py                     ← Chart generation
├── filegen.py                     ← Excel/PDF/Invoice
├── remote.py                      ← QR remote control
├── verify.py                      ← Endpoint testing
├── agent_teams.py                 ← A2A coordination
├── ui_audit.py                    ← Visual UI audit
└── .gitignore
```

---

## ⚠️ Rules untuk Claude Code Web

Ketika claude.ai/code bekerja di repo `inxotive-hub`:

1. **Jangan hapus fitur yang ada** — edit/increment, jangan rewrite
2. **Pastikan div balance** (`<div` = `</div>`)
3. **Pastikan comment balance** (`<!--` = `-->`)
4. **Pastikan JS function balance** `{` = `}`
5. **CSS harus inline** di `<style>` tag hub.html
6. **Jangan tambah library baru** — pakai yang sudah ada (Tailwind CDN, FontAwesome, Material Symbols, ApexCharts)
7. **Commit message harus deskriptif**
8. **File yang di-edit:** biasanya `hub.html` (frontend) + `app.py` (backend)

---

## 🔄 Sync Commands (Quick Reference)

```bash
# === Push dari CLI ke GitHub ===
cd ~/market-api && git add -A && git commit -m "fix: desc" && git push origin main

# === Pull dari GitHub ke CLI ===
cd /tmp && rm -rf inxotive-hub
git clone "https://bismaduta:TOKEN@github.com/bismaduta/inxotive-hub.git" /tmp/inxotive-hub

# === Apply bundle dari Web ===
cd /tmp/inxotive-hub && git fetch "$BUNDLE" claude/*:claude/* && git checkout main && git merge claude/* --no-edit && git push origin main

# === Deploy ke production ===
cp /tmp/inxotive-hub/hub.html ~/market-api/hub.html && cp /tmp/inxotive-hub/app.py ~/market-api/app.py && kill -9 $(lsof -ti:8888) && cd ~/market-api && python3 -m uvicorn app:app --host 0.0.0.0 --port 8888 &

# === Verify ===
curl -s http://localhost:8888/hub | head -5
```

---

*Last updated: 14 Juni 2026*
*Owner: Bisma (bismaduta)*
