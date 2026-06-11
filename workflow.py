from __future__ import annotations

from typing import Any, Protocol

from llama_index.core.workflow import (
    Context,
    Event,
    StartEvent,
    StopEvent,
    Workflow,
    step,
)

from ism_bot_core.llamaforge.capabilities.rag.generation import GenerationCap
from ism_bot_core.llamaforge.capabilities.rag.rerank import RerankCap
from ism_bot_core.llamaforge.capabilities.rag.retrieval import RetrievalCap
from ism_bot_core.llamaforge.capabilities.rag.routing import RoutingCapability
from ism_bot_core.llamaforge.capabilities.rag.transform import QueryTransformCap
from ism_bot_core.llamaforge.schema import MODE, PipelineState
from ism_bot_core.llm.vllm import VLLM
from ism_bot_core.logger import get_logger
from ism_bot_core.retriever.docs_api import DocumentsAPIRetriever

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Protocols (replace bare `Any | None` with real contracts)
# --------------------------------------------------------------------------- #
class Reranker(Protocol):
    async def rerank(self, *args: Any, **kwargs: Any) -> Any: ...


# --------------------------------------------------------------------------- #
# Typed events. Each carries the working state; shared request-scoped data
# (messages, session_id, capabilities) lives in ctx.store instead.
# --------------------------------------------------------------------------- #
class RouteEvent(Event):
    """RAG path: needs routing + transform before retrieval."""
    state: PipelineState


class RetrieveEvent(Event):
    """Retrieval needed (full-text path, or RAG resolved to a search route)."""
    state: PipelineState


class RerankEvent(Event):
    """Retrieved context that should be reranked before generation."""
    state: PipelineState


class GenerateEvent(Event):
    """All paths converge here for the single generation step."""
    state: PipelineState


class ProgressEvent(Event):
    """Streamed to the client for live stage status."""
    stage: str
    detail: str | None = None


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #
class ChatWorkflow(Workflow):
    def __init__(
        self,
        retriever_client: DocumentsAPIRetriever,
        llms: dict[str, VLLM],
        default_model: str | None = None,
        reranker: Reranker | None = None,
        memory_provider: dict[str, Any] | None = None,
        timeout: float = 500.0,
        verbose: bool = False,
        **kwargs: Any,
    ):
        super().__init__(timeout=timeout, verbose=verbose, **kwargs)
        if not llms:
            raise ValueError("ChatWorkflow requires at least one LLM.")

        self._retriever = retriever_client
        self._llms = llms
        self._reranker = reranker
        self._memory_provider = memory_provider

        # Explicit default instead of relying on dict insertion order.
        if default_model is not None and default_model not in llms:
            raise ValueError(
                f"default_model={default_model!r} not in llms {sorted(llms)}"
            )
        self._default_model = default_model or next(iter(llms))

        self._cache: dict[str, dict[str, Any]] = {}

        if verbose:
            logger.debug(
                "ChatWorkflow: initialized with %d LLMs, default=%s, timeout=%.1f",
                len(llms),
                self._default_model,
                timeout,
            )

    # ----------------------------------------------------------------------- #
    # Resource resolution. Fails loudly on unknown models and caches under the
    # *resolved* key so a typo can never poison another model's entry.
    # ----------------------------------------------------------------------- #
    def _resolve_model(self, model_name: str | None) -> str:
        name = model_name or self._default_model
        if name not in self._llms:
            raise KeyError(
                f"Unknown model={name!r}. Available: {sorted(self._llms)}"
            )
        return name

    def _get_resources(self, model_name: str) -> dict[str, Any]:
        if model_name in self._cache:
            return self._cache[model_name]

        llm = self._llms[model_name]
        caps = {
            "routing": RoutingCapability(llm),
            "transform": QueryTransformCap(llm),
            "retrieval": RetrievalCap(self._retriever),
            "rerank": RerankCap(self._reranker),
            "generation": GenerationCap(llm, memory_provider=self._memory_provider),
        }

        if self._verbose:
            logger.debug("ChatWorkflow: cached capabilities for model=%s", model_name)

        self._cache[model_name] = caps
        return caps

    # ----------------------------------------------------------------------- #
    # Entry point: validate, build state, resolve resources, branch by event.
    # ----------------------------------------------------------------------- #
    @step
    async def prepare(
        self, ctx: Context, ev: StartEvent
    ) -> RouteEvent | RetrieveEvent | GenerateEvent | StopEvent:
        messages = ev.get("messages") or []
        if not messages or not messages[-1].get("content"):
            return StopEvent(result=self._error("No messages provided"))

        user_query = messages[-1]["content"]
        session_id = ev.get("session_id")

        try:
            model_name = self._resolve_model(ev.get("model"))
        except KeyError as e:
            return StopEvent(result=self._error(str(e)))

        caps = self._get_resources(model_name)

        state = PipelineState.from_request(
            user_query=user_query,
            messages=messages,
            mode=ev.get("mode"),
            strategy=ev.get("strategy"),
            verbose=ev.get("verbose", self._verbose),
        )

        # Request-scoped data shared across steps lives here, not threaded by hand.
        await ctx.store.set("messages", messages)
        await ctx.store.set("session_id", session_id)
        await ctx.store.set("caps", caps)
        await ctx.store.set("model_name", model_name)
        await ctx.store.set("requested_mode", state.intent)  # preserve original intent

        if self._verbose:
            logger.debug(
                "ChatWorkflow: model=%s intent=%s strategy=%s session_id=%s",
                model_name,
                state.intent,
                state.strategy,
                session_id,
            )

        match state.intent:
            case MODE.CHAT:
                return GenerateEvent(state=state)
            case MODE.FULLTEXT:
                return RetrieveEvent(state=state)
            case MODE.RAG:
                return RouteEvent(state=state)
            case _:
                return StopEvent(
                    result=self._error(f"Unsupported mode: {state.intent}")
                )

    # ----------------------------------------------------------------------- #
    # RAG only: decide whether the query needs retrieval at all, then transform.
    # ----------------------------------------------------------------------- #
    @step
    async def route(
        self, ctx: Context, ev: RouteEvent
    ) -> GenerateEvent | RetrieveEvent:
        ctx.write_event_to_stream(ProgressEvent(stage="routing"))
        caps = await ctx.store.get("caps")
        messages = await ctx.store.get("messages")

        state = await caps["routing"].execute(ev.state, messages=messages)

        # Routing may resolve a RAG request down to a plain chat answer.
        if state.intent == MODE.CHAT:
            return GenerateEvent(state=state)

        ctx.write_event_to_stream(ProgressEvent(stage="transform"))
        state = await caps["transform"].execute(state, messages=messages)
        return RetrieveEvent(state=state)

    # ----------------------------------------------------------------------- #
    # Shared by FULLTEXT and the RAG search path. Conditional rerank is
    # expressed by the *return type*, not a buried `if`.
    # ----------------------------------------------------------------------- #
    @step
    async def retrieve(
        self, ctx: Context, ev: RetrieveEvent
    ) -> RerankEvent | GenerateEvent:
        ctx.write_event_to_stream(ProgressEvent(stage="retrieval"))
        caps = await ctx.store.get("caps")
        session_id = await ctx.store.get("session_id")

        state = await caps["retrieval"].execute(ev.state, session_id=session_id)

        if self._reranker is not None:
            return RerankEvent(state=state)
        return GenerateEvent(state=state)

    @step
    async def rerank(self, ctx: Context, ev: RerankEvent) -> GenerateEvent:
        ctx.write_event_to_stream(ProgressEvent(stage="rerank"))
        caps = await ctx.store.get("caps")
        state = ev.state

        state = await caps["rerank"].execute(
            state, query=state.transformed_query or state.user_query
        )
        return GenerateEvent(state=state)

    # ----------------------------------------------------------------------- #
    # The single generation site every path converges on.
    # ----------------------------------------------------------------------- #
    @step
    async def generate(self, ctx: Context, ev: GenerateEvent) -> StopEvent:
        ctx.write_event_to_stream(ProgressEvent(stage="generation"))
        caps = await ctx.store.get("caps")
        messages = await ctx.store.get("messages")
        session_id = await ctx.store.get("session_id")

        state = await caps["generation"].execute(
            ev.state, messages=messages, session_id=session_id
        )

        requested_mode: MODE = await ctx.store.get("requested_mode")
        return StopEvent(
            result={
                "response": state.final_response,
                "nodes": state.reranked_nodes or state.context_nodes,
                "flags": {
                    "requested_mode": requested_mode.value,
                    "resolved_mode": state.intent.value,
                    "strategy": state.strategy.value,
                    "steps": state.steps_taken,
                },
                "error": None,
            }
        )

    # ----------------------------------------------------------------------- #
    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        # Consistent response shape so callers never branch on presence of keys.
        return {"response": None, "nodes": [], "flags": {}, "error": message}