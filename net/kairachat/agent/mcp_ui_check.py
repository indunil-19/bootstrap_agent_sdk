"""Check which MCP tools return JSON data that a rich UI can render.

An MCP tool can feed a UI card only if its result includes structuredContent (JSON data). Tools that
declare an outputSchema always do; tools without one usually return text only. The agent passes that
JSON to frontends as the tool's artifact (chat()["tool_outputs"], stream_chat() "tool.finished").

Run from the repo root:

    # List every tool of every MCP server in the config, with its JSON fields
    python -m net.kairachat.agent.mcp_ui_check agent_config.json

    # Only one server
    python -m net.kairachat.agent.mcp_ui_check agent_config.json --server memory

    # Call a tool and show exactly what the UI would receive
    python -m net.kairachat.agent.mcp_ui_check agent_config.json --server memory --call read_graph
    python -m net.kairachat.agent.mcp_ui_check agent_config.json --server time --call get_current_time --args "{\"timezone\": \"Asia/Colombo\"}"
"""

import argparse
import asyncio
import json
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from net.kairachat.agent.mcp_loader import load_mcp_tools_from_config
from net.kairachat.agent.utils import expand_env


def _connections(servers: dict) -> dict:
    return {name: expand_env({k: v for k, v in config.items() if k != "tools"}) for name, config in servers.items()}


def _only_text(schema: dict) -> bool:
    """True if the JSON is just one string field, e.g. {"result": "..."}: text in a wrapper, not card data."""
    properties = list(schema.get("properties", {}).values())
    return len(properties) == 1 and properties[0].get("type") == "string"


def _fields(schema: dict) -> str:
    """Top-level fields of a JSON schema, e.g. 'entities: array, relations: array'."""
    properties = schema.get("properties", {})
    return ", ".join(f"{name}: {spec.get('type', 'any')}" for name, spec in properties.items()) or "(no fields listed)"


def _resolve(schema: dict, root: dict) -> tuple[dict, str | None]:
    """Follow a local "$ref" (e.g. "#/$defs/Hotel"); returns (schema, model name or None)."""
    ref = schema.get("$ref")
    if not (isinstance(ref, str) and ref.startswith("#/")):
        return schema, None
    target = root
    for part in ref[2:].split("/"):
        target = target.get(part, {}) if isinstance(target, dict) else {}
    return target, ref.rsplit("/", 1)[-1]


def _options(schema: dict) -> list[dict]:
    return schema.get("anyOf") or schema.get("oneOf") or []


def _type_name(schema: dict, root: dict) -> str:
    """Readable type, e.g. 'array of Hotel', 'number or null', '"single" | "double"'."""
    schema, model = _resolve(schema, root)
    if _options(schema):
        return " or ".join(dict.fromkeys(_type_name(option, root) for option in _options(schema)))
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    schema_type = schema.get("type", "object" if "properties" in schema else "any")
    if isinstance(schema_type, list):
        return " or ".join(schema_type)
    if schema_type == "array":
        return f"array of {_type_name(schema.get('items', {}), root)}"
    return model or schema_type


def _object_schema(schema: dict, root: dict, seen: frozenset) -> tuple[dict | None, frozenset]:
    """The object a field holds, looking through $ref, optional (anyOf) and array items, if any."""
    for _ in range(10):
        schema, model = _resolve(schema, root)
        if model:
            if model in seen:  # recursive model: stop here
                return None, seen
            seen = seen | {model}
        non_null = [option for option in _options(schema) if option.get("type") != "null"]
        if len(non_null) == 1:
            schema = non_null[0]
        elif schema.get("type") == "array" and "items" in schema:
            schema = schema["items"]
        else:
            return (schema if schema.get("properties") else None), seen
    return None, seen


def output_structure(schema: dict | None, indent: str = "") -> list[str]:
    """Describe an MCP tool's outputSchema as an indented field tree, one line per field.

    Example line: '  rating: number or null (optional)  - Guest rating out of 5'
    """
    if not schema:
        return []
    lines = []

    def walk(node: dict, depth: int, seen: frozenset):
        required = set(node.get("required", []))
        for name, spec in node.get("properties", {}).items():
            resolved, _ = _resolve(spec, schema)
            line = f"{indent}{'  ' * depth}{name}: {_type_name(spec, schema)}"
            if name not in required:
                line += " (optional)"
            description = spec.get("description") or resolved.get("description")
            if description:
                line += f"  - {' '.join(description.split())[:80]}"
            lines.append(line)
            child, child_seen = _object_schema(spec, schema, seen)
            if child is not None and depth < 8:
                walk(child, depth + 1, child_seen)

    walk(schema, 0, frozenset())
    return lines


async def check_mcp_ui_support(servers: dict) -> list[dict]:
    """Return one entry per tool of each MCP server: whether it declares JSON output (outputSchema).

    Entries: {"server", "tool", "selected" (listed in the server's "tools", or no list), "json_output",
    "output_schema", "structure" (output_structure() lines), "ui_resource" (MCP Apps _meta.ui.resourceUri,
    if any)}.
    A server that cannot be reached gives one entry {"server", "error"} instead, so the others are still checked.
    """
    client = MultiServerMCPClient(_connections(servers))
    results = []
    for server_name, config in servers.items():
        wanted = config.get("tools")
        try:
            async with client.session(server_name) as session:
                tools = (await session.list_tools()).tools
        except Exception as e:
            results.append({"server": server_name, "error": _root_error(e)})
            continue
        for tool in tools:
            results.append({
                "server": server_name,
                "tool": tool.name,
                "selected": wanted is None or tool.name in wanted,
                "json_output": tool.outputSchema is not None,
                "output_schema": tool.outputSchema,
                "structure": output_structure(tool.outputSchema),
                "ui_resource": ((tool.meta or {}).get("ui") or {}).get("resourceUri"),
            })
    return results


def _root_error(error: BaseException) -> str:
    """The real cause inside the MCP client's ExceptionGroup, e.g. 'ConnectError: [Errno 11001] getaddrinfo failed'."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return f"{type(error).__name__}: {error}"


async def preview_mcp_tool(servers: dict, server_name: str, tool_name: str, args: dict | None = None) -> dict:
    """Call one MCP tool as the agent would and return {"content": ..., "artifact": ...}.

    "artifact" is exactly what frontends receive as the tool's data (None = nothing to render).
    """
    config = {**servers[server_name], "tools": [tool_name]}
    [tool] = await load_mcp_tools_from_config({server_name: config})
    message = await tool.ainvoke({"type": "tool_call", "id": "ui-check", "name": tool_name, "args": args or {}})
    return {"content": message.content, "artifact": message.artifact}


def _print_report(results: list[dict]):
    for server_name in dict.fromkeys(entry["server"] for entry in results):
        entries = [entry for entry in results if entry["server"] == server_name]
        print(f"\nMCP server '{server_name}'")
        if "error" in entries[0]:
            print(f"  could not connect: {entries[0]['error']}")
            continue
        width = max(len(entry["tool"]) for entry in entries)
        for entry in entries:
            selected = " " if entry["selected"] else "-"
            if entry["json_output"] and _only_text(entry["output_schema"]):
                status = f"JSON for UI: text  ({_fields(entry['output_schema'])}; one text field, little to render)"
            elif entry["json_output"]:
                status = "JSON for UI: yes   structure:"
            else:
                status = "JSON for UI: no    (text only; use --call to confirm)"
            print(f"  {selected} {entry['tool']:<{width}}  {status}")
            if entry["json_output"] and not _only_text(entry["output_schema"]):
                # The JSON structure a renderer for this tool receives as the artifact
                print("\n".join(output_structure(entry["output_schema"], indent="        ")))
            if entry["ui_resource"]:
                print(f"    {'':<{width}}  MCP App UI: {entry['ui_resource']} (not rendered by this library)")
    print("\nyes  = returns JSON data a card can render (the agent sends it to the UI as the tool's artifact)"
          "\ntext = returns JSON with a single text field; the UI gets it, but it is just text"
          "\nno   = returns text only; no UI data"
          "\n'-'  = not in the server's \"tools\" list in the config, so the agent does not load it")


async def _main():
    parser = argparse.ArgumentParser(description="Check which MCP tools return JSON data usable by a rich UI.")
    parser.add_argument("config", help="Agent config JSON file with \"mcp_servers\"")
    parser.add_argument("--server", help="Only check this server")
    parser.add_argument("--call", metavar="TOOL", help="Call this tool (needs --server) and show the UI data")
    parser.add_argument("--args", default="{}", help="Tool arguments as JSON, for --call")
    options = parser.parse_args()

    with open(options.config, encoding="utf-8") as f:
        servers = json.load(f).get("mcp_servers") or {}
    if options.server:
        if options.server not in servers:
            parser.error(f"no MCP server '{options.server}' in {options.config}; servers: {sorted(servers)}")
        servers = {options.server: servers[options.server]}
    if not servers:
        parser.error(f"no \"mcp_servers\" in {options.config}")

    if options.call:
        if not options.server:
            parser.error("--call needs --server")
        entries = await check_mcp_ui_support(servers)
        if entries and "error" in entries[0]:
            parser.exit(1, f"could not connect to MCP server '{options.server}': {entries[0]['error']}\n")
        declared = next((entry for entry in entries if entry["tool"] == options.call), None)
        if declared and declared["structure"]:
            print(f"\nDeclared JSON structure (outputSchema):\n" + "\n".join(output_structure(declared["output_schema"], "  ")))
        elif declared:
            print("\nDeclared JSON structure: none (no outputSchema)")
        result = await preview_mcp_tool(servers, options.server, options.call, json.loads(options.args))
        print(f"\nModel sees:\n{json.dumps(result['content'], indent=2, ensure_ascii=False, default=str)}")
        if result["artifact"] is None:
            print("\nUI data: none. This tool returns text only, so no card can be rendered from it.")
        else:
            print(f"\nUI data (artifact, key the renderer by '{options.call}'):\n"
                  f"{json.dumps(result['artifact'], indent=2, ensure_ascii=False, default=str)}")
    else:
        _print_report(await check_mcp_ui_support(servers))


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv  # ${VAR} values in the config may come from .env
        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
