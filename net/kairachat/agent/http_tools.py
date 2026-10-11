"""Build LangChain tools from REST endpoints declared under "http_tools" in the agent config.

Each entry describes one endpoint; the model only fills the declared "params". Values from the
environment (${VAR}, resolved at build time) and from the run payload ({payload.key}, resolved per
call) are added by the library and never shown to the model.
"""

import json
import logging
import re
import time
from typing import Literal, Optional
from urllib.parse import quote

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from net.kairachat.agent.utils import expand_env

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RESPONSE_CHARS = 8000

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_BODY_METHODS = {"POST", "PUT", "PATCH"}
_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# {payload.key.sub} placeholders (from the run payload) or {param} placeholders (from tool arguments)
_PLACEHOLDER = re.compile(r"\{(payload(?:\.\w+)+|\w+)\}")
_HTML_DROP = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_TAG = re.compile(r"<[^>]+>")


class _MissingPayloadValue(Exception):
    pass


def _payload_value(payload: dict, path: str):
    value = payload
    for key in path.split("."):
        if not isinstance(value, dict) or value.get(key) is None:
            raise _MissingPayloadValue(f"payload.{path}")
        value = value[key]
    return value


def _fill(template, payload: dict, args: dict | None = None, encode: bool = False):
    """Fill {payload.x} (and, for URLs, {param}) placeholders.

    Substitution is a single pass, so substituted values are never re-scanned for placeholders.
    With encode=True values are URL-encoded so they cannot add path segments or change the host.
    """
    if isinstance(template, dict):
        return {key: _fill(value, payload) for key, value in template.items()}
    if isinstance(template, list):
        return [_fill(value, payload) for value in template]
    if not isinstance(template, str):
        return template

    # A value that is exactly one payload placeholder keeps its original type (e.g. an int in a JSON body)
    whole = _PLACEHOLDER.fullmatch(template)
    if whole and not encode and whole.group(1).startswith("payload."):
        return _payload_value(payload, whole.group(1)[len("payload."):])

    def replace(match):
        name = match.group(1)
        if name.startswith("payload."):
            value = _payload_value(payload, name[len("payload."):])
        elif args is not None and name in args:
            value = args[name]
        else:
            return match.group(0)
        if not encode:
            return str(value)
        encoded = quote(str(value), safe="")
        # "." / ".." segments would be collapsed by the URL parser (path traversal), so encode the dots too
        return encoded.replace(".", "%2E") if set(encoded) <= {"."} else encoded

    return _PLACEHOLDER.sub(replace, template)


def _extract(data, path: str):
    """Follow a dotted path like "data.items.0.name" into parsed JSON."""
    for key in path.split("."):
        if isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        elif isinstance(data, dict) and key in data:
            data = data[key]
        else:
            raise KeyError(path)
    return data


def _response_text(response: httpx.Response, response_path: str | None, max_chars: int) -> str:
    content_type = response.headers.get("content-type", "")

    if "json" in content_type:
        try:
            data = response.json()
        except ValueError:
            text = response.text
        else:
            if response_path:
                try:
                    data = _extract(data, response_path)
                except KeyError:
                    return f"Error: response_path '{response_path}' was not found in the response."
            text = json.dumps(data, ensure_ascii=False)
    elif "html" in content_type:
        text = _HTML_TAG.sub(" ", _HTML_DROP.sub(" ", response.text))
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = response.text

    if len(text) > max_chars:
        text = f"{text[:max_chars]}... [truncated, {len(text)} characters in total]"
    return text


def _response_artifact(name: str, response: httpx.Response, artifact: bool | str, response_path: str | None):
    """Return the part of a JSON response sent to the frontend (not truncated), or None.

    artifact=True uses the same data as the model (after response_path); a string is a dotted
    path into the full response. Non-JSON responses and missing paths give no artifact.
    """
    if "json" not in response.headers.get("content-type", ""):
        return None
    path = response_path if artifact is True else artifact
    try:
        data = response.json()
        return _extract(data, path) if path else data
    except ValueError:
        return None
    except KeyError:
        logger.warning("[http] %s: artifact path '%s' was not found in the response", name, path)
        return None


def _build_http_tool(name: str, spec: dict) -> BaseTool:
    if not _TOOL_NAME.match(name):
        raise ValueError(f"HTTP tool name '{name}' must match {_TOOL_NAME.pattern}")

    spec = expand_env(spec)
    description = spec.get("description")
    url = spec.get("url")
    if not description or not url:
        raise ValueError(f"HTTP tool '{name}': 'description' and 'url' are required")

    method = spec.get("method", "GET").upper()
    if method not in _METHODS:
        raise ValueError(f"HTTP tool '{name}': method must be one of {sorted(_METHODS)}")

    fixed_body = spec.get("body", {})
    if not isinstance(fixed_body, dict):
        raise ValueError(f"HTTP tool '{name}': 'body' must be a JSON object")
    if fixed_body and method not in _BODY_METHODS:
        raise ValueError(f"HTTP tool '{name}': 'body' is only allowed for {sorted(_BODY_METHODS)}")

    auth = spec.get("auth")
    if auth and auth.get("type") != "basic":
        raise ValueError(f"HTTP tool '{name}': only auth type 'basic' is supported")

    # Build the argument schema the model sees, and remember where each argument goes
    path_names = {match for match in _PLACEHOLDER.findall(url) if not match.startswith("payload.")}
    params = spec.get("params", {})
    locations = {}
    fields = {}
    for param_name, param_spec in params.items():
        if not param_name.isidentifier():
            raise ValueError(f"HTTP tool '{name}': parameter name '{param_name}' must be a valid identifier")

        param_type = param_spec.get("type", "string")
        if param_type not in _TYPES:
            raise ValueError(f"HTTP tool '{name}': parameter '{param_name}' has unknown type '{param_type}'. "
                             f"Use one of {sorted(_TYPES)}")

        default_location = "path" if param_name in path_names else "body" if method in _BODY_METHODS else "query"
        location = param_spec.get("in", default_location)
        if location not in ("path", "query", "body"):
            raise ValueError(f"HTTP tool '{name}': parameter '{param_name}' has unknown location '{location}'")
        if location == "path" and param_name not in path_names:
            raise ValueError(f"HTTP tool '{name}': path parameter '{param_name}' is not used as {{{param_name}}} in the url")
        if location == "body" and method not in _BODY_METHODS:
            raise ValueError(f"HTTP tool '{name}': body parameter '{param_name}' needs one of {sorted(_BODY_METHODS)}")
        locations[param_name] = location

        annotation = Literal[tuple(param_spec["enum"])] if "enum" in param_spec else _TYPES[param_type]
        required = location == "path" or (param_spec.get("required", True) and "default" not in param_spec)
        if required:
            field_default = ...
        else:
            annotation = Optional[annotation]
            field_default = param_spec.get("default")
        fields[param_name] = (annotation, Field(field_default, description=param_spec.get("description", "")))

    missing = path_names - locations.keys()
    if missing:
        raise ValueError(f"HTTP tool '{name}': url placeholders {sorted(missing)} have no matching params")

    args_schema = create_model(f"{name}_args", **fields)

    fixed_headers = spec.get("headers", {})
    fixed_query = spec.get("query", {})
    response_path = spec.get("response_path")
    timeout = spec.get("timeout", DEFAULT_TIMEOUT)
    max_chars = spec.get("max_response_chars", DEFAULT_MAX_RESPONSE_CHARS)

    # Optional structured data for rich UIs: true, or a dotted path into the JSON response
    artifact = spec.get("artifact", False)
    if not (artifact is True or artifact is False or (isinstance(artifact, str) and artifact)):
        raise ValueError(f"HTTP tool '{name}': 'artifact' must be true, false or a dotted path into the response")

    async def call_endpoint(config: RunnableConfig, **kwargs):
        text, data = await request_endpoint(config, **kwargs)
        # With an artifact the tool returns (content for the model, data for the frontend)
        return (text, data) if artifact else text

    async def request_endpoint(config: RunnableConfig, **kwargs) -> tuple[str, object]:
        payload = (config.get("configurable") or {}).get("payload") or {}
        args = {key: value for key, value in kwargs.items() if value is not None}
        by_location = {location: {key: value for key, value in args.items() if locations[key] == location}
                       for location in ("path", "query", "body")}

        try:
            request_url = _fill(url, payload, by_location["path"], encode=True)
            headers = _fill(fixed_headers, payload)
            query = {**_fill(fixed_query, payload), **by_location["query"]}
            body = {**_fill(fixed_body, payload), **by_location["body"]}
            http_auth = None
            if auth:
                filled_auth = _fill(auth, payload)
                http_auth = httpx.BasicAuth(str(filled_auth["username"]), str(filled_auth["password"]))
        except _MissingPayloadValue as e:
            logger.warning("[http] %s: missing %s", name, e)
            return f"Error: this tool needs '{e}' to be provided by the application, but it is missing.", None

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(
                    method,
                    request_url,
                    params=query or None,
                    json=body if method in _BODY_METHODS else None,
                    headers=headers,
                    auth=http_auth,
                )
        except httpx.TimeoutException:
            logger.warning("[http] %s: timed out after %ss", name, timeout)
            return f"Error: the request timed out after {timeout} seconds.", None
        except httpx.HTTPError as e:
            logger.warning("[http] %s: request failed: %s", name, type(e).__name__)
            return f"Error: the request failed ({type(e).__name__}).", None

        # Log without the query string, which may contain API keys
        logger.info("[http] %s: %s %s -> %d in %.2fs", name, method,
                    response.request.url.copy_with(query=None), response.status_code, time.perf_counter() - start)

        if response.status_code >= 400:
            return f"Error: HTTP {response.status_code}: {_response_text(response, None, 500)}", None
        text = _response_text(response, response_path, max_chars)
        return text, _response_artifact(name, response, artifact, response_path) if artifact else None

    return StructuredTool.from_function(
        coroutine=call_endpoint,
        name=name,
        description=description,
        args_schema=args_schema,
        response_format="content_and_artifact" if artifact else "content",
    )


def build_http_tools(http_tools: dict | None) -> list[BaseTool]:
    """Build one tool per entry under "http_tools" in the agent config."""
    if not http_tools:
        return []

    tools = [_build_http_tool(name, spec) for name, spec in http_tools.items()]
    logger.info("HTTP tools loaded: %s", [tool.name for tool in tools])
    return tools
