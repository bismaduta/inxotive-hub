"""
MCP Client Manager — lightweight JSON-RPC over stdio.
No heavy SDK dependency. Connects to any MCP server via subprocess.
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp-client")

HOME = Path.home()
SETTINGS_PATH = HOME / ".claude" / "settings.json"


class MCPClient:
    """Low-level MCP client over stdio — sends JSON-RPC, receives responses."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self._proc = None
        self._reader = None
        self._writer = None
        self._tools: List[Dict] = []
        self._connected = False
        self._lock = asyncio.Lock()
        self._msg_id = 0

    @property
    def connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.returncode is None

    async def connect(self, timeout: int = 10) -> bool:
        """Spawn the server process and initialize MCP session."""
        async with self._lock:
            if self._connected:
                return True

            try:
                cmd = self.config.get("command", "")
                args = self.config.get("args", [])
                env = self.config.get("env", {})

                if not cmd:
                    logger.warning("[%s] No command", self.name)
                    return False

                merged_env = None
                if env:
                    merged_env = {**os.environ, **{k: str(v) for k, v in env.items()}}

                self._proc = await asyncio.create_subprocess_exec(
                    cmd, *args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=merged_env,
                )

                self._reader = self._proc.stdout
                self._writer = self._proc.stdin

                # Initialize MCP session
                result = await self._request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "inxotive-hub", "version": "1.0.0"},
                }, timeout=timeout)

                if result and "serverInfo" in result:
                    self._connected = True
                    await self._refresh_tools()
                    logger.info("[%s] Connected — %d tools", self.name, len(self._tools))
                    return True
                else:
                    logger.warning("[%s] Init failed: %s", self.name, result)
                    await self._cleanup()
                    return False

            except asyncio.TimeoutError:
                logger.warning("[%s] Timeout (%ds)", self.name, timeout)
                await self._cleanup()
                return False
            except Exception as e:
                logger.warning("[%s] Error: %s", self.name, e)
                await self._cleanup()
                return False

    async def _request(self, method: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
        """Send JSON-RPC request, read single-line response."""
        self._msg_id += 1
        req = {"jsonrpc": "2.0", "id": self._msg_id, "method": method}
        if params:
            req["params"] = params

        if not self._writer:
            return None

        try:
            line = json.dumps(req, ensure_ascii=False) + "\n"
            self._writer.write(line.encode())
            await self._writer.drain()

            resp_line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
            if not resp_line:
                return None

            resp = json.loads(resp_line.decode())
            if "error" in resp:
                logger.warning("[%s] RPC error: %s", self.name, resp["error"])
                return None
            return resp.get("result")

        except asyncio.TimeoutError:
            logger.warning("[%s] Timeout: %s", self.name, method)
            raise
        except Exception as e:
            logger.warning("[%s] Error: %s", self.name, e)
            return None

    async def _refresh_tools(self):
        result = await self._request("tools/list", timeout=8)
        if result and "tools" in result:
            self._tools = []
            for t in result["tools"]:
                self._tools.append({
                    "name": t.get("name", ""),
                    "description": (t.get("description", "") or "")[:200],
                    "input_schema": t.get("inputSchema", t.get("input_schema", {})),
                })
        else:
            self._tools = []

    async def list_tools(self) -> List[Dict]:
        if not self._connected:
            return []
        if not self._tools:
            await self._refresh_tools()
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict = None) -> dict:
        if not self._connected:
            return {"success": False, "error": f"Server '{self.name}' not connected", "tool": tool_name, "server": self.name}

        result = await self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        }, timeout=30)

        if not result:
            return {"success": False, "error": "No response", "tool": tool_name, "server": self.name}

        content = result.get("content", [])
        is_error = result.get("isError", False)

        contents = []
        for c in content:
            ct = c.get("type", "text")
            if ct == "text" and c.get("text"):
                contents.append({"type": "text", "text": c["text"]})
            else:
                contents.append({"type": "text", "text": str(c)[:500]})

        return {
            "success": not is_error,
            "content": contents,
            "error": contents[0]["text"] if is_error and contents else None,
            "tool": tool_name,
            "server": self.name,
        }

    async def _cleanup(self):
        self._connected = False
        self._tools = []
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                pass
        self._proc = None
        self._reader = None
        self._writer = None

    async def disconnect(self):
        async with self._lock:
            await self._cleanup()


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self.servers: Dict[str, MCPClient] = {}
        self._initialized = False

    async def init(self, config_path: Optional[str] = None):
        if self._initialized:
            return
        path = Path(config_path) if config_path else SETTINGS_PATH
        if not path.exists():
            self._initialized = True
            return
        try:
            data = json.loads(path.read_text())
            for name, config in data.get("mcpServers", {}).items():
                self.servers[name] = MCPClient(name, config)
            logger.info("MCP: %d configs loaded", len(self.servers))
        except Exception as e:
            logger.error("MCP config error: %s", e)
        self._initialized = True

    async def connect_all(self, concurrency: int = 3) -> Dict[str, str]:
        results = {}
        sem = asyncio.Semaphore(concurrency)

        async def _connect(name: str, client: MCPClient):
            async with sem:
                try:
                    ok = await asyncio.wait_for(client.connect(), timeout=12)
                    results[name] = "connected" if ok else "failed"
                except Exception:
                    results[name] = "failed"

        tasks = [_connect(n, c) for n, c in self.servers.items()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def connect_server(self, name: str) -> bool:
        conn = self.servers.get(name)
        return await conn.connect() if conn else False

    async def disconnect_all(self):
        for c in self.servers.values():
            await c.disconnect()

    async def list_all_tools(self, refresh: bool = False) -> Dict[str, List[Dict]]:
        result = {}
        for name, conn in self.servers.items():
            if conn.connected:
                tools = await conn.list_tools()
                if tools:
                    result[name] = tools
        return result

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict = None) -> dict:
        conn = self.servers.get(server_name)
        if not conn:
            return {"success": False, "error": f"Server '{server_name}' not found", "tool": tool_name, "server": server_name}
        if not conn.connected:
            ok = await conn.connect()
            if not ok:
                return {"success": False, "error": f"Could not connect to '{server_name}'", "tool": tool_name, "server": server_name}
        return await conn.call_tool(tool_name, arguments)

    async def search_tools(self, query: str) -> List[Dict]:
        results = []
        q = query.lower()
        all_tools = await self.list_all_tools()
        for srv, tools in all_tools.items():
            for t in tools:
                if q in t["name"].lower() or q in t["description"].lower():
                    results.append({**t, "server": srv})
        return results

    def get_server_status(self) -> List[Dict]:
        return [
            {"name": name, "connected": conn.connected, "tools_count": len(conn._tools) if conn.connected else 0, "command": conn.config.get("command", "")}
            for name, conn in self.servers.items()
        ]


mcp_manager = MCPManager()


async def init_mcp():
    await mcp_manager.init()
    status = await mcp_manager.connect_all()
    connected = [n for n, s in status.items() if s == "connected"]
    failed = [n for n, s in status.items() if s == "failed"]
    if connected:
        logger.info("MCP connected: %s", ", ".join(connected))
    if failed:
        logger.warning("MCP failed: %s", ", ".join(failed))
    return status


def format_tool_result_for_context(result: dict) -> str:
    if not result.get("success"):
        return f"\n[MCP Tool: {result.get('server')}/{result.get('tool')} — Error: {result.get('error')}]\n"
    content = result.get("content", [])
    text_parts = [c["text"] for c in content if c.get("type") == "text" and c.get("text")]
    combined = "\n".join(text_parts) if text_parts else "(no text output)"
    return f"\n[MCP Tool Result: {result.get('server')}/{result.get('tool')}]\n{combined[:3000]}\n[End Tool Result]\n"


def format_tool_list_for_context(tools: Dict[str, List[Dict]]) -> str:
    if not tools:
        return ""
    lines = ["\n## Available MCP Tools", ""]
    for srv, tool_list in tools.items():
        lines.append(f"### {srv}")
        for t in tool_list:
            desc = (t.get("description", "") or "")[:120]
            schema = t.get("input_schema", {})
            params = list(schema.get("properties", {}).keys()) if schema else []
            params_str = f" ({', '.join(params)})" if params else ""
            lines.append(f"- `{t['name']}`{params_str}: {desc}")
        lines.append("")
    lines.append("To use a tool, respond with: `[USE TOOL: server/tool_name]` with JSON arguments.")
    return "\n".join(lines)
