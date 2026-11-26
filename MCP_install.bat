@echo off
cd /d "%~dp0"
rem Add this program as an MCP server in the Claude desktop app (read-only tools)
python mcp_server.py --install
