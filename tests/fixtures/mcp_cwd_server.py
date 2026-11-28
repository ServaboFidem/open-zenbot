"""A tiny stdio MCP server for tests: says which folder it was started in and what that folder holds."""

import json
import os
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("cwd")


@server.tool(description="The working folder and its entries, as JSON.")
def where() -> str:
    here = os.getcwd()
    return json.dumps({"cwd": here, "entries": sorted(os.listdir(here))})


@server.tool(description="Leave a file named `name` in the working folder.")
def leave(name: str) -> str:
    with open(os.path.join(os.getcwd(), name), "w", encoding="utf-8") as f:
        f.write("left behind")
    return "ok"


if __name__ == "__main__":
    time.sleep(float(os.environ.get("ZEN_TEST_START_DELAY", "0")))  # a slow starter, as paddleocr loading its models
    server.run("stdio")
