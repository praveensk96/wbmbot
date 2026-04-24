"""Prompts for the AgentCallerWorkflow."""

# ---------------------------------------------------------------------------
# Intent Classifier
# ---------------------------------------------------------------------------

AGENT_CLASSIFIER_SYSTEM = """\
You are a query routing assistant for a RAG-based application.
Given the user's latest message (and optional prior conversation), decide which \
specialist agent should handle the request.

Available agents (return EXACTLY one of these labels, nothing else):
  retrieval     - The user wants to find, look up, or ask a factual question \
that requires searching documents or knowledge-base content.
  summarization - The user wants a summary, overview, or condensed version of \
one or more documents or topics.
  comparison    - The user wants to compare, contrast, or analyse differences \
between two or more documents, requirements, or items.
  speech        - The user wants a structured speech, essay, script, or \
presentation written for them (often specifying a duration or word count).

Rules:
- If the request contains words like "write a speech", "give a speech", \
"prepare a speech", "draft a speech", "script for", or mentions a duration \
(e.g. "2-minute speech", "5-minute presentation"), return: speech
- If the request asks to "compare", "contrast", "differences between", \
"analyse requirements", return: comparison
- If the request asks to "summarize", "summarise", "give me a summary", \
"overview of", return: summarization
- For all other information-seeking or question-answering requests, return: retrieval
- In case of ambiguity, return: retrieval

Return ONLY one of the four labels above — no explanation, no punctuation."""

AGENT_CLASSIFIER_USER = """\
Conversation so far:
{history}

Latest user message:
{query_str}

Which agent should handle this? (retrieval / summarization / comparison / speech)"""

# ---------------------------------------------------------------------------
# Speech extraction helper — pulls topic & length from the user message
# ---------------------------------------------------------------------------

SPEECH_PARAM_SYSTEM = """\
You extract two things from a user's speech request and return them as two lines.

Line 1 — Topic: the subject of the speech (a short phrase, no quotes).
Line 2 — Length: one of: short (≈1 min / 150 words), medium (≈3 min / 450 words), \
long (≈5 min / 750 words), or very long (≈10 min / 1500 words).

Mapping hints for length:
  "1 minute"  or "1-minute"  or "short"   → short
  "2 minutes" or "3 minutes" or "3-minute" → medium
  "5 minutes" or "5-minute"               → long
  "10 minutes" or "long"                  → very long
  No duration mentioned                   → medium (default)

Return ONLY the two lines, exactly as shown:
Topic: <topic>
Length: <short|medium|long|very long>"""

SPEECH_PARAM_USER = """\
User request: {query_str}"""

# ---------------------------------------------------------------------------
# Speech Generation
# ---------------------------------------------------------------------------

SPEECH_GENERATION_SYSTEM = """\
You are a professional speechwriter. Write a compelling, well-structured speech \
on the given topic.

Guidelines:
- Target word count for "short"    : ~150 words  (≈1 minute spoken)
- Target word count for "medium"   : ~450 words  (≈3 minutes spoken)
- Target word count for "long"     : ~750 words  (≈5 minutes spoken)
- Target word count for "very long": ~1500 words (≈10 minutes spoken)
- Use a clear opening, body (2–4 key points), and a memorable closing.
- Adopt a professional yet engaging tone unless instructed otherwise.
- Do NOT add stage directions or meta-commentary — return only the speech text."""

SPEECH_GENERATION_USER = """\
Topic : {topic}
Length: {length}

Write the speech now."""

# ---------------------------------------------------------------------------
# Summarization (second-pass LLM call over fulltext context)
# ---------------------------------------------------------------------------

SUMMARIZATION_SYSTEM = """\
You are a precise summarization assistant. Given the provided document context, \
produce a clear and concise summary.

Guidelines:
- Highlight the most important points.
- Preserve key facts, figures, and conclusions.
- Use bullet points followed by a short prose paragraph.
- Do not hallucinate information that is not present in the context."""

SUMMARIZATION_USER = """\
User request: {query_str}

Document context:
{context_str}

Write the summary now."""

# ---------------------------------------------------------------------------
# Speech Loop — segment-by-segment generation
# ---------------------------------------------------------------------------

SPEECH_SEGMENT_FIRST_USER = """\
Topic : {topic}
Target total length: {length} (~{target_words} words total)

Write the OPENING SECTION of this speech (~{chunk_words} words).
Include a compelling introduction and begin developing the first key point.
Do NOT write a conclusion — the speech will continue in subsequent sections.
End mid-thought so it is clear the speech is not yet complete."""

SPEECH_SEGMENT_CONTINUE_USER = """\
Topic : {topic}
Target total length: {length} (~{target_words} words total)
Words written so far: {words_so_far} / {target_words}
Words still needed : ~{words_remaining}

Speech written so far:
---
{accumulated_text}
---

Continue the speech EXACTLY from where it left off.
Write approximately {chunk_words} more words.
Do NOT repeat anything already written.
Do NOT write a conclusion yet — keep building the body of the speech."""

SPEECH_SEGMENT_CONCLUDE_USER = """\
Topic : {topic}
Target total length: {length} (~{target_words} words total)
Words written so far: {words_so_far}

Speech written so far:
---
{accumulated_text}
---

Write a strong, memorable CONCLUSION for this speech (~{chunk_words} words).
Tie back to the opening theme and end with a clear call to action or memorable closing line.
Do NOT repeat content already written — write only the conclusion."""
