"""
MCP Server runner with transport selection.
Usage:
  python3 run_mcp.py market stdio        # for Claude Code
  python3 run_mcp.py market sse 8101     # for n8n / remote access
"""

import sys
import importlib.util
from pathlib import Path

SERVERS = {
    "market":   ("mcp_market_server",   "inxotive-market",   8101),
    "youtube":  ("mcp_youtube_server",  "inxotive-youtube",  8102),
    "knowledge":("mcp_knowledge_server","inxotive-knowledge", 8103),
    "system":   ("mcp_system_server",   "inxotive-system",   8104),
    "agentshield":("mcp_agentshield_server","inxotive-agentshield", 8105),
}

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <server> <transport> [port]")
        print(f"Servers: {', '.join(SERVERS.keys())}")
        print(f"Transport: stdio | sse")
        sys.exit(1)

    name = sys.argv[1]
    transport = sys.argv[2]

    if name not in SERVERS:
        print(f"Unknown server: {name}. Choose: {', '.join(SERVERS.keys())}")
        sys.exit(1)

    mod_name, mcp_name, default_port = SERVERS[name]
    port = int(sys.argv[3]) if len(sys.argv) > 3 else default_port

    # Import the module
    spec = importlib.util.spec_from_file_location(mod_name, str(Path(__file__).parent / f"{mod_name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mcp = getattr(mod, "mcp", None)
    if not mcp:
        print(f"Error: {mod_name}.py has no 'mcp' FastMCP instance")
        sys.exit(1)

    if transport == "sse":
        print(f"Starting {mcp_name} on SSE port {port}...")
        mcp.run(transport="sse", port=port)
    else:
        print(f"Starting {mcp_name} on stdio...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
