from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
import datetime
from typing import Union
import asyncio
import os

from llama_index.core.workflow import Workflow, step, Event, StartEvent, StopEvent
from llama_index.core.llms import LLM
from llama_index.core.llms import ChatMessage

from ism_bot_core.reranker import get_reranker
from ism_bot_core.llm.vllm import VLLM, MultiVLLM
from ism_bot_core.retriever.docs_api import DocumentsAPIRetriever
from ism_bot_core.rag.requirements import RequirementsComparisonService
from . import prompts

WF_TIMEOUT = float(os.getenv("WF_TIMEOUT", "500.0"))  # 5 minutes


class UserQuery(Event):
    messages: List[Dict[str, str]]
    model: Optional[str]
    session_id: Optional[str]
    retrieval: Optional[bool]
    fulltext: Optional[bool]
    use_global: Optional[bool]
    use_tesi: Optional[bool]
    use_session: Optional[bool]
    use_comparison: Optional[bool]


class Loaded(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    retrieval: Optional[bool]
    fulltext: Optional[bool]
    use_global: Optional[bool]
    use_session: Optional[bool]
    use_tesi: Optional[bool]



class Routed(Event):
    messages: List[Dict[str, str]]
    llm: VLLM
    session_id: Optional[str]
    use_retrieval: bool
    use_fulltext: bool
    use_global: bool
    use_session: bool
    use_tesi: bool

class Rephrased(Event):
    query: str
    llm: VLLM
    messages: List[Dict[str, str]]
    session_id: Optional[str]
    use_global: bool
    use_session: bool
    use_tesi: bool

class Retrieved(Event):
    nodes: list
    query: str
    llm: VLLM
    messages: List[Dict[str, str]]
    use_global: Optional[bool] = None
    use_session: Optional[bool] = None
    use_tesi: Optional[bool] = None


class FulltextCtx(Event):
    context: str
    llm: VLLM
    messages: List[Dict[str, str]]
    use_global: Optional[bool] = None
    use_session: Optional[bool] = None
    use_tesi: Optional[bool] = None

class Answer(Event):
    text: str
    nodes: list
    use_retrieval: bool
    use_fulltext: bool
    use_global: bool
    use_session: bool
    use_comparison: Optional[bool] = None
    comparison: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    use_tesi: bool = False


# ------------- Helpers -------------
async def _chat_content(llm: VLLM, msgs: List[ChatMessage]) -> str:
    if hasattr(llm, "chat") and asyncio.iscoroutinefunction(getattr(llm, "chat")):
        response = await llm.chat(messages=msgs)
    else:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: llm.chat(messages=msgs))

    if hasattr(response, "message") and hasattr(
        response.message, "content"
    ):  # new standard response.message.content
        return response.message.content
    if hasattr(response, "text"):  # old apis response.text
        return str(response.text)
    return str(response)  # Fallback


async def _complete_text(llm: VLLM, prompt: str) -> str:
    if hasattr(llm, "complete") and asyncio.iscoroutinefunction(
        getattr(llm, "complete")
    ):
        response = await llm.complete(prompt, max_tokens=1024)
    else:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: llm.complete(prompt, max_tokens=1024)
        )
    return getattr(
        response,
        "text",
        getattr(response, "message", getattr(response, "content", str(response))),
    )


def _system_prompt_today() -> str:
    return prompts.QA_SYSTEM_PROMPT.format(today=datetime.date.today())


# ------------- Workflow -------------
class RAGWorkflow(Workflow):
    def __init__(
        self,
        *,
        retriever_client: DocumentsAPIRetriever,
        llms: Dict[str, VLLM],
        reranker,
        comparison_service: Optional[RequirementsComparisonService] = None,
        timeout: float | None = 500.0,
    ):
        super().__init__(timeout=timeout)
        self._retriever = retriever_client
        self._llms = llms
        self._reranker = reranker
        self._comparison_service = comparison_service

    @step
    async def start(self, ev: StartEvent) -> UserQuery:
        return UserQuery(
            messages=ev.messages,
            model=ev.get("model"),
            session_id=ev.get("session_id"),
            retrieval=ev.get("retrieval"),
            fulltext=ev.get("fulltext"),
            use_global=ev.get("use_global"),
            use_session=ev.get("use_session"),
            use_comparison=ev.get("use_comparison"),
            use_tesi=ev.get("use_tesi"),
        )

    @step
    async def load(self, ev: UserQuery) -> Union[Loaded, Answer]:
        model = ev.model or list(self._llms.keys())[0]
        llm = self._llms[model]

        # Comparison-only
        if ev.use_comparison:
            if not ev.session_id:
                raise ValueError("Requirements Comparison benötigt eine Session-ID.")
            if not self._comparison_service:
                raise RuntimeError("Comparison-Service ist nicht konfiguriert.")

            comparison_json = await self._comparison_service.compare_payload(
                llm=llm,
                session_id=ev.session_id,
            )

            return Answer(
                text="",
                nodes=[],
                use_retrieval=False,
                use_fulltext=False,
                use_global=False,
                use_session=bool(ev.use_session),
                use_comparison=True,
                comparison=comparison_json,  # dict, nicht liste
                session_id=ev.session_id,
            )

        # normal rag workflow
        return Loaded(
            messages=ev.messages,
            llm=llm,
            session_id=ev.session_id,
            retrieval=ev.retrieval,
            fulltext=ev.fulltext,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
        )

    @step
    async def route(self, ev: Loaded) -> Routed:
        use_ret = bool(ev.retrieval) if ev.retrieval is not None else False
        use_ft = bool(ev.fulltext) if ev.fulltext is not None else False

        # if no flag let llm decide
        if ev.retrieval is None and ev.fulltext is None:
            chat = [ChatMessage(role="system", content=prompts.ROUTER_SYSTEM)]
            for m in ev.messages[:-1]:
                chat.append(ChatMessage(role=m["role"], content=m["content"].strip()))
            chat.append(
                ChatMessage(
                    role="user",
                    content=prompts.ROUTER_CHAT.format(
                        query_str=ev.messages[-1]["content"]
                    ),
                )
            )
            decision = (await _chat_content(ev.llm, chat)).lower()
            use_ret = "ja" in decision
            use_ft = False

            # if both true do retreival
            if use_ret and use_ft:
                use_ret, use_ft = True, False

        return Routed(
            messages=ev.messages,
            llm=ev.llm,
            session_id=ev.session_id,
            use_retrieval=use_ret,
            use_fulltext=use_ft,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=bool(ev.use_tesi),
        )

    # Routed -> ggf. Rephrase (RAG) ODER Fulltext oder Direkte Antwort
    @step
    async def decide(self, ev: Routed) -> Union[Rephrased, FulltextCtx, Answer]:
        if ev.use_fulltext and not ev.use_retrieval:
            ctx = self._retriever.retrieve_fulltext(session_id=ev.session_id) or ""
            return FulltextCtx(
                context=ctx,
                llm=ev.llm,
                messages=ev.messages,
            )
        if ev.use_retrieval:
            conv = "\n".join(f"{m['role']}: {m['content']}" for m in ev.messages)
            prompt = prompts.REPHRASER_COMPLETE.format(conversation_str=conv)
            query = (await _complete_text(ev.llm, prompt)).strip()
            return Rephrased(
                query=query,
                llm=ev.llm,
                messages=ev.messages,
                session_id=ev.session_id,
                use_global=ev.use_global,
                use_session=ev.use_session,
                use_tesi=ev.use_tesi,
            )
        else:  # no rag no fulltext
            sys = _system_prompt_today()
            chat = [ChatMessage(role="system", content=sys)]
            for m in ev.messages:
                chat.append(ChatMessage(role=m["role"], content=m["content"]))
            text = await _chat_content(ev.llm, chat)
        return Answer(
            text=text,
            nodes=[],
            use_retrieval=False,
            use_fulltext=False,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
        )
    # Rephrased -> Retrieved (RAG:Retrieve + optional Rerank)
    # branch: RAG – Retrieve (+ optional rerank)
    @step
    async def retrieve(self, ev: Rephrased) -> Retrieved:
        nodes = self._retriever.retrieve(
            ev.query,
            session_id=ev.session_id,
            use_global=ev.use_global,
            use_session=ev.use_session,
            use_tesi=ev.use_tesi,
        )
        # optional rerank – tolerant
        ranked = nodes
        try:
            if callable(self._reranker):
                try:
                    ranked = self._reranker(nodes, query=ev.query)
                except TypeError:
                    ranked = self._reranker(nodes)
            elif hasattr(self._reranker, "rank"):
                ranked = self._reranker.rank(nodes, query=ev.query)
        except Exception:
            ranked = nodes
        return Retrieved(
            nodes=ranked,
            query=ev.query,
            llm=ev.llm,
            messages=ev.messages,
            use_global=ev.use_global,
            use_tesi=ev.use_tesi,
            use_session=ev.use_session
        )

    # Beide Pfade -> Answer
    @step
    async def answer_rag(self, ev: Retrieved) -> Answer:
        sys = _system_prompt_today()
        chat = [ChatMessage(role="system", content=sys)]
        for m in ev.messages[:-1]:
            chat.append(ChatMessage(role=m["role"], content=m["content"]))

        ctx = "\n\n".join(
            getattr(n, "text", getattr(n.node, "text", "")) for n in (ev.nodes or [])
        )
        user_prompt = prompts.QA_RAG_PROMPT.format(
            query_str=ev.messages[-1]["content"],
            context_str=ctx,
        )
        chat.append(ChatMessage(role="user", content=user_prompt))
        text = await _chat_content(ev.llm, chat)
        return Answer(
            text=text,
            nodes=ev.nodes,
            use_retrieval=True,
            use_fulltext=False,
            use_global=bool(ev.use_global),
            use_session=bool(ev.use_session),
            use_tesi=bool(ev.use_tesi),
        )

    @step
    async def answer_fulltext(self, ev: FulltextCtx) -> Answer:
        sys = _system_prompt_today()
        chat = [ChatMessage(role="system", content=sys)]
        for m in ev.messages[:-1]:
            chat.append(ChatMessage(role=m["role"], content=m["content"]))

        user_prompt = prompts.QA_FULLTEXT_PROMPT.format(
            query_str=ev.messages[-1]["content"], context_str=ev.context
        )
        chat.append(ChatMessage(role="user", content=user_prompt))
        text = await _chat_content(ev.llm, chat)
        return Answer(
            text=text,
            nodes=[],
            use_retrieval=False,
            use_fulltext=True,
            use_global=False,
            use_session=True,
        )

    @step
    async def end(self, ev: Answer) -> StopEvent:
        if ev.use_fulltext:
            mode = "fulltext"
        elif ev.use_retrieval:
            mode = "retrieval"
        else:
            mode = "chat_only"

        result = {
            "response": ev.text,
            "nodes": ev.nodes,
            "flags": {
                "use_session": bool(ev.use_session),
                "use_global": bool(ev.use_global),
                "use_tesi": bool(ev.use_tesi),
                "retrieval": bool(ev.use_retrieval),
                "fulltext": bool(ev.use_fulltext),
                "comparison": bool(ev.use_comparison),
                "mode": mode,
            },
        }
        if ev.comparison is not None:
            result["comparison"] = ev.comparison
        return StopEvent(result)


# --------- Adapter für backend api---------
def get_query_pipeline(
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
    import ism_bot_core
    import inspect
    import workflows

    print("ism_bot_core from:", ism_bot_core.__file__)
    print("workflows from:", workflows.__file__)
    print("RAGWorkflow from:", inspect.getsourcefile(RAGWorkflow))

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
    # llm_api_urls = llm_api_url.split(";")
    # llm_api_keys = (
    #     llm_api_key.split(";") if llm_api_key else [None for _ in llm_api_urls]
    # )
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

    wf = RAGWorkflow(
        retriever_client=retriever,
        llms=multi.models,
        reranker=reranker,
        comparison_service=comparison_service,
        timeout=WF_TIMEOUT,
    )

    print("WF_TIMEOUT configured:", WF_TIMEOUT)
    print("wf._timeout effective:", getattr(wf, "_timeout", None))
    print("wf class:", wf.__class__)
    print("wf module:", wf.__class__.__module__)
    print("wf file:", __import__(wf.__class__.__module__).__file__)
    print("wf._timeout:", getattr(wf, "_timeout", None))
    print("workflows.Workflow file:", __import__("workflows").__file__)

    class _Adapter:
        async def run(self, **kwargs):
            """
            Erwartete kwargs (wie früher):
              - messages: List[{"role": "...", "content": "..."}]
              - model: Optional[str]
              - session_id: Optional[str]
              - retrieval: Optional[bool]
              - fulltext: Optional[bool]
              - use_global: Optional[bool]
              - use_session: Optional[bool]
            Rückgabe:
              {
                "response": str,
                "nodes": list,
                "flags": {
                    "use_session": bool,
                    "use_global": bool,
                    "retrieval": bool,
                    "fulltext": bool,
                    "mode": "chat_only"|"retrieval"|"fulltext"
                }
              }
            """

            print("wf._timeout before run:", getattr(wf, "_timeout", None))
            print("kwargs timeout passed to run:", kwargs.get("timeout", "<none>"))

            result = await wf.run(**kwargs, timeout=float(500.0))
            print("wf._timeout after run:", getattr(wf, "_timeout", None))

            return result

    if return_available_models:
        return _Adapter(), list(multi.models.keys())
    return _Adapter()