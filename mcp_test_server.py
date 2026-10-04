"""Minimal stdio MCP server used by test_mcp.py."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the result."""
    return a * b


@mcp.tool()
def get_capital(country: str) -> str:
    """Return the capital city of the given country."""
    capitals = {"france": "Paris", "japan": "Tokyo", "sri lanka": "Sri Jayawardenepura Kotte"}
    return capitals.get(country.lower(), f"Unknown capital for {country}")


@mcp.tool()
def delete_all_files() -> str:
    """Delete all files. Not selected in test_mcp.py's config, to check tool filtering."""
    return "This tool should never be loaded."


if __name__ == "__main__":
    mcp.run()
