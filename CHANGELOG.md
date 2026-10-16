# Changelog

All notable changes to Bootstrap Agent SDK are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Released]

## [0.1.0] - 2026-10-17

First public release.

### Added

- **`ChatAgent`**, a config-driven chat agent built on LangChain and LangGraph, created with `await ChatAgent.create(name, config_path, tools)`.
- **JSON configuration**: model (`provider:model`, any LangChain chat model), optional OpenAI-compatible `llm_base_url`, temperature, `agent_instructions`, `response_instructions` and `recursion_limit`.
- **Chat API**:
  - `chat()` returns `{"text", "tool_outputs", "usage"}`.
  - `start_chat()` returns only the reply text.
  - `stream_chat()` yields events for UIs.
  - All three take `message`, `chat_id`, `payload` and `chat_history`.
- **Automatic conversation summarization** once the history exceeds 20 messages.
- **Response generator step**, which rewrites the agent's tool-backed answer into the final reply, applying `response_instructions`.
- **Payload**: server-side values for tools (user id, access tokens) that are never visible to the model.
- **Tools**:
  - Python and LangChain tools; with `response_format="content_and_artifact"` they also return structured data for the UI.
  - **HTTP tools** (`http_tools`): REST endpoints declared in JSON, with path/query/body parameters, headers, basic auth and `response_path`. Placeholders: `${ENV_VAR}` from the environment, `{payload.key}` from the payload. Opt-in `artifact` data for UIs.
  - **MCP tools** (`mcp_servers`): stdio and streamable HTTP servers with a per-server tool allow-list. Tools that return JSON automatically provide UI data.
- **`python -m bootstrap_agent.mcp_ui_check`**: reports which MCP tools return JSON, shows their structure, and previews real results with `--call`.
- **Intent classification** (optional, `intent_classification`): routes each message to a focused agent with its own tools and instructions, a direct reply, or a refusal.
- **Streaming events** (`schema_version` `"1"`): `run.started`, `tool.started`, `tool.finished` (with the tool's data), `message.delta`, then `run.finished` or `run.error`.
- **Token usage** per turn and per graph step, returned as `usage` and logged. Can be turned off with `"token_usage": false`.
- **LangSmith tracing** through environment variables.
- **Examples**: a custom React UI, an AG-UI app and an Open WebUI Pipe.

[Unreleased]: https://github.com/indunil-19/bootstrap_agent_sdk/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/indunil-19/bootstrap_agent_sdk/releases/tag/v0.1.0
