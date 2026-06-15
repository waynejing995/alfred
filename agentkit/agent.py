from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agentkit.control.config import AgentConfig
from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.context import ContextAssembler, FrozenPrefix
from agentkit.kernel.events.base import serialize
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import SessionStart
from agentkit.kernel.instructions import InstructionResolver
from agentkit.kernel.loop import TurnCtx, TurnResult, emit_budget_event_to_bus, run_turn
from agentkit.kernel.permission import Autonomy, PermissionResolver
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.config import LiteLLMParams, build_litellm_provider
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.registries import ToolEntry, ToolsRegistry
from agentkit.stores.memory.base import MemoryProvider
from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext
from agentkit.stores.project import resolve_project_id
from agentkit.stores.session.base import SessionStore
from agentkit.stores.skill.loader import Catalog, build_catalog
from agentkit.tools import register_builtin_tools


class Agent:
    """Public SDK facade over the Tier-0 kernel loop."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None = None,
        config: AgentConfig | Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        alfred_home: str | Path | None = None,
        memory_provider: MemoryProvider | None = None,
        skill_catalog: Catalog | None = None,
        session_store: SessionStore | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        self.config = _coerce_config(config)
        self.provider = provider or _provider_from_config(self.config)
        self.tools = _coerce_tools(tools)
        self.cwd = Path(cwd or ".").resolve()
        self.alfred_home = Path(alfred_home).expanduser() if alfred_home is not None else None
        self.memory_provider = memory_provider or _memory_from_config(self.config)
        self.skill_catalog = skill_catalog or _skill_catalog_from_config(self.config)
        self.session_store = session_store
        self.session_id = resume_session_id or str(uuid.uuid4())
        self.history: list = (
            session_store.get_messages(resume_session_id)
            if session_store is not None and resume_session_id is not None
            else []
        )
        self._persisted_count = len(self.history)
        self._session_created = resume_session_id is not None
        self.last_events: list[dict[str, Any]] = []
        self.last_instruction_manifest: list[dict[str, object]] = []
        self.last_memory_blocks: list[dict[str, Any]] = []
        self.last_skill_l0: list[dict[str, str]] = []
        self._assembler: ContextAssembler | None = None

    async def run(
        self,
        prompt: str,
        *,
        stream: bool = False,
        event_sink=None,
        tool_choice: str | None = None,
    ) -> TurnResult:
        bus = EventBus()
        captured: list[dict[str, Any]] = []

        def capture(event):
            frame = serialize(event)
            captured.append(frame)
            if event_sink is not None:
                event_sink(frame)

        bus.on("*", capture)
        new_session = self._assembler is None
        if self._assembler is None:
            self._assembler = self._build_assembler(prompt)
        self._ensure_session(prompt)
        if new_session:
            await bus.emit(
                SessionStart(
                    session_id=self.session_id,
                    epoch=self._assembler.epoch,
                    manifest={
                        "instructions": self.last_instruction_manifest,
                        "memory": self.last_memory_blocks,
                        "skills": self.last_skill_l0,
                    },
                )
            )
        budget = IterationBudget(
            self.config.budget.max_tool_calls if self.config is not None else 20,
            on_event=emit_budget_event_to_bus(bus),
        )
        permission = _permission_from_config(self.config)
        ctx = TurnCtx(
            provider=self.provider,
            tools=self.tools,
            budget=budget,
            bus=bus,
            history=self.history,
            assembler=self._assembler,
            permission=permission,
            autonomy=self.config.autonomy if self.config is not None else Autonomy.ASSIST,
            session_id=self.session_id,
            tool_choice=tool_choice,
        )
        result = await run_turn(ctx, prompt, stream=stream)
        await asyncio.sleep(0)
        self.history = ctx.history
        self._persist_new_messages()
        self.last_events = captured
        return result

    def run_sync(
        self,
        prompt: str,
        *,
        stream: bool = False,
        event_sink=None,
        tool_choice: str | None = None,
    ) -> TurnResult:
        return asyncio.run(
            self.run(prompt, stream=stream, event_sink=event_sink, tool_choice=tool_choice)
        )

    def _build_assembler(self, prompt: str) -> ContextAssembler:
        resolved = InstructionResolver().resolve(self.cwd, self.alfred_home)
        persona, user, memory = self._prefetch_memory(prompt)
        self.last_instruction_manifest = resolved.manifest()
        return ContextAssembler(
            FrozenPrefix.build(
                tools=self.tools.tool_defs(),
                persona=persona,
                user=user,
                project_instructions=resolved.merged,
                memory=memory,
                skill_l0=self._skill_l0_text(),
            )
        )

    def _skill_l0_text(self) -> str:
        if self.skill_catalog is None:
            self.last_skill_l0 = []
            return ""
        self.last_skill_l0 = self.skill_catalog.l0()
        return self.skill_catalog.l0_text()

    def _prefetch_memory(self, prompt: str) -> tuple[str, str, str]:
        if self.memory_provider is None:
            self.last_memory_blocks = []
            return "", "", ""
        ctx = MemoryContext(project_id=resolve_project_id(self.cwd), resumed_tail=[prompt])
        retrieved = self.memory_provider.prefetch(ctx)
        self.last_memory_blocks = [block.model_dump(mode="json") for block in retrieved.blocks]
        persona = _join_blocks(block for block in retrieved.blocks if block.kind == "persona")
        user = _join_blocks(block for block in retrieved.blocks if block.kind == "user")
        facts = _join_blocks(block for block in retrieved.blocks if block.kind == "fact")
        return persona, user, facts

    def _ensure_session(self, prompt: str) -> None:
        if self.session_store is None or self._session_created:
            return
        self.session_id = self.session_store.create_session(
            source="cli",
            model=getattr(self.provider, "model", ""),
            model_config=self.config.model.model_dump(mode="json")
            if self.config is not None
            else {"type": "mock"},
            system_prompt=_system_prompt_text(self._assembler),
            title=prompt[:80],
        )
        self._session_created = True

    def _persist_new_messages(self) -> None:
        if self.session_store is None:
            return
        for message in self.history[self._persisted_count :]:
            self.session_store.add_message(self.session_id, message)
        self._persisted_count = len(self.history)


def _coerce_config(config: AgentConfig | Mapping[str, Any] | None) -> AgentConfig | None:
    if config is None or isinstance(config, AgentConfig):
        return config
    data = dict(config)
    data.setdefault("model", {"type": "mock"})
    return AgentConfig.model_validate(data)


def _provider_from_config(config: AgentConfig | None) -> ModelProvider:
    if config is None or config.model.type == "mock":
        return MockProvider()
    if config.model.type == "litellm":
        return build_litellm_provider(LiteLLMParams.model_validate(config.model.params))
    raise ValueError(f"unsupported model provider type: {config.model.type}")


def _memory_from_config(config: AgentConfig | None) -> MemoryProvider | None:
    if config is None or config.memory is None:
        return None
    if config.memory.type == "files":
        params = dict(config.memory.params)
        return FilesMemoryProvider(**params)
    raise ValueError(f"unsupported memory provider type: {config.memory.type}")


def _skill_catalog_from_config(config: AgentConfig | None) -> Catalog | None:
    if config is None or not config.skill_sources:
        return None
    roots = []
    for source in config.skill_sources:
        if source.type in {"dir", "bundled"}:
            roots.append(source.params["path"])
        else:
            raise ValueError(f"unsupported skill source type: {source.type}")
    return build_catalog(roots)


def _permission_from_config(config: AgentConfig | None) -> PermissionResolver:
    resolver = PermissionResolver.default()
    if config is None:
        return resolver
    for layer in config.permission:
        resolver = resolver.with_layer(layer)
    return resolver


def _coerce_tools(
    tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None,
) -> ToolsRegistry:
    if isinstance(tools, ToolsRegistry):
        return tools
    registry = ToolsRegistry()
    if tools is None:
        register_builtin_tools(registry)
        return registry
    if isinstance(tools, Mapping):
        for name, handler in tools.items():
            registry.register(
                name=name,
                description=f"User supplied tool: {name}",
                parameters={"type": "object", "properties": {}},
                handler=handler,
                permission_bucket="read",
            )
        return registry
    for entry in tools:
        if not isinstance(entry, ToolEntry):
            raise TypeError("tools iterable must contain ToolEntry instances")
        registry.register(
            name=entry.name,
            description=entry.description,
            parameters=entry.parameters,
            handler=entry.handler,
            permission_bucket=entry.permission_bucket,
            refundable=entry.refundable,
        )
    return registry


def _join_blocks(blocks) -> str:
    return "\n\n".join(block.text for block in blocks)


def _system_prompt_text(assembler: ContextAssembler | None) -> str:
    if assembler is None:
        return ""
    return "\n\n".join(block.text for block in assembler.prefix.content_blocks())


__all__ = ["Agent"]
