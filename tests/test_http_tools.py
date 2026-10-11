"""Test HTTP tools declared under "http_tools" in the agent config.

Part 1 calls the tools directly (no LLM) against free public APIs to check request building:
payload tokens, ${ENV} values, URL-encoding of path params, response_path, artifacts, errors and validation.
Part 2 runs a ChatAgent end-to-end with the same tools and checks that artifacts reach chat()'s tool_outputs.
"""

import asyncio
import json
import logging
import os
import tempfile

from dotenv import load_dotenv
from pydantic import ValidationError

from bootstrap_agent.http_tools import build_http_tools
from bootstrap_agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logging.getLogger("bootstrap_agent").setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.environ["HTTPBIN_PASSWORD"] = "secret-pass"

HTTP_TOOLS = {
    "get_current_weather": {
        "description": "Get the current temperature and wind speed for a location given its latitude and longitude.",
        "url": "https://api.open-meteo.com/v1/forecast",
        "params": {
            "latitude": {"type": "number", "description": "Latitude of the location"},
            "longitude": {"type": "number", "description": "Longitude of the location"},
        },
        "query": {"current": "temperature_2m,wind_speed_10m"},
        "response_path": "current",
        # The frontend gets the same data as the model ("current"), untruncated
        "artifact": True,
    },
    "get_weather_units": {
        "description": "Get the units of the current weather readings.",
        "url": "https://api.open-meteo.com/v1/forecast",
        "params": {
            "latitude": {"type": "number", "description": "Latitude of the location"},
            "longitude": {"type": "number", "description": "Longitude of the location"},
        },
        "query": {"current": "temperature_2m"},
        "response_path": "current",
        "artifact": "current_units",
    },
    "list_user_posts": {
        "description": "List the blog posts of a user.",
        "url": "https://jsonplaceholder.typicode.com/posts",
        "params": {"userId": {"type": "integer", "description": "User id"}},
        "max_response_chars": 200,
        "artifact": True,
    },
    "get_exchange_rate": {
        "description": "Get the latest exchange rate between two currencies.",
        "url": "https://api.frankfurter.dev/v1/latest",
        "params": {
            "base": {"type": "string", "description": "Currency to convert from, e.g. USD"},
            "symbols": {"type": "string", "description": "Currency to convert to, e.g. EUR"},
        },
    },
    "get_post": {
        "description": "Get a blog post by its id.",
        "url": "https://jsonplaceholder.typicode.com/posts/{post_id}",
        "params": {"post_id": {"type": "integer", "description": "Post id"}},
    },
    "create_post": {
        "description": "Create a new blog post for the current user.",
        "method": "POST",
        "url": "https://jsonplaceholder.typicode.com/posts",
        "params": {
            "title": {"type": "string", "description": "Post title"},
            "body": {"type": "string", "description": "Post text"},
        },
        "body": {"userId": "{payload.user_id}"},
    },
    # httpbin echoes the request back, so these check exactly what was sent
    "echo_with_user_token": {
        "description": "Echo endpoint authenticated with the current user's token.",
        "url": "https://httpbin.org/anything/users/{payload.user_id}/items/{item}",
        "params": {
            "item": {"type": "string", "description": "Item name"},
            "sort": {"type": "string", "enum": ["asc", "desc"], "required": False, "description": "Sort order"},
        },
        "headers": {"Authorization": "Bearer {payload.access_token}"},
    },
    "basic_auth_check": {
        "description": "Check basic auth.",
        "url": "https://httpbin.org/basic-auth/agent/secret-pass",
        "auth": {"type": "basic", "username": "agent", "password": "${HTTPBIN_PASSWORD}"},
    },
    "not_found": {
        "description": "Endpoint that returns 404.",
        "url": "https://httpbin.org/status/404",
        "artifact": True,
    },
}

PAYLOAD = {"user_id": 7, "access_token": "user-7-token"}

results = {}


def check(name: str, passed: bool, detail: str = ""):
    results[name] = passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


async def direct_tests():
    print("\n=== Part 1: direct tool calls ===")
    tools = {tool.name: tool for tool in build_http_tools(HTTP_TOOLS)}
    run_config = {"configurable": {"payload": PAYLOAD}}

    # The model-facing schema contains only the declared params (no config, no payload values)
    schema = tools["create_post"].tool_call_schema.model_json_schema()
    check("schema hides payload/config", sorted(schema["properties"]) == ["body", "title"], str(sorted(schema["properties"])))

    result = await tools["get_current_weather"].ainvoke({"latitude": 48.85, "longitude": 2.35}, config=run_config)
    check("GET with query params + response_path", "temperature_2m" in result, result[:100])

    result = await tools["get_exchange_rate"].ainvoke({"base": "USD", "symbols": "EUR"}, config=run_config)
    check("GET exchange rate", '"EUR"' in result, result[:100])

    result = await tools["get_post"].ainvoke({"post_id": 1}, config=run_config)
    check("GET with path param", json.loads(result).get("id") == 1, result[:80])

    result = json.loads(await tools["create_post"].ainvoke({"title": "Hello", "body": "World"}, config=run_config))
    check("POST body merges payload value (int kept)", result.get("userId") == 7 and result.get("title") == "Hello", str(result))

    echoed = json.loads(await tools["echo_with_user_token"].ainvoke({"item": "book", "sort": "desc"}, config=run_config))
    check("payload token sent as header", echoed["headers"].get("Authorization") == "Bearer user-7-token")
    check("payload value in URL path + query param",
          echoed["url"].startswith("https://httpbin.org/anything/users/7/items/book") and echoed["args"] == {"sort": "desc"},
          echoed["url"])

    # httpbin echoes the decoded path, so check the request stayed on /anything/users/7/items/ and
    # that "?x=1" did not become a query param (a traversal to /status/500 would return HTTP 500)
    result = await tools["echo_with_user_token"].ainvoke({"item": "../../../status/500?x=1"}, config=run_config)
    echoed = json.loads(result) if not result.startswith("Error") else {}
    check("path param is URL-encoded (no traversal)",
          "/anything/users/7/items/" in echoed.get("url", "") and echoed.get("args") == {}, result[:120])

    result = await tools["echo_with_user_token"].ainvoke({"item": ".."}, config=run_config)
    echoed = json.loads(result) if not result.startswith("Error") else {}
    check("'..' path param is not collapsed", "/anything/users/7/items/" in echoed.get("url", ""), result[:120])

    result = await tools["basic_auth_check"].ainvoke({}, config=run_config)
    check("basic auth with ${ENV} password", '"authenticated": true' in result, result)

    result = await tools["not_found"].ainvoke({}, config=run_config)
    check("HTTP error returned as message", result.startswith("Error: HTTP 404"), result)

    result = await tools["echo_with_user_token"].ainvoke({"item": "book"}, config={"configurable": {"payload": {}}})
    check("missing payload value returned as message", "payload.user_id" in result, result)

    try:
        await tools["echo_with_user_token"].ainvoke({"item": "book", "sort": "random"}, config=run_config)
        check("enum validation", False, "no error raised")
    except ValidationError:
        check("enum validation", True)

    # Artifacts are only attached when the tool is called through a tool call (as the agent does)
    async def tool_call(tool_name, args):
        return await tools[tool_name].ainvoke({"type": "tool_call", "id": "call-1", "name": tool_name, "args": args},
                                              config=run_config)

    message = await tool_call("get_current_weather", {"latitude": 48.85, "longitude": 2.35})
    check("artifact: true = same data as the model", isinstance(message.artifact, dict)
          and message.artifact == json.loads(message.content), str(message.artifact)[:100])

    message = await tool_call("get_weather_units", {"latitude": 48.85, "longitude": 2.35})
    check("artifact path into the full response", message.artifact.get("temperature_2m") == "°C"
          and "°C" not in message.content, f"{message.artifact} / {message.content[:60]}")

    message = await tool_call("list_user_posts", {"userId": 1})
    check("artifact is not truncated", "[truncated" in message.content
          and isinstance(message.artifact, list) and len(message.artifact) == 10, f"{len(message.artifact or [])} posts")

    message = await tool_call("not_found", {})
    check("HTTP error gives no artifact", message.content.startswith("Error: HTTP 404") and message.artifact is None)

    message = await tool_call("get_post", {"post_id": 1})
    check("no artifact unless configured", message.artifact is None and json.loads(message.content)["id"] == 1)

    try:
        build_http_tools({"bad": {"description": "d", "url": "https://x.com", "artifact": ""}})
        check("config error: empty artifact path", False, "no error raised")
    except ValueError as e:
        check("config error: empty artifact path", True, str(e))

    try:
        build_http_tools({"bad": {"description": "d", "url": "https://x.com/{missing}"}})
        check("config error: url placeholder without param", False, "no error raised")
    except ValueError as e:
        check("config error: url placeholder without param", True, str(e))


async def agent_test():
    print("\n=== Part 2: agent end-to-end ===")
    with open(os.path.join(BASE_DIR, "../agent_config.json")) as f:
        config = json.load(f)
    config.pop("mcp_servers", None)
    config["http_tools"] = {name: HTTP_TOOLS[name] for name in
                            ["get_current_weather", "get_exchange_rate", "get_post", "create_post"]}

    config_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(config, config_file)
    config_file.close()

    agent = await ChatAgent.create(name="http-test-agent", config_path=config_file.name, tools=[])

    result = await agent.chat("What's the weather in Paris right now?", chat_id="http-test", payload=PAYLOAD)
    print(f"\nUser: What's the weather in Paris right now?\nAssistant: {result['text']}")
    outputs = result["tool_outputs"]
    check("agent: artifact in chat() tool_outputs",
          [output["name"] for output in outputs] == ["get_current_weather"] and "temperature_2m" in outputs[0]["data"],
          str(outputs)[:120])

    for prompt in ["How many euros is 1 US dollar today?",
                   "What is the title of post 3?",
                   "Create a post titled 'Hello' with the text 'First post'. What id and userId did it get?"]:
        print(f"\nUser: {prompt}")
        print(f"Assistant: {await agent.start_chat(prompt, chat_id='http-test', payload=PAYLOAD)}")


async def main():
    await direct_tests()
    await agent_test()

    print("\n=== Summary ===")
    print(f"{sum(results.values())}/{len(results)} direct checks passed")
    assert all(results.values()), [name for name, passed in results.items() if not passed]


if __name__ == "__main__":
    asyncio.run(main())
