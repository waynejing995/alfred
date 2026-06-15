# Module Research: mcp (Model Context Protocol — a tools-registry source)

Date: 2026-06-15
Module: Ring-3 subsystem `mcp` (Decision #21, §5.4)
Spec: ../specs/2026-06-15-alfred-design.md · Decisions: ../decisions/2026-06-15-hermes-agent-loop-decisions.md
Sibling contracts this module binds to: provider-layer.md (`ToolDef`/`ToolCall`), kernel-loop-budget.md (`_dispatch`, `ctx.tools[name].handler`), kernel-event-bus.md (`session_start`), control:autonomy+config.

---

## Module scope

mcp is **not a second tool system**. It is a *source* that populates the existing `tools`
registry (Decision #8, #21). An mcp client connects to an mcp server, calls `list_tools()`,
converts each discovered tool into an Alfred registry entry (`name + JSON-Schema + handler`),
and registers it. From that point the kernel loop dispatches mcp tools through the **same one
path** as local tools — `ctx.tools[call.name].handler(**call.args)` (kernel-loop-budget.md
§`_dispatch`). The loop never learns a tool came from mcp. This is the SSoT "one dispatch
path" invariant; the only mcp-specific code is (a) the client connection, (b) the schema
adapter, (c) the handler closure that proxies `call_tool()` back over the wire.

In scope (MVP, Decision #21):
- mcp **client** only (Alfred consumes mcp servers; it does not expose one — that would be the
  server-shell's job, out of scope here).
- Two transports: **stdio** (spawn a subprocess server) and **HTTP** (Streamable HTTP, the
  current spec transport; SSE is the deprecated predecessor — support Streamable HTTP, treat
  legacy SSE as optional).
- Multiple servers, declared in unified config as `{type: mcp, params: {...}}` (Decision #13).
- Tool discovery + dispatch. **Out of scope for MVP:** mcp resources, prompts, sampling,
  roots, notifications/`tools/list_changed` (frozen-at-session_start mirrors skill-loader
  Decision #12 — no hot-reload, cache stability).

Verifies e2e contract **#13**: "Dev connects an mcp server; its tool appears in agent's tools
and is called → mcp-exposed tool appears in tool list, is successfully called in conversation,
returns real result."

---

## MCP client

Official SDK: `mcp` (PyPI, `uv add mcp`), Python ≥3.10. The relevant client surface:

| Import | Purpose |
|---|---|
| `from mcp import ClientSession, StdioServerParameters` | session + stdio config |
| `from mcp.client.stdio import stdio_client` | stdio transport context manager |
| `from mcp.client.streamable_http import streamablehttp_client` | HTTP transport (note: **`streamablehttp_client`**, one word, NOT `streamable_http_client`) |
| `import mcp.types as types` | `Tool`, `TextContent`, `CallToolResult` |

Both transports yield read/write streams that you hand to a `ClientSession`. **stdio yields a
2-tuple `(read, write)`; Streamable HTTP yields a 3-tuple `(read, write, get_session_id)`**
(the third is a callable returning the HTTP `Mcp-Session-Id`, or `None`). This asymmetry is a
real gotcha — the adapter must branch on transport.

### stdio (spawn a subprocess server)
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="uv",                       # or "npx", "python", a path...
    args=["run", "my-mcp-server"],
    env={"SOME_KEY": os.environ["SOME_KEY"]},   # explicit env passthrough
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()      # MCP handshake (capabilities exchange)
        resp = await session.list_tools()
        result = await session.call_tool("add", arguments={"a": 5, "b": 3})
```

### Streamable HTTP (connect to a running server)
```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8000/mcp") as (read, write, get_session_id):
    async with ClientSession(read, write) as session:
        await session.initialize()
        resp = await session.list_tools()
```

### Shapes returned (the data we adapt)
- `session.list_tools()` → `ListToolsResult` with `.tools: list[types.Tool]`. Each `Tool` has:
  - `tool.name: str`
  - `tool.description: str | None`
  - `tool.inputSchema: dict`  ← **JSON Schema (object)** — maps 1:1 to Alfred `ToolDef.parameters`
- `session.call_tool(name, arguments: dict)` → `types.CallToolResult` with:
  - `.content: list[ContentBlock]` (each `TextContent` has `.text`; also Image/Embedded)
  - `.structuredContent: dict | None` (structured output if the server declares it)
  - `.isError: bool` (tool-level error flag — see Lifecycle: this becomes a tool-result
    message, NOT a loop crash)

---

## Tool registration (one dispatch path)

The whole module is "convert MCP `Tool` → Alfred `ToolDef`, attach a proxy handler, register".
Alfred's contract (provider-layer.md) is exactly:

```python
class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema (object). SSoT for tool shape.
```

and the loop calls `await ctx.tools[call.name].handler(**call.args)` (kernel-loop-budget.md).
So a registry entry = `ToolDef` + an async `handler`. MCP gives us name/description/inputSchema
directly; the only work is the handler closure that proxies back over the live session.

### Schema chain: MCP Tool → Alfred ToolDef → LiteLLM tool
1. **MCP → Alfred** (trivial rename): `inputSchema` → `parameters`; `description or ""`.
2. **Alfred → LiteLLM** is done *once, centrally*, already in provider-layer.md
   `_to_litellm_tools()` — it wraps every `ToolDef` into the OpenAI/LiteLLM shape
   `{"type":"function","function":{"name","description","parameters"}}`. **mcp does NOT touch
   this step** — that is the entire point of registering as a normal `ToolDef`: the LiteLLM
   conversion is provider-layer's job, shared by local + mcp + plugin tools (SSoT).

This is why "one dispatch path" holds at *both* ends: discovery feeds the same `ToolDef` the
provider already knows how to serialize, and dispatch hits the same `handler(**args)` the loop
already knows how to call.

### Registration sketch
```python
# agentkit/mcp/register.py
import mcp.types as types
from agentkit.kernel.providers.types import ToolDef
from agentkit.kernel.registries import RegistryEntry   # name + ToolDef + handler + flags

def _alfred_tooldef(t: types.Tool) -> ToolDef:
    return ToolDef(
        name=t.name,
        description=t.description or "",
        parameters=t.inputSchema or {"type": "object", "properties": {}},
    )

def _make_handler(session, tool_name: str):
    async def handler(**kwargs) -> str:
        # loop passes parsed dict args (provider-layer parses JSON at the boundary)
        result: types.CallToolResult = await session.call_tool(tool_name, arguments=kwargs)
        text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        if result.isError:
            # surface as a tool-result string the model reads — NOT a raised exception
            return f"[mcp tool error] {text}"
        return text or (str(result.structuredContent) if result.structuredContent else "")
    return handler

async def register_server_tools(session, registry, *, server_name: str, autonomy) -> list[str]:
    resp = await session.list_tools()
    registered = []
    for t in resp.tools:
        entry = RegistryEntry(
            tooldef=_alfred_tooldef(t),
            handler=_make_handler(session, t.name),
            source=f"mcp:{server_name}",       # provenance for collision WARNING + trace
            permission_bucket="mcp",            # default ask unless config narrows per server/tool
            refundable=False,                  # network tool, not execute_code-class (#budget)
        )
        registry.add(entry)                    # add() emits the collision WARNING (below)
        registered.append(t.name)
    return registered
```

Notes:
- handler returns a **string** (or a small structured payload the loop wraps as a tool-result
  message) — matches how local tool results flow back. `isError` and transport faults both
  become tool-result text, never loop crashes (§Error Handling, spec §7: "tool exceptions →
  converted to tool-result messages, never crash the loop").
- `source="mcp:<server>"` is the provenance tag used for collision reporting and trace
  fidelity (trace store #17).
- **Permission default:** every MCP tool resolves to `ask` by default. A dynamic remote tool
  is not equivalent to local read, even if its schema looks harmless. Config may explicitly
  narrow/allow by provenance key (`mcp:<server>:<tool>` or `mcp:<server>:*`), and the
  resolved permission appears in the session-start `-v` manifest. Registering an MCP tool
  without a known permission bucket is a config/registry error, not implicit allow.

---

## Lifecycle + collisions

### When to connect: session_start (frozen mirror of skill-loader)
mcp tools join the registry at **`session_start`**, before the static system prompt is
assembled and frozen (Decision #21 cache discipline; #12 skill-list-frozen analogue). Tool
list (local + mcp + plugin) must be stable for the whole session so the cache prefix stays
stable (Decision #29). **No `tools/list_changed` handling in MVP** — a server adding tools
mid-session is ignored until next session, exactly like skills (no hot-reload). This is a
deliberate deferral, recorded for fail-loud honesty.

Connections must **outlive** `session_start`: the proxy handler closes over a *live* session
used on every later tool call. So connecting cannot be a `async with ... :` block that exits
after discovery — the session must be held open for the session's lifetime. See §Async.

### Connection failure: fail-loud at startup (config error), not at Nth tool call
A declared mcp server that fails to connect/initialize is a **startup crash**, consistent with
spec §7 Fail-Loud ("missing config / unsupported platform → crash at startup, not on the Nth
user action") and `extra=forbid` config strictness. Rationale: a silently-absent mcp tool is
the "silent degradation" anti-pattern — the agent would behave as if the tool never existed,
undetectably. Concretely:
- stdio: subprocess spawn fails / non-zero exit / `initialize()` times out → raise, naming the
  server and command.
- HTTP: connection refused / non-200 on initialize → raise, naming the URL.

Open question O3 below: whether to offer an opt-in `required: false` per-server (degrade to
WARNING + skip) for "nice-to-have" servers. Default = fail-loud.

### Tool name collisions (mcp vs local vs another mcp server)
Reuse the **skill-loader precedence model verbatim** (Decision #12): identity = `name`,
higher-precedence source wins, **fail-loud WARNING listing the shadowed entry** (never silent
merge). Precedence order (highest → lowest), proposed:

1. **local/kernel tools** (e.g. `read_file`, `spawn_subagent`) — Alfred-owned, never shadowed
   by a remote server (security: an mcp server must not hijack `execute_code`).
2. **mcp servers** in config-declaration order (earlier server wins, mirrors `skill_sources`
   ordered-roots = precedence).
3. plugin-registered tools (lowest, or interleave — O4).

`registry.add()` on a duplicate `name` → keep incumbent, emit
`WARNING: tool '<name>' from mcp:<server> shadowed by <winner-source>`. This is the same
mechanism e2e #5 verifies for skills; reusing it keeps one collision policy across the bench.
**Optional namespacing** (O2): prefix mcp tools `<server>__<tool>` to sidestep collisions
entirely — cleaner but uglier tool names in the prompt; left as an open question.

---

## Config schema

Per Decision #13, mcp is an assembly-type component declared `{type, params}`, layered like
everything else (bundled → `~/.alfred/config.yaml` → `./agent.yaml` → env/code),
`extra=forbid`. The canonical config key is `mcp` (matching `AgentConfig`), a list of tool
sources where each entry is `{type: mcp, params: {...}}`:

```yaml
mcp:
  - type: mcp
    params:
      name: filesystem            # logical id → provenance "mcp:filesystem", collision msgs
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/work"]
      env: {NODE_ENV: production} # explicit; values may use env_key indirection (Decision #26)
  - type: mcp
    params:
      name: github
      transport: http            # Streamable HTTP
      url: https://mcp.example.com/mcp
      headers_env_key: GITHUB_MCP_HEADERS   # secrets via env-var NAME, never plaintext (#26)
      # required: true            # default true → fail-loud on connect failure (O3)
```

Pydantic model (SSoT, discriminated union on `transport`):
```python
class StdioMCPParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["stdio"]
    command: str
    args: list[str] = []
    env: dict[str, str] = {}

class HttpMCPParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["http"]
    url: str
    headers_env_key: str | None = None        # Decision #26 secret hygiene

class MCPServerConfig(BaseModel):
    type: Literal["mcp"]
    params: StdioMCPParams | HttpMCPParams = Field(discriminator="transport")
```

- **Secrets**: never inline tokens — store the env-var *name* (`headers_env_key`), resolved at
  connect time. Same `env_key` indirection the provider layer uses (Decision #26).
- **`name`** is mandatory (provenance + collision messages); duplicate `name` across servers →
  config-validation crash (`extra=forbid`-grade strictness).

---

## Async

This is the module's hardest part. mcp clients are **async, anyio-based, and have a strict
task-affinity constraint** that dictates how Alfred owns connections.

### The anyio cancel-scope constraint (the load-bearing fact)
`ClientSession` (and `stdio_client`) internally use `anyio.create_task_group()`. AnyIO
requires a cancel scope to be **entered and exited in the same task**, and task groups to be
torn down **FILO** (first-in-last-out). Violations throw
`RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`
(python-sdk issues #521, #577, #922; microsoft/agent-framework #2846). **Consequence for
Alfred:** you cannot open an mcp session in one task and close it in another, and you cannot
close session1 before session2 if session2 opened later. Naive "one AsyncExitStack per server,
close them in any order" breaks.

### Recommended ownership model: a single connection-manager owning all sessions in one task
Run all mcp connections under **one `AsyncExitStack` entered and exited on one owning task**
(the host's main agent task / the daemon's event-loop task). For N servers, push each
transport + session onto the *same* stack via `enter_async_context`; `aclose()` unwinds them
in correct FILO order automatically:

```python
# agentkit/mcp/manager.py
class MCPManager:
    def __init__(self):
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}

    async def connect_all(self, configs: list[MCPServerConfig], registry):
        for cfg in configs:                     # one owning task — FILO-safe
            p = cfg.params
            if p.transport == "stdio":
                read, write = await self._stack.enter_async_context(
                    stdio_client(StdioServerParameters(command=p.command, args=p.args, env=p.env)))
            else:
                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(p.url, headers=_resolve_headers(p)))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()          # fail-loud on failure (name the server)
            self.sessions[p.name] = session
            await register_server_tools(session, registry, server_name=p.name, autonomy=...)

    async def aclose(self):
        await self._stack.aclose()              # unwinds FILO, same task — no cancel-scope error
```

- **One stack, one task, FILO unwind** sidesteps every reported cancel-scope bug. Do NOT give
  each server its own stack closed independently.
- Tie lifetime to the host: in-process SDK → `connect_all` at agent construction,
  `aclose()` in a `finally`; daemon/server-shell → connect at startup, close at shutdown
  (Decision #6: the server-shell is the required host for long-lived background work; same
  applies to long-lived mcp sessions).
- **Concurrent tool calls**: the loop may dispatch tools concurrently (subagents, budget
  §kernel-loop-budget). A single `ClientSession` multiplexes requests by id, but to be safe
  against any per-session serialization assumption, treat each session as the unit and let the
  SDK's request-id correlation handle concurrency; if a server proves non-concurrent, gate that
  one session with an `asyncio.Lock` (O5). MVP: rely on the SDK.
- **Everything is already async**: Alfred's loop, providers, and tool handlers are async
  (`await ctx.tools[name].handler(...)`), so the mcp proxy handler (`await session.call_tool`)
  drops straight in — no thread bridging, no `asyncio.run` inside handlers (which would itself
  trip the cancel-scope rule).

---

## Industry refs (URLs)

- Official MCP Python SDK (client API, stdio/streamable_http, ClientSession):
  https://github.com/modelcontextprotocol/python-sdk
- Official "Build an MCP client" tutorial (AsyncExitStack `connect_to_server`/`cleanup`
  pattern, list_tools → LLM tools, call_tool dispatch):
  https://modelcontextprotocol.io/docs/develop/build-client
- SDK Writing-Clients guide (stdio + streamable HTTP code, Tool/CallToolResult shapes):
  https://py.sdk.modelcontextprotocol.io/client/
- Transports spec (stdio + Streamable HTTP; SSE deprecated; `Mcp-Session-Id` header):
  https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- `mcp` on PyPI (version/Python requirement): https://pypi.org/project/mcp/
- anyio cancel-scope / multi-session teardown issues (the async constraint):
  https://github.com/modelcontextprotocol/python-sdk/issues/521 ·
  https://github.com/modelcontextprotocol/python-sdk/issues/577 ·
  https://github.com/modelcontextprotocol/python-sdk/issues/922
- LiteLLM function-calling tool format (`{"type":"function","function":{...}}`) — the target of
  provider-layer `_to_litellm_tools`: https://docs.litellm.ai/docs/completion/function_call
- OpenAI Agents SDK MCP integration (cross-check on stateful HTTP session-id handling):
  https://openai.github.io/openai-agents-python/mcp/
- RealPython "Build a Python MCP Client" (multi-server, transport tuple asymmetry):
  https://realpython.com/python-mcp-client/

---

## Open questions

- **O1 — list_changed / hot-reload.** MVP freezes tools at session_start (cache discipline).
  Servers can emit `notifications/tools/list_changed`. Defer, or subscribe and re-register on a
  *new* session only (never mid-session)? Lean: defer (matches skill-loader #12).
- **O2 — namespacing.** Prefix mcp tools `<server>__<tool>` to avoid collisions vs. keep bare
  names + precedence WARNING (#12 reuse). Trade-off: collision-proof vs. uglier prompt names
  and a longer tool surface. Lean: bare names + WARNING for MVP; revisit if real collisions
  bite.
- **O3 — per-server `required` flag.** Default fail-loud on connect failure. Offer opt-in
  `required: false` (WARNING + skip) for nice-to-have servers? Risk: re-introduces silent
  degradation. Lean: keep default fail-loud; only add if a concrete use case appears.
- **O4 — precedence of plugin tools vs mcp tools.** §collisions proposes local > mcp > plugin.
  Confirm against the plugin module's expectations (plugin = packaged registry calls, Decision
  #8) — a plugin may legitimately want to override an mcp tool.
- **O5 — per-session concurrency.** Does a single `ClientSession` safely handle concurrent
  `call_tool` from concurrent subagents, or do we need an `asyncio.Lock` per session? MVP
  relies on the SDK's request-id multiplexing; validate under the subagent-concurrency e2e.
- **O6 — budget classification of mcp tools.** Registered `refundable=False` (network call, not
  `execute_code`-class). Confirm with kernel-loop-budget refund policy ownership (its O3): is a
  network round-trip ever refundable? Lean: no.
- **O7 — legacy SSE transport.** Spec says "stdio + HTTP". Streamable HTTP is the current HTTP
  transport; the older `sse_client` is deprecated. Support only Streamable HTTP, or also accept
  `transport: sse` for older servers still in the wild? Lean: Streamable HTTP only for MVP.
