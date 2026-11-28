"""A tiny stdio MCP server for tests: says which folder it was started in and what that folder holds."""

import json
import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("cwd")


@server.tool(description="The working folder and its entries, as JSON.")
def where() -> str:
    here = os.getcwd()
    return json.dumps({"cwd": here, "entries": sorted(os.listdir(here))})


if __name__ == "__main__":
    server.run("stdio")
