# Agentic RAG Pipeline — Integration Guide

## Overview

This pipeline extends the existing `RAGWorkflow` with an agentic layer that can
intelligently route requests to specialist agents. Both the plain RAG path and
the agentic path share the same infrastructure, the same LLM, and the same
factory function. The switch happens **per request at runtime**, not at
deployment time.

```
                              ┌─────────────────────────────────────────┐
                              │          get_unified_pipeline()          │
                              │                                          │
  caller.run(agentic=False)──▶│  rag_wf  ────────────────────────────── ▶  response
                              │                                          │
  caller.run(agentic=True) ──▶│  agentic_wf ──▶  LLM classifier         │
                              │                       │                  │
                              │          ┌────────────┼────────────┐    │
                              │          ▼            ▼            ▼    │
                              │       retrieval  summarization  comparison  speech
                              │          │            │            │        │
                              │          └────────────┴────────────┴────────┘
                              │                       │                  │
                              │                  response ◀─────────────┘│
                              └─────────────────────────────────────────┘
```

---

## Files

| File | Role |
|---|---|
| `llama-workflow.py` | Original RAGWorkflow — **untouched** |
| `agent_prompts.py` | All LLM prompts for the agentic layer |
| `agent_caller.py` | `AgentCallerWorkflow`, Events, `get_unified_pipeline()` |

---

## Installation / Migration

### Step 1 — Replace the factory import

Your existing backend imports `get_query_pipeline`. Replace it with
`get_unified_pipeline` from `agent_caller`. The function signature is
**identical** — no other call-site changes are needed.

```python
# Before
from .llama_workflow import get_query_pipeline
pipeline = get_query_pipeline(
    documents_api_url=...,
    llm_api_url=...,
)

# After
from .agent_caller import get_unified_pipeline
pipeline = get_unified_pipeline(
    documents_api_url=...,
    llm_api_url=...,
)
```

### Step 2 — Existing calls continue to work unchanged

Any call that does not pass `agentic=True` is routed directly to the original
`RAGWorkflow` with zero behavioural change:

```python
result = await pipeline.run(
    messages=[{"role": "user", "content": "What is ISM?"}],
    session_id="abc",
    retrieval=True,
)
# → identical to the old get_query_pipeline behaviour
```

---

## Request kwargs reference

### Flags shared by both paths

| kwarg | Type | Description |
|---|---|---|
| `messages` | `List[{"role", "content"}]` | **Required.** Full conversation history, latest message last. |
| `model` | `str \| None` | LLM model key. Defaults to the first available model. |
| `session_id` | `str \| None` | Session ID for session-scoped retrieval and comparison. |
| `use_global` | `bool \| None` | Include global knowledge base in retrieval. |
| `use_session` | `bool \| None` | Include session-scoped documents in retrieval. |
| `use_tesi` | `bool \| None` | Include TESI corpus in retrieval. |

### Explicit mode flags (plain RAG path)

These flags bypass the LLM classifier entirely and route straight to
`RAGWorkflow`. Use them whenever the UI or API already knows the user's intent.

| kwarg | Type | Effect |
|---|---|---|
| `retrieval` | `bool` | Run vector retrieval + rerank + RAG answer generation. |
| `fulltext` | `bool` | Fetch full session document text, then generate answer. |
| `use_comparison` | `bool` | Run `RequirementsComparisonService` against the session. Requires `session_id`. |

> **Priority rule**: If any of `retrieval`, `fulltext`, or `use_comparison` is
> truthy, those flags **always win** — even if `agentic=True` is also passed.
> The LLM classifier is never called when intent is already explicit.

### Agentic routing flag

| kwarg | Type | Effect |
|---|---|---|
| `agentic` | `bool` | When `True` **and no explicit mode flag is set**, activates `AgentCallerWorkflow`. The LLM classifies intent and routes to the appropriate agent. |

---

## How the agent caller works

### Step 1 — Intent classification

When `agentic=True` and no explicit mode flag is set, `AgentCallerWorkflow`
fires an LLM call (using `AGENT_CLASSIFIER_SYSTEM/USER` prompts) that reads the
latest user message and conversation history and returns **exactly one** of
these labels:

| Label | Triggered when the user… |
|---|---|
| `retrieval` | Asks a factual question that requires searching documents |
| `summarization` | Asks for a summary, overview, or condensed version |
| `comparison` | Asks to compare, contrast, or analyse differences between items |
| `speech` | Asks for a written speech, script, or presentation |

If the classifier returns an unrecognised label (hallucination guard), it
falls back to `retrieval`.

### Step 2 — Parameter extraction (speech only)

For `speech`, a second LLM call (`SPEECH_PARAM_SYSTEM/USER`) extracts:
- **Topic** — the subject of the speech as a short phrase
- **Length** — mapped to one of `short | medium | long | very long`

If extraction fails, defaults are `topic = original message`, `length = medium`.

### Step 3 — Agent execution

Each label maps to a dedicated step:

| Agent | What it does |
|---|---|
| `retrieval` | Calls `RAGWorkflow(retrieval=True)`. Full retrieve → rerank → answer pipeline. |
| `summarization` | Calls `RAGWorkflow(fulltext=True)` to get document text, then runs a second summarization LLM pass. |
| `comparison` | Calls `RAGWorkflow(use_comparison=True)`. Delegates to `RequirementsComparisonService`. |
| `speech` | Direct LLM loop — no retrieval. See speech loop below. |

---

## Speech generation loop

The speech agent uses a multi-step loop to avoid context window exhaustion on
long speeches.

### Word count targets

| Length label | Target words | Approx. spoken duration |
|---|---|---|
| `short` | 150 | ~1 minute |
| `medium` | 450 | ~3 minutes |
| `long` | 750 | ~5 minutes |
| `very long` | 1,500 | ~10 minutes |

### How the loop works

```
speech_step  →  SpeechLoopState
                     │
              speech_checker_step ◀─────────────────────┐
                     │                                   │
                     ├─ words_so_far < target            │
                     │  AND iteration < max_iterations   │
                     │  AND words_remaining > threshold  │
                     │       → generate next body segment│
                     │       → new SpeechLoopState ──────┘
                     │
                     └─ target met OR close to target OR safety cap
                              → generate conclusion segment (if still short)
                              → emit AgentResult
```

Each LLM call generates **≤ 300 words** — well within any context window.
A safety cap of **8 iterations** prevents infinite loops regardless of LLM
behaviour.

### Speech metadata in the result

```python
result["agent"]             # "speech"
result["flags"]["topic"]    # extracted topic
result["flags"]["length"]   # "short" | "medium" | "long" | "very long"
result["flags"]["iterations"]  # how many LLM loop iterations ran
result["flags"]["words"]    # final word count
```

---

## Response shape

All paths return the same top-level dict shape:

```python
{
    "response": str,          # generated text
    "nodes": list,            # retrieved source nodes (empty for speech/comparison)
    "flags": {
        "mode": str,          # "retrieval" | "fulltext" | "chat_only" | agent name
        "use_session": bool,
        "use_global": bool,
        "use_tesi": bool,
        "retrieval": bool,
        "fulltext": bool,
        "comparison": bool,
        # speech-only extras:
        "topic": str,
        "length": str,
        "iterations": int,
        "words": int,
    },
    # agentic path only:
    "agent": str,             # "retrieval" | "summarization" | "comparison" | "speech"
    # comparison path only:
    "comparison": dict,       # structured comparison payload
}
```

> `"agent"` is only present when `agentic=True` was used. Existing code that
> does not check for it is unaffected.

---

## Routing decision matrix

| `agentic` | `retrieval` | `fulltext` | `use_comparison` | Path taken |
|---|---|---|---|---|
| `False` / absent | — | — | — | `RAGWorkflow` (existing router step decides retrieval vs chat) |
| `False` / absent | `True` | — | — | `RAGWorkflow(retrieval=True)` |
| `False` / absent | — | `True` | — | `RAGWorkflow(fulltext=True)` |
| `False` / absent | — | — | `True` | `RAGWorkflow(use_comparison=True)` |
| `True` | `True` | — | — | `RAGWorkflow(retrieval=True)` — **explicit flag wins** |
| `True` | — | `True` | — | `RAGWorkflow(fulltext=True)` — **explicit flag wins** |
| `True` | — | — | `True` | `RAGWorkflow(use_comparison=True)` — **explicit flag wins** |
| `True` | — | — | — | `AgentCallerWorkflow` → LLM classifier → agent |

---

## Environment variables

All variables are inherited from the original `llama-workflow.py` and apply
identically:

| Variable | Default | Description |
|---|---|---|
| `WF_TIMEOUT` | `500.0` | Workflow timeout in seconds (applies to both RAG and agentic workflows) |
| `REQ_COMP_TOP_K` | `8` | Top-k documents for comparison retrieval |
| `REQ_COMP_LLM_CONCURRENCY` | `4` | Max parallel LLM calls in comparison |
| `REQ_COMP_RETR_CONCURRENCY` | `8` | Max parallel retrieval calls in comparison |
| `REQ_COMP_MIN_BEST_SCORE` | `0.0` | Minimum score threshold for comparison evidence |
| `REQ_COMP_MIN_EVIDENCE_CHARS` | `10` | Minimum evidence length for comparison |
| `REQ_COMP_LLM_BATCH_SIZE` | `5` | LLM batch size for comparison |
| `REQ_COMP_MAX_EVIDENCE_CHARS` | `5000` | Max evidence chars per requirement |

---

## Tuning the speech loop

Three constants at the top of `agent_caller.py` control speech generation
behaviour. Edit them directly:

```python
_SPEECH_CHUNK_WORDS = 300   # words per LLM call — increase for fewer iterations
_SPEECH_MAX_ITERATIONS = 8  # hard safety cap — increase for very long speeches
_SPEECH_TARGET_WORDS = {    # target word counts per label
    "short": 150,
    "medium": 450,
    "long": 750,
    "very long": 1500,
}
```

For a 10-minute speech (`very long`, 1500 words) with the default chunk size of
300 words, the loop runs at most 5 body iterations + 1 conclusion = 6 LLM
calls, comfortably under the safety cap of 8.

---

## Adding a new agent

1. Add a new label string to `_VALID_AGENTS` in `agent_caller.py`
2. Add the label to `AGENT_CLASSIFIER_SYSTEM` in `agent_prompts.py` with
   routing rules
3. Add a new `Task` Event class
4. Add a `dispatch` branch in `AgentCallerWorkflow.dispatch()`
5. Add a `@step` method that handles the new task and returns `AgentResult`

No changes to `llama-workflow.py` are needed.

---

## Known limitations

- **Non-deterministic routing**: when `agentic=True` without explicit flags, the
  LLM classifier can occasionally misroute ambiguous queries. Mitigate by being
  specific in the user message, or by passing explicit flags when the UI knows
  the intent.
- **Summarization requires a loaded session**: `summarization_step` calls
  `RAGWorkflow(fulltext=True)`, which calls `retriever.retrieve_fulltext(session_id=...)`.
  If no documents are loaded into the session, the summary will be generated
  from an empty context.
- **Comparison requires `session_id`**: the comparison agent will raise a
  `ValueError` if `session_id` is not provided. Always pass `session_id` when
  the classifier might route to comparison.
- **Speech does not use the knowledge base**: the speech agent is a pure LLM
  call. It does not retrieve any documents. If domain-specific facts are needed
  in the speech, add a retrieval step before the speech loop.
