"""
AgentCallerWorkflow
===================
An agentic wrapper around RAGWorkflow. The caller decides per-request whether
to use the agentic path (agentic=True in kwargs) or the plain RAG path.

Agents
------
  retrieval     – delegates to RAGWorkflow(retrieval=True)
  summarization – delegates to RAGWorkflow(fulltext=True), then summarises
  comparison    – delegates to RAGWorkflow(use_comparison=True)
  speech        – direct LLM call; no retrieval

Usage
-----
  pipeline = get_unified_pipeline(...)

  # plain RAG (existing deployments unchanged)
  result = await pipeline.run(messages=[...], session_id="abc")

  # agentic routing
  result = await pipeline.run(messages=[...], session_id="abc", agentic=True)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Union

from llama_index.core.llms import ChatMessage
from llama_index.core.workflow import Event, StartEvent, StopEvent, Workflow, step

from ism_bot_core.llm.vllm import VLLM, MultiVLLM
from ism_bot_core.reranker import get_reranker
from ism_bot_core.retriever.docs_api import DocumentsAPIRetriever
from ism_bot_core.rag.requirements import RequirementsComparisonService

from . import agent_prompts
from .llama_workflow import RAGWorkflow, _chat_content, WF_TIMEOUT

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class AgentIdentified(Event):
    """Carries the classified agent name and extracted parameters."""
    agent: str                        # retrieval | summarization | comparison | speech
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    use_global: Optional[bool]
    use_session: Optional[bool]
    use_tesi: Optional[bool]
    params: Dict[str, Any]            # agent-specific extras (e.g. speech topic/length)


class RetrievalTask(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    use_global: Optional[bool]
    use_session: Optional[bool]
    use_tesi: Optional[bool]


class SummarizationTask(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    use_global: Optional[bool]
    use_session: Optional[bool]
    use_tesi: Optional[bool]


class ComparisonTask(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    use_session: Optional[bool]


class SpeechTask(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    topic: str
    length: str                       # short | medium | long | very long


class SpeechLoopState(Event):
    """Carries the accumulating speech text through each loop iteration."""
    accumulated_text: str
    words_so_far: int
    target_words: int
    iteration: int
    max_iterations: int
    topic: str
    length_label: str
    llm: VLLM
    messages: List[Dict[str, str]]


class AgentResult(Event):
    agent: str
    text: str
    nodes: list
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------------
# AgentCallerWorkflow
# ---------------------------------------------------------------------------

_VALID_AGENTS = {"retrieval", "summarization", "comparison", "speech"}
_FALLBACK_AGENT = "retrieval"

# Speech loop configuration
_SPEECH_TARGET_WORDS: Dict[str, int] = {
    "short": 150,
    "medium": 450,
    "long": 750,
    "very long": 1500,
}
_SPEECH_CHUNK_WORDS = 300   # words generated per segment
_SPEECH_MAX_ITERATIONS = 8  # absolute safety cap against infinite loops


class AgentCallerWorkflow(Workflow):
    """
    Wraps RAGWorkflow. Each incoming request is classified by an LLM into one
    of four agents, then dispatched to the corresponding step.
    """

    def __init__(
        self,
        *,
        rag_workflow: RAGWorkflow,
        llms: Dict[str, VLLM],
        timeout: float | None = 500.0,
    ):
        super().__init__(timeout=timeout)
        self._rag_wf = rag_workflow
        self._llms = llms

    # ------------------------------------------------------------------
    # Step 1 – classify intent
    # ------------------------------------------------------------------

    @step
    async def start(self, ev: StartEvent) -> AgentIdentified:
        model = ev.get("model") or list(self._llms.keys())[0]
        llm: VLLM = self._llms[model]
        messages: List[Dict[str, str]] = ev.messages

        history = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages[:-1]
        ) or "(no prior messages)"
        query_str = messages[-1]["content"]

        # --- classify ---
        classifier_msgs = [
            ChatMessage(role="system", content=agent_prompts.AGENT_CLASSIFIER_SYSTEM),
            ChatMessage(
                role="user",
                content=agent_prompts.AGENT_CLASSIFIER_USER.format(
                    history=history,
                    query_str=query_str,
                ),
            ),
        ]
        raw = (await _chat_content(llm, classifier_msgs)).strip().lower()
        agent = raw if raw in _VALID_AGENTS else _FALLBACK_AGENT

        # --- extract speech params if needed ---
        params: Dict[str, Any] = {}
        if agent == "speech":
            params = await self._extract_speech_params(llm, query_str)

        return AgentIdentified(
            agent=agent,
            messages=messages,
            llm=llm,
            session_id=ev.get("session_id"),
            use_global=ev.get("use_global"),
            use_session=ev.get("use_session"),
            use_tesi=ev.get("use_tesi"),
            params=params,
        )

    async def _extract_speech_params(self, llm: VLLM, query_str: str) -> Dict[str, Any]:
        """Ask LLM to pull topic + length out of the user's speech request."""
        msgs = [
            ChatMessage(role="system", content=agent_prompts.SPEECH_PARAM_SYSTEM),
            ChatMessage(
                role="user",
                content=agent_prompts.SPEECH_PARAM_USER.format(query_str=query_str),
            ),
        ]
        raw = (await _chat_content(llm, msgs)).strip()
        topic = query_str  # safe default
        length = "medium"  # safe default
        for line in raw.splitlines():
            line = line.strip()
            if line.lower().startswith("topic:"):
                topic = line.split(":", 1)[1].strip()
            elif line.lower().startswith("length:"):
                length = line.split(":", 1)[1].strip().lower()
        return {"topic": topic, "length": length}

    # ------------------------------------------------------------------
    # Step 2 – dispatch to the right task event
    # ------------------------------------------------------------------

    @step
    async def dispatch(
        self, ev: AgentIdentified
    ) -> Union[RetrievalTask, SummarizationTask, ComparisonTask, SpeechTask]:
        common = dict(
            messages=ev.messages,
            llm=ev.llm,
            session_id=ev.session_id,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
        )
        if ev.agent == "summarization":
            return SummarizationTask(**common)
        if ev.agent == "comparison":
            return ComparisonTask(
                messages=ev.messages,
                llm=ev.llm,
                session_id=ev.session_id,
                use_session=ev.use_session,
            )
        if ev.agent == "speech":
            return SpeechTask(
                messages=ev.messages,
                llm=ev.llm,
                topic=ev.params.get("topic", ev.messages[-1]["content"]),
                length=ev.params.get("length", "medium"),
            )
        # default: retrieval
        return RetrievalTask(**common)

    # ------------------------------------------------------------------
    # Step 3a – Retrieval agent
    # ------------------------------------------------------------------

    @step
    async def retrieval_step(self, ev: RetrievalTask) -> AgentResult:
        result = await self._rag_wf.run(
            messages=ev.messages,
            session_id=ev.session_id,
            retrieval=True,
            fulltext=False,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
            use_comparison=False,
            timeout=float(WF_TIMEOUT),
        )
        return AgentResult(
            agent="retrieval",
            text=result.get("response", ""),
            nodes=result.get("nodes", []),
            metadata=result.get("flags", {}),
        )

    # ------------------------------------------------------------------
    # Step 3b – Summarization agent
    # ------------------------------------------------------------------

    @step
    async def summarization_step(self, ev: SummarizationTask) -> AgentResult:
        # 1. Fetch full-text context via RAGWorkflow
        rag_result = await self._rag_wf.run(
            messages=ev.messages,
            session_id=ev.session_id,
            fulltext=True,
            retrieval=False,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
            use_comparison=False,
            timeout=float(WF_TIMEOUT),
        )
        context_str = rag_result.get("response", "")

        # 2. Second-pass LLM call: summarise the returned context
        query_str = ev.messages[-1]["content"]
        sum_msgs = [
            ChatMessage(role="system", content=agent_prompts.SUMMARIZATION_SYSTEM),
            ChatMessage(
                role="user",
                content=agent_prompts.SUMMARIZATION_USER.format(
                    query_str=query_str,
                    context_str=context_str,
                ),
            ),
        ]
        summary = await _chat_content(ev.llm, sum_msgs)

        return AgentResult(
            agent="summarization",
            text=summary,
            nodes=rag_result.get("nodes", []),
            metadata=rag_result.get("flags", {}),
        )

    # ------------------------------------------------------------------
    # Step 3c – Comparison agent
    # ------------------------------------------------------------------

    @step
    async def comparison_step(self, ev: ComparisonTask) -> AgentResult:
        result = await self._rag_wf.run(
            messages=ev.messages,
            session_id=ev.session_id,
            use_comparison=True,
            retrieval=False,
            fulltext=False,
            use_global=False,
            use_session=ev.use_session,
            use_tesi=False,
            timeout=float(WF_TIMEOUT),
        )
        return AgentResult(
            agent="comparison",
            text=result.get("response", ""),
            nodes=result.get("nodes", []),
            metadata={**result.get("flags", {}), "comparison": result.get("comparison")},
        )

    # ------------------------------------------------------------------
    # Step 3d – Speech agent (loop entry — generates first segment)
    # ------------------------------------------------------------------

    @step
    async def speech_step(self, ev: SpeechTask) -> SpeechLoopState:
        target_words = _SPEECH_TARGET_WORDS.get(ev.length, 450)
        chunk_words = min(_SPEECH_CHUNK_WORDS, target_words)

        msgs = [
            ChatMessage(role="system", content=agent_prompts.SPEECH_GENERATION_SYSTEM),
            ChatMessage(
                role="user",
                content=agent_prompts.SPEECH_SEGMENT_FIRST_USER.format(
                    topic=ev.topic,
                    length=ev.length,
                    target_words=target_words,
                    chunk_words=chunk_words,
                ),
            ),
        ]
        text = await _chat_content(ev.llm, msgs)
        return SpeechLoopState(
            accumulated_text=text,
            words_so_far=len(text.split()),
            target_words=target_words,
            iteration=1,
            max_iterations=_SPEECH_MAX_ITERATIONS,
            topic=ev.topic,
            length_label=ev.length,
            llm=ev.llm,
            messages=ev.messages,
        )

    # ------------------------------------------------------------------
    # Step 3e – Speech checker (loops until target length is reached)
    # ------------------------------------------------------------------

    @step
    async def speech_checker_step(
        self, ev: SpeechLoopState
    ) -> Union[SpeechLoopState, AgentResult]:
        words_remaining = ev.target_words - ev.words_so_far

        # Decide whether this is the final segment:
        #   - target already met, OR
        #   - safety cap reached, OR
        #   - so close to target that a conclusion is the right next move
        conclude_threshold = max(50, _SPEECH_CHUNK_WORDS // 2)
        is_final = (
            ev.words_so_far >= ev.target_words
            or ev.iteration >= ev.max_iterations
            or words_remaining <= conclude_threshold
        )

        if is_final:
            if ev.words_so_far < ev.target_words:
                # Generate closing segment
                chunk_words = min(_SPEECH_CHUNK_WORDS, max(50, words_remaining))
                msgs = [
                    ChatMessage(
                        role="system",
                        content=agent_prompts.SPEECH_GENERATION_SYSTEM,
                    ),
                    ChatMessage(
                        role="user",
                        content=agent_prompts.SPEECH_SEGMENT_CONCLUDE_USER.format(
                            topic=ev.topic,
                            length=ev.length_label,
                            target_words=ev.target_words,
                            words_so_far=ev.words_so_far,
                            accumulated_text=ev.accumulated_text,
                            chunk_words=chunk_words,
                        ),
                    ),
                ]
                conclusion = await _chat_content(ev.llm, msgs)
                final_text = ev.accumulated_text + "\n\n" + conclusion
            else:
                final_text = ev.accumulated_text

            return AgentResult(
                agent="speech",
                text=final_text,
                nodes=[],
                metadata={
                    "topic": ev.topic,
                    "length": ev.length_label,
                    "iterations": ev.iteration,
                    "words": len(final_text.split()),
                },
            )

        # Not done yet — generate the next body segment
        chunk_words = min(_SPEECH_CHUNK_WORDS, words_remaining)
        msgs = [
            ChatMessage(role="system", content=agent_prompts.SPEECH_GENERATION_SYSTEM),
            ChatMessage(
                role="user",
                content=agent_prompts.SPEECH_SEGMENT_CONTINUE_USER.format(
                    topic=ev.topic,
                    length=ev.length_label,
                    target_words=ev.target_words,
                    words_so_far=ev.words_so_far,
                    words_remaining=words_remaining,
                    accumulated_text=ev.accumulated_text,
                    chunk_words=chunk_words,
                ),
            ),
        ]
        new_segment = await _chat_content(ev.llm, msgs)
        new_text = ev.accumulated_text + "\n\n" + new_segment

        return SpeechLoopState(
            accumulated_text=new_text,
            words_so_far=len(new_text.split()),
            target_words=ev.target_words,
            iteration=ev.iteration + 1,
            max_iterations=ev.max_iterations,
            topic=ev.topic,
            length_label=ev.length_label,
            llm=ev.llm,
            messages=ev.messages,
        )

    # ------------------------------------------------------------------
    # Step 4 – Finalise
    # ------------------------------------------------------------------

    @step
    async def end(self, ev: AgentResult) -> StopEvent:
        result = {
            "response": ev.text,
            "nodes": ev.nodes,
            "agent": ev.agent,
            "flags": {
                **ev.metadata,
                "mode": ev.agent,
            },
        }
        if ev.agent == "comparison" and "comparison" in ev.metadata:
            result["comparison"] = ev.metadata["comparison"]
        return StopEvent(result)


# ---------------------------------------------------------------------------
# Unified pipeline factory
# ---------------------------------------------------------------------------

def get_unified_pipeline(
    *,
    documents_api_url: str,
    llm_api_url: str,
    llm_api_key: str | None = None,
    return_available_models: bool = False,
    vllm_username: str | None = None,
    vllm_password: str | None = None,
    token_url: str | None = None,
    vllm_client_id: str | None = None,
    expire_time: int | None = None,
    model_id: str | None = None,
):
    """
    Builds a unified adapter that serves both RAG and agentic requests.

    Callers pass ``agentic=True`` in kwargs to route through AgentCallerWorkflow.
    Omitting the flag (or ``agentic=False``) routes through the plain RAGWorkflow —
    keeping existing deployments completely unaffected.

    Returns the same _Adapter interface as ``get_query_pipeline``.
    If ``return_available_models=True``, returns (_Adapter, List[str]).
    """
    retriever = DocumentsAPIRetriever(api_url=documents_api_url)
    comparison_service = RequirementsComparisonService(
        retriever=retriever,
        top_k=int(os.getenv("REQ_COMP_TOP_K", "8")),
        max_concurrency=int(os.getenv("REQ_COMP_LLM_CONCURRENCY", "4")),
        retrieval_concurrency=int(os.getenv("REQ_COMP_RETR_CONCURRENCY", "8")),
        min_best_score=float(os.getenv("REQ_COMP_MIN_BEST_SCORE", "0.0")),
        min_evidence_chars=int(os.getenv("REQ_COMP_MIN_EVIDENCE_CHARS", "10")),
        llm_batch_size=int(os.getenv("REQ_COMP_LLM_BATCH_SIZE", "5")),
        max_evidence_chars_per_req=int(
            os.getenv("REQ_COMP_MAX_EVIDENCE_CHARS", "5000")
        ),
    )
    multi = MultiVLLM(
        api_urls=llm_api_url,
        api_keys=llm_api_key,
        vllm_username=vllm_username,
        vllm_password=vllm_password,
        token_url=token_url,
        vllm_client_id=vllm_client_id,
        expire_time=expire_time,
        model_id=model_id,
    )
    reranker = get_reranker(path="dummy")

    # Shared RAGWorkflow instance — used directly for plain RAG calls AND
    # called internally by AgentCallerWorkflow agent steps.
    rag_wf = RAGWorkflow(
        retriever_client=retriever,
        llms=multi.models,
        reranker=reranker,
        comparison_service=comparison_service,
        timeout=WF_TIMEOUT,
    )

    agentic_wf = AgentCallerWorkflow(
        rag_workflow=rag_wf,
        llms=multi.models,
        timeout=WF_TIMEOUT,
    )

    class _Adapter:
        async def run(self, **kwargs):
            """
            kwargs (all optional except messages):
              messages       : List[{"role": "...", "content": "..."}]  — required
              model          : Optional[str]
              session_id     : Optional[str]
              retrieval      : Optional[bool]
              fulltext       : Optional[bool]
              use_global     : Optional[bool]
              use_session    : Optional[bool]
              use_tesi       : Optional[bool]
              use_comparison : Optional[bool]
              agentic        : Optional[bool]   ← set True for LLM-based agent routing
                                                  Ignored when retrieval, fulltext, or
                                                  use_comparison is explicitly set — those
                                                  flags always take the direct RAG path.

            Returns:
              {
                "response" : str,
                "nodes"    : list,
                "flags"    : dict,
                # agentic-only keys (absent in plain RAG responses):
                "agent"    : str   (retrieval|summarization|comparison|speech)
              }
            """
            # Explicit mode flags mean the caller already knows the intent.
            # Honour them directly via RAGWorkflow regardless of agentic=True,
            # because the LLM classifier would silently override them otherwise.
            _EXPLICIT_MODE_FLAGS = ("retrieval", "fulltext", "use_comparison")
            has_explicit_flag = any(kwargs.get(f) for f in _EXPLICIT_MODE_FLAGS)

            use_agentic = kwargs.pop("agentic", False)
            if use_agentic and not has_explicit_flag:
                return await agentic_wf.run(**kwargs, timeout=float(WF_TIMEOUT))
            return await rag_wf.run(**kwargs, timeout=float(WF_TIMEOUT))

    if return_available_models:
        return _Adapter(), list(multi.models.keys())
    return _Adapter()
