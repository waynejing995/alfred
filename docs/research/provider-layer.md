# Provider Layer — Detail Research

Module: Provider layer (Ring 1). Date: 2026-06-15.
Decisions: #10 (composite-provider fusion), #26 (config format + env_key), #28 (LiteLLM
behind own ABC), #29 (cache field names). Spec §3.3 / §3.4.

---

## Module scope

The provider layer is the **single boundary** between Alfred's pure core and any LLM SDK.

In scope:
- `ModelProvider` ABC — the only model interface the loop, fusion, judge, and subagents know.
- Alfred's **owned** message/response/usage pydantic types (core never imports litellm types).
- `LiteLLMProvider` — the default impl; the **only file** that imports `litellm`.
- Alfred-types ↔ litellm conversion (request + response + streaming + tool calls + exceptions).
- Provider config schema (`{type, params}`) + `env_key` secret indirection.
- Per-vendor cache-control injection + correct `cached_tokens` read (Decision #29).

Out of scope (other modules, referenced only):
- fusion composite provider internals (`fusion.md`) — but it MUST implement this ABC.
- context assembly / cache breakpoint placement (`kernel-context-cache.md`) — provider only
  *passes through* the `cache_control` annotation the assembler already placed.
- mcp tool sourcing (`mcp.md`) — provider only consumes the unified `tools` list shape.
- registry mechanics (`models` registry) — provider is the registered item.

**Invariant (the whole point of #28):** `grep -rl 'import litellm' agentkit/` returns exactly
one path: `agentkit/kernel/providers/litellm_provider.py`. Regression-testable.

---

## Recommended design

### Layering

```
loop / fusion / judge / subagent
        │  depends only on ▼
   ModelProvider (ABC)  +  Alfred pydantic types (Message, ToolDef, ModelResponse, StreamDelta, Usage)
        │  implemented by ▼
   LiteLLMProvider  ──converts──▶  litellm.completion / acompletion
        │
   (future) AnthropicSDKProvider, VLLMProvider, MockProvider … core untouched
```

### Sync vs async

Core loop is async (asyncio event loop owns the session, Decision #5/#6). The ABC's
primary method is **async** (`async def complete`, `async def stream`); LiteLLMProvider
calls `litellm.acompletion`. A sync convenience wrapper is NOT in the ABC (YAGNI — the
in-process SDK consumer can `asyncio.run`). One method shape, no sync/async drift.

### Two methods, not a `stream: bool` flag

`complete()` returns a whole `ModelResponse`; `stream()` is an async generator of
`StreamDelta` and ends by yielding/returning the assembled final `ModelResponse`.
Rationale: a `bool` flag forces a union return type (`ModelResponse | AsyncIterator`),
which leaks into every caller's type signature. Two methods keep each return type clean.
`complete()` is the default path (in-process SDK reuse, fusion workers, judge — none need
tokens); `stream()` is opt-in for the TUI/SSE path (Decision #15 `stream_delta` is
opt-in). `stream()` MUST still produce the full final `ModelResponse` (with usage) so the
loop persists the SSoT message and reads cache numbers regardless of mode.

### MockProvider ships in the same module

A deterministic `MockProvider` (canned responses / scripted tool calls) lives beside
LiteLLMProvider. It lets every other module's unit test run with zero network and no
litellm import — the e2e rows (#1, #17) cover the *real* litellm path; units use Mock.

---

## ABC + message types sketch

Alfred's owned types are deliberately a **subset** of the OpenAI-shaped schema litellm
already normalizes to — this keeps conversion near-trivial while still being *our* contract
(so a future non-litellm impl is forced to honor the same shape, not litellm's).

```python
# agentkit/kernel/providers/types.py  — Alfred-owned, zero litellm import
from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]

class ContentBlock(BaseModel):
    # text-only for MVP; vision/audio are a later additive block type
    type: Literal["text"] = "text"
    text: str
    # cache breakpoint annotation, placed by context-assembly, passed through verbatim
    # to litellm for Anthropic; ignored for OpenAI (prefix-auto-cache). Decision #29.
    cache_control: dict[str, Any] | None = None  # e.g. {"type": "ephemeral"}

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]          # PARSED — Alfred owns json.loads, not callers
    raw_arguments: str = ""            # original JSON string kept for trace fidelity

class Message(BaseModel):
    role: Role
    # str for simple turns; list[ContentBlock] when a cache breakpoint or multimodal needed
    content: str | list[ContentBlock] | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)   # assistant → tool requests
    tool_call_id: str | None = None    # set on role=="tool" result messages
    name: str | None = None            # optional tool name on result message

class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]         # JSON Schema (object). SSoT for tool shape.

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0             # cache READ hits (see §cache below)
    cache_creation_tokens: int = 0     # Anthropic cache WRITE (0 on OpenAI)

class ModelResponse(BaseModel):
    message: Message                   # the assistant message (text + tool_calls)
    usage: Usage
    finish_reason: str | None = None   # "stop" | "tool_calls" | "length" | ...
    model: str = ""                    # resolved model id actually used
    raw: dict[str, Any] | None = None  # litellm dict for debugging; never read by core

class StreamDelta(BaseModel):
    # transient render projection (Decision #15) — NOT persisted to session store
    text: str | None = None                       # incremental assistant text
    tool_call_fragment: ToolCallFragment | None = None
    usage: Usage | None = None                    # present only on the final chunk
    finish_reason: str | None = None

class ToolCallFragment(BaseModel):
    index: int                          # which tool call this fragment belongs to
    id: str | None = None               # arrives on the first fragment
    name: str | None = None             # arrives on the first fragment
    arguments_delta: str = ""           # streamed JSON string piece (concatenate by index)
```

```python
# agentkit/kernel/providers/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator
from .types import Message, ToolDef, ModelResponse, StreamDelta

class ModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,        # "auto" | "none" | "required"
        **params: object,                       # temperature, max_tokens, etc.
    ) -> ModelResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> AsyncIterator[StreamDelta]: ...
        # async generator; final yielded StreamDelta carries usage + finish_reason.
        # Implementations also expose the assembled ModelResponse to the loop — see boundary.
```

**Design notes**
- `arguments` is parsed (`dict`) at the boundary so every tool handler gets a dict, not a
  JSON string — one parse site, fail-loud on malformed JSON (Decision: model output is
  untrusted; validate at boundary per `llm-prompt-and-boundary-contracts`). `raw_arguments`
  retained for the trace store (#17) so we can replay exactly what the model emitted.
- `tool_choice` kept as a small enum-ish str, not litellm's union, to avoid leaking the
  `{"type":"function",...}` object shape into core; LiteLLMProvider expands "required"/named
  forms at the boundary if needed.
- `content` allows bare `str` for the common case and `list[ContentBlock]` only when a
  cache breakpoint or multimodal block is needed — matches what litellm accepts and keeps
  90% of messages tiny.

---

## LiteLLMProvider boundary

```python
# agentkit/kernel/providers/litellm_provider.py  — the ONLY litellm importer
import json, os, litellm
from litellm import acompletion
from litellm.exceptions import (
    AuthenticationError, RateLimitError, BadRequestError, Timeout,
    APIConnectionError, ContextWindowExceededError, ServiceUnavailableError, APIError,
)
from .base import ModelProvider
from .types import (Message, ToolDef, ToolCall, ModelResponse, Usage, StreamDelta,
                    ContentBlock)
from .errors import (ProviderAuthError, ProviderRateLimit, ProviderBadRequest,
                     ProviderTimeout, ProviderUnavailable, ProviderContextExceeded,
                     ProviderError)

class LiteLLMProvider(ModelProvider):
    def __init__(self, *, model: str, api_key: str | None, base_url: str | None = None,
                 http_headers: dict | None = None, query_params: dict | None = None,
                 extra: dict | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.http_headers = http_headers or {}
        self.query_params = query_params or {}
        self.extra = extra or {}     # api_version, timeout, etc.
```

### Request conversion (Alfred → litellm kwargs)

```python
def _to_litellm_messages(self, messages: list[Message]) -> list[dict]:
    out = []
    for m in messages:
        d: dict = {"role": m.role}
        if isinstance(m.content, list):
            d["content"] = [self._block(b) for b in m.content]   # carries cache_control
        elif m.content is not None:
            d["content"] = m.content
        if m.tool_calls:                                          # assistant → requests
            d["tool_calls"] = [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.name,
                             "arguments": tc.raw_arguments or json.dumps(tc.arguments)},
            } for tc in m.tool_calls]
        if m.tool_call_id:                                        # role=="tool" result
            d["tool_call_id"] = m.tool_call_id
            if m.name: d["name"] = m.name
        out.append(d)
    return out

@staticmethod
def _block(b: ContentBlock) -> dict:
    d = {"type": "text", "text": b.text}
    if b.cache_control:                       # Anthropic ephemeral passthrough (#29)
        d["cache_control"] = b.cache_control  # litellm forwards verbatim
    return d

@staticmethod
def _to_litellm_tools(tools: list[ToolDef] | None) -> list[dict] | None:
    if not tools: return None
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description,
                          "parameters": t.parameters}} for t in tools]

def _call_kwargs(self, messages, tools, tool_choice, params) -> dict:
    kw = {"model": self.model,
          "messages": self._to_litellm_messages(messages),
          "api_key": self.api_key, **params}
    if self.base_url:     kw["api_base"] = self.base_url       # NOTE: litellm uses api_base
    if self.http_headers: kw["extra_headers"] = self.http_headers
    if self.query_params: kw["extra_query"] = self.query_params
    if "api_version" in self.extra: kw["api_version"] = self.extra["api_version"]
    t = self._to_litellm_tools(tools)
    if t:           kw["tools"] = t
    if tool_choice: kw["tool_choice"] = tool_choice
    return kw
```

litellm uses **`api_base`** (alias `base_url` accepted) and **`extra_headers`** for custom
gateway routing — confirmed in completion/input docs. Azure `api-version` can be passed as a
top-level `api_version` kwarg OR as `extra_query` (Codex puts it in `query_params`; the
key-proxy at `127.0.0.1:8888` expects it on the query string — use `extra_query`).

### complete()

```python
async def complete(self, messages, tools=None, tool_choice=None, **params):
    try:
        r = await acompletion(**self._call_kwargs(messages, tools, tool_choice, params))
    except Exception as e:
        raise self._map_exc(e) from e
    return self._to_response(r)

def _to_response(self, r) -> ModelResponse:
    choice = r.choices[0]
    msg = choice.message
    tool_calls = []
    for tc in (msg.tool_calls or []):
        raw = tc.function.arguments or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:               # untrusted model output → fail loud
            raise ProviderBadRequest(f"tool call args not JSON: {raw!r}") from e
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                   arguments=args, raw_arguments=raw))
    return ModelResponse(
        message=Message(role="assistant", content=msg.content, tool_calls=tool_calls),
        usage=self._to_usage(r.usage),
        finish_reason=choice.finish_reason,
        model=r.model,
        raw=r.model_dump() if hasattr(r, "model_dump") else dict(r),
    )
```

### Usage / cache reading (VERIFIED field names — Decision #29)

litellm returns an **OpenAI-compatible** usage object across all providers. Verified shape
from litellm prompt-caching docs:

```json
{ "prompt_tokens": 2006, "completion_tokens": 300, "total_tokens": 2306,
  "prompt_tokens_details": { "cached_tokens": 1920 },
  "completion_tokens_details": { "reasoning_tokens": 0 },
  "cache_creation_input_tokens": 0 }
```

So through litellm, read **both vendors** the same way:

```python
@staticmethod
def _to_usage(u) -> Usage:
    # u may be a pydantic obj or dict depending on litellm version → normalize to dict
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    details = d.get("prompt_tokens_details") or {}
    cached = (details.get("cached_tokens") if isinstance(details, dict) else
              getattr(details, "cached_tokens", 0)) or 0
    return Usage(
        prompt_tokens=d.get("prompt_tokens", 0),
        completion_tokens=d.get("completion_tokens", 0),
        total_tokens=d.get("total_tokens", 0),
        cached_tokens=cached,                                  # READ hits, both vendors
        cache_creation_tokens=d.get("cache_creation_input_tokens", 0) or 0,  # Anthropic WRITE
    )
```

Field-name reconciliation (the thing Decision #29 demanded we verify):

| Concept | Via litellm (use this) | Native Anthropic | Native OpenAI |
|---|---|---|---|
| cache read hits | `usage.prompt_tokens_details.cached_tokens` | `usage.cache_read_input_tokens` | `usage.prompt_tokens_details.cached_tokens` |
| cache write | `usage.cache_creation_input_tokens` | `usage.cache_creation_input_tokens` | n/a (auto, not reported) |

Because Alfred goes **through litellm**, the boundary reads the normalized
`prompt_tokens_details.cached_tokens` for cache-read on both vendors. A future native
AnthropicSDKProvider would instead read `cache_read_input_tokens` — that mapping lives in
*that* impl, not in core. Core only ever sees `Usage.cached_tokens`.

**Runtime fail-loud (spec §7 / #29c):** the loop (not the provider) compares turn-2
`Usage.cached_tokens` against the frozen-prefix size; if it stays 0 across a `--continue`
session → WARNING. The provider's only job is to populate the field correctly.

### Cache-control injection per vendor

The provider does **not** decide where the breakpoint goes — context-assembly placed
`cache_control={"type":"ephemeral"}` on the last static `ContentBlock` (Decision #21/#29).
The provider's per-vendor behavior:
- **Anthropic** (`model` starts `anthropic/` / `claude-`): pass the `cache_control` block
  through verbatim — litellm forwards it (confirmed in prompt_caching docs; optional
  `"ttl":"1h"`).
- **OpenAI/Azure**: there is no annotation; caching is automatic on a ≥1024-token stable
  prefix. The boundary should **strip** `cache_control` keys before sending (avoid an
  "unsupported param" risk) — i.e. `_block()` emits `cache_control` only when the model is
  Anthropic-family. (Refine `_block` to be model-aware; sketch above shows the passthrough
  case.)

### Streaming → StreamDelta

litellm `stream=True` yields chunks with `chunk.choices[0].delta`; `delta.content` is the
incremental text and `delta.tool_calls` is a list of fragments each carrying `index`, and
on the first fragment `id` + `function.name`, then `function.arguments` string pieces.
`stream_options={"include_usage": True}` makes the final chunk carry `usage`.

```python
async def stream(self, messages, tools=None, tool_choice=None, **params):
    kw = self._call_kwargs(messages, tools, tool_choice, params)
    kw["stream"] = True
    kw["stream_options"] = {"include_usage": True}     # usage in final chunk
    chunks = []
    try:
        resp = await acompletion(**kw)
        async for chunk in resp:
            chunks.append(chunk)
            d = chunk.choices[0].delta if chunk.choices else None
            usage = getattr(chunk, "usage", None)
            fin = chunk.choices[0].finish_reason if chunk.choices else None
            if d and getattr(d, "content", None):
                yield StreamDelta(text=d.content)
            for tc in (getattr(d, "tool_calls", None) or []):
                yield StreamDelta(tool_call_fragment=ToolCallFragment(
                    index=tc.index, id=getattr(tc, "id", None),
                    name=(tc.function.name if tc.function else None),
                    arguments_delta=(tc.function.arguments or "") if tc.function else "",
                ))
            if usage or fin:
                yield StreamDelta(usage=self._to_usage(usage) if usage else None,
                                  finish_reason=fin)
    except Exception as e:
        raise self._map_exc(e) from e
    # reassemble the SSoT message for the loop to persist (Decision #15: delta not persisted)
    full = litellm.stream_chunk_builder(chunks, messages=kw["messages"])
    self._last_full = self._to_response(full)     # loop reads provider.last_full after drain
```

`litellm.stream_chunk_builder(chunks, messages=...)` reassembles fragments (including
partial tool-call argument strings) into a normal `ModelResponse` — reuse it rather than
hand-rolling fragment concatenation. The loop drains `stream()` for the TUI projection,
then reads the assembled full response to persist the message + read usage. (Exact handoff
of the assembled response — return value vs. attribute vs. a terminal `StreamDelta` — is an
open question below; the attribute sketch above is the least-surprising option but couples
caller to "drain fully first".)

### Exception mapping

litellm exceptions subclass the matching `openai` exception types and import from
`litellm.exceptions` (also re-exported from `litellm`). Map them to **Alfred-owned** error
types so core never `except`s a litellm/openai class (same SSoT logic as the type wrapping):

```python
def _map_exc(self, e: Exception) -> ProviderError:
    return {
        AuthenticationError:        ProviderAuthError,
        RateLimitError:             ProviderRateLimit,
        Timeout:                    ProviderTimeout,
        APIConnectionError:         ProviderUnavailable,
        ServiceUnavailableError:    ProviderUnavailable,
        ContextWindowExceededError: ProviderContextExceeded,
        BadRequestError:            ProviderBadRequest,
    }.get(type(e), ProviderError)(str(e))
```

Verified litellm → openai base mapping (litellm exception_mapping docs):

| litellm | openai base | HTTP | Alfred error | Retryable? |
|---|---|---|---|---|
| AuthenticationError | openai.AuthenticationError | 401 | ProviderAuthError | no (fail loud at startup-ish) |
| PermissionDeniedError | openai.PermissionDeniedError | 403 | ProviderAuthError | no |
| BadRequestError | openai.BadRequestError | 400 | ProviderBadRequest | no |
| ContextWindowExceededError | ⊂ BadRequestError | 400 | ProviderContextExceeded | no (loop may compress) |
| NotFoundError | openai.NotFoundError | 404 | ProviderBadRequest | no |
| Timeout | openai.APITimeoutError | 408 | ProviderTimeout | yes |
| RateLimitError | openai.RateLimitError | 429 | ProviderRateLimit | yes (backoff) |
| APIConnectionError | openai.APIConnectionError | 5xx | ProviderUnavailable | yes |
| ServiceUnavailableError | openai.APIStatusError | 503 | ProviderUnavailable | yes |
| InternalServerError | openai.InternalServerError | ≥500 | ProviderUnavailable | yes |
| APIError | openai.APIError | 500 | ProviderError | maybe |

The retryable column matters for fusion (M6: worker timeout/quorum/judge-failure fallback)
— fusion can branch on the Alfred error *class*, never on litellm internals. Retry/backoff
policy itself is NOT the provider's job for MVP (litellm has `num_retries`, but keeping
retry in the provider hides failures from the loop's budget accounting). Recommendation:
expose litellm `num_retries`/`timeout` via `extra` params, default OFF, and let the loop /
fusion own retry decisions against the typed errors. (Open question.)

---

## Config schema

Per Decision #13 (`{type, params}` recursive) + #26 (Codex-style + `env_key` indirection).
This is a pydantic model under `AgentConfig.model`, `extra="forbid"`.

```python
# agentkit/kernel/providers/config.py
from pydantic import BaseModel, Field

class LiteLLMParams(BaseModel, extra="forbid"):
    model: str                                   # litellm model id, e.g. "claude-opus-4-8"
                                                 #   or "azure/gpt-5.5", "anthropic/claude-..."
    env_key: str | None = None                   # NAME of env var holding the key (NOT the key)
    base_url: str | None = None                  # proxy gateway, e.g. http://127.0.0.1:8888/openai
    http_headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)  # e.g. {"api-version": "..."}
    api_version: str | None = None               # Azure convenience (alt to query_params)
    extra: dict = Field(default_factory=dict)    # temperature, max_tokens, timeout, num_retries

class ProviderConfig(BaseModel, extra="forbid"):
    type: str                                    # registry key: "litellm" (default), "fusion", "mock"
    params: dict                                 # validated against the type's param model
```

### env_key indirection — how the secret is resolved

Config stores the env-var **name**; the factory reads `os.environ[name]` at construction.
Plaintext keys never enter owned config files (honors `llm-prompt-and-boundary-contracts`:
secrets don't enter owned config). Fail loud if the named var is missing/empty — a silent
empty key would surface as a confusing 401 deep in a call.

```python
def build_litellm_provider(p: LiteLLMParams) -> LiteLLMProvider:
    api_key = None
    if p.env_key:
        api_key = os.environ.get(p.env_key)
        if not api_key:
            raise ConfigError(                       # fail loud at startup, not Nth call
                f"env_key {p.env_key!r} referenced by model config is unset/empty")
    qp = dict(p.query_params)
    if p.api_version and "api-version" not in qp:
        qp["api-version"] = p.api_version
    return LiteLLMProvider(model=p.model, api_key=api_key, base_url=p.base_url,
                           http_headers=p.http_headers, query_params=qp, extra=p.extra)
```

### Worked examples (mapping the two REAL local setups — secrets redacted)

Anthropic via the local proxy gateway (env already has `ANTHROPIC_API_KEY` +
`ANTHROPIC_BASE_URL`; litellm reads `ANTHROPIC_API_KEY` automatically, but we make it
explicit via env_key for SSoT):

```yaml
model:
  type: litellm
  params:
    model: claude-opus-4-8         # or anthropic/claude-...
    env_key: ANTHROPIC_API_KEY     # NAME only
    base_url: http://127.0.0.1:8888    # proxy injects the real key downstream
```

Azure/OpenAI via the key-proxy (mirrors the real `~/.codex/config.toml` block, see below):

```yaml
model:
  type: litellm
  params:
    model: azure/gpt-5.5
    env_key: AZURE_OPENAI_API_KEY
    base_url: http://127.0.0.1:8888/openai
    query_params: {api-version: "2025-04-01-preview"}
    http_headers:
      Ocp-Apim-Subscription-Key: SUBSCRIPTION_KEY_ENV   # see note
      user: jingwech
```

Note on `http_headers` containing a secret: the real Codex config inlines a subscription
key in `http_headers`. Alfred's `env_key` rule says secrets must not live in owned config.
Recommendation: support **`${ENV_VAR}` interpolation in `http_headers` values** so a header
secret is also indirected (`Ocp-Apim-Subscription-Key: ${AZURE_APIM_KEY}`), resolved at
build time from `os.environ`. This keeps the one-rule invariant (no plaintext secrets in
config) without losing the gateway's custom-header capability. (Open question: interpolation
syntax — `${VAR}` vs a typed `{env: VAR}` object.)

Fusion (Decision #10/#11) is just nested provider configs — proves the recursion:

```yaml
model:
  type: fusion
  params:
    strategy: ensemble
    workers:
      - {type: litellm, params: {model: claude-opus-4-8, env_key: ANTHROPIC_API_KEY}}
      - {type: litellm, params: {model: azure/gpt-5.5, env_key: AZURE_OPENAI_API_KEY,
                                 base_url: "http://127.0.0.1:8888/openai",
                                 query_params: {api-version: "2025-04-01-preview"}}}
    aggregator:
      type: llm_judge
      params: {judge: {type: litellm, params: {model: claude-haiku-4-5,
                                               env_key: ANTHROPIC_API_KEY}}}
```

### Inspected REAL local configs (structure only — NO secrets copied)

`~/.codex/config.toml` — confirms the Codex provider-block shape Decision #26 borrows from:
- top-level: `model_provider = "custom"`, `model`, `model_reasoning_effort`, `wire_api`.
- `[model_providers.custom]` block with fields: `name`, `base_url`
  (`http://127.0.0.1:8888/openai` — the key-proxy), **`env_key = "AZURE_OPENAI_API_KEY"`**
  (the indirection pattern — NAME of env var, exactly what #26 adopts),
  `query_params = { api-version = "..." }`, `http_headers = { "Ocp-Apim-Subscription-Key" =
  <REDACTED>, "user" = <user> }`, `wire_api = "responses"`.
- Takeaway: our `LiteLLMParams` maps 1:1 to this block — `model`/`env_key`/`base_url`/
  `query_params`/`http_headers`. The only field with a plaintext secret is one
  `http_headers` value → motivates the `${ENV}` interpolation recommendation above.

`~/.claude/settings.json` — confirms the Anthropic proxy env the e2e (#26) reuses:
- `env` block: `ANTHROPIC_BASE_URL = "http://127.0.0.1:8888"`,
  `ANTHROPIC_API_KEY = <REDACTED placeholder; proxy injects the real key>`,
  `ANTHROPIC_DEFAULT_OPUS_MODEL`, empty `*_PROXY` vars.
- Takeaway: Anthropic path needs only `model` + `env_key: ANTHROPIC_API_KEY` +
  `base_url: http://127.0.0.1:8888`; the proxy handles real-key injection. Claude Code uses
  a **flat** `env` block (one key per var) — exactly the format #26 rejected in favor of
  Codex's structured `[model_providers.x]` block, because the flat form has no place for
  per-provider `query_params`/`http_headers`/`wire_api`.

---

## Industry refs with URLs

- LiteLLM `completion()` input / params (model, messages, stream, tools, tool_choice,
  api_base, api_key, extra_headers, extra_query): https://docs.litellm.ai/docs/completion/input
- LiteLLM prompt caching — VERIFIED usage fields
  (`prompt_tokens_details.cached_tokens`, `cache_creation_input_tokens`) + Anthropic
  `cache_control:{type:ephemeral}` passthrough: https://docs.litellm.ai/docs/completion/prompt_caching
- LiteLLM usage object ("OpenAI-compatible usage across all providers"):
  https://docs.litellm.ai/docs/completion/usage
- LiteLLM function / tool calling — `tool_calls[].function.{name,arguments}`, role=="tool"
  result messages, tools schema: https://docs.litellm.ai/docs/completion/function_call
- LiteLLM streaming + `stream_chunk_builder` reassembly:
  https://docs.litellm.ai/docs/completion/stream
- LiteLLM exception mapping (litellm ⊂ openai exception types, HTTP status table):
  https://docs.litellm.ai/docs/exception_mapping
- LiteLLM Azure config (`api_base`, `api_version`, `azure/<deployment>` model prefix):
  https://docs.litellm.ai/docs/providers/azure
- Anthropic prompt caching (native `cache_read_input_tokens` /
  `cache_creation_input_tokens`; "consistently zero ⇒ caching isn't happening"):
  https://docs.claude.com/en/docs/build-with-claude/prompt-caching
- OpenAI prompt caching (automatic, ≥1024-token prefix; `cached_tokens` in
  `prompt_tokens_details`): https://platform.openai.com/docs/guides/prompt-caching
- Codex provider config reference (`[model_providers.x]` `base_url`/`env_key`/
  `query_params`/`http_headers`/`wire_api`) — the borrowed config shape:
  https://github.com/openai/codex/blob/main/docs/config.md

---

## Open questions

1. **Stream → full-response handoff.** `stream()` must still produce the SSoT
   `ModelResponse`. Three options: (a) provider stashes `self._last_full` after drain
   (sketched; couples caller to drain-fully-first, and is not concurrency-safe if one
   provider instance streams two turns at once); (b) the final `StreamDelta` carries the
   whole assembled `ModelResponse`; (c) `stream()` returns an object exposing both the async
   iterator and an awaitable `.final()`. (c) is cleanest but heavier. **Pick before
   implementing the loop's stream path.** Note (a) breaks if fusion shares one streaming
   provider instance across workers — likely (b) or (c).

2. **Retry ownership.** litellm offers `num_retries`/`fallbacks`. Putting retry in the
   provider hides failures from iteration-budget accounting (H1) and from fusion's
   quorum/timeout logic (M6). Recommendation: retry OFF in provider by default, loop/fusion
   own it against typed errors. Confirm — or allow a thin provider-level retry for pure
   transport blips (`APIConnectionError`) only.

3. **`http_headers` secret indirection syntax.** Real Codex config inlines a subscription
   key in a header value, violating the "no plaintext secret in config" rule. Proposed
   `${ENV_VAR}` interpolation in header values vs. a typed `{env: VAR}` object. Decide the
   syntax (and whether it applies to `query_params` too).

4. **Model-family detection for cache_control stripping.** Deciding "is this Anthropic?"
   by `model` string prefix (`claude` / `anthropic/`) is brittle behind a custom
   `base_url`/proxy where the model id may be opaque (e.g. a gateway alias). Options: an
   explicit `cache_style: anthropic|openai|none` param on `LiteLLMParams`, or trust litellm
   to no-op the unknown `cache_control`. Verify litellm silently drops `cache_control` for
   OpenAI rather than erroring; if it errors, the explicit `cache_style` param is required.

5. **Reasoning-effort / `wire_api: responses`.** The real Codex config uses
   `wire_api = "responses"` + `model_reasoning_effort`. Does the local Azure proxy require
   the OpenAI *Responses* API rather than Chat Completions? litellm routes Chat Completions
   by default. If the gateway only speaks Responses, we need litellm's responses support or
   a `wire_api` param. Verify against the live `127.0.0.1:8888` gateway during e2e #1
   (OpenAI vendor pass).

6. **`tool_choice="required"` portability.** Anthropic vs OpenAI express forced tool use
   differently; litellm normalizes, but confirm "required" round-trips on both vendors
   (needed for any future structured-output-via-forced-tool path). Low priority for MVP.

7. **`reasoning_content` / thinking blocks.** Newer models emit reasoning content
   (`delta.reasoning_content` / message reasoning). Not in the MVP `Message` type. Decide
   whether to surface as a `StreamDelta.reasoning` field + a `Message` reasoning slot, or
   drop for MVP. Affects trace fidelity (#17) if we want to learn from reasoning traces.
