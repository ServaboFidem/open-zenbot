"""A tiny stdio MCP server for tests: two tools, no side effects."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("echo")


@server.tool(description="Echo the text back.")
def echo(text: str) -> str:
    return text


@server.tool(description="Add two integers.")
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    server.run("stdio")
