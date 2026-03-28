"""
LLM PDF Parser
==============
LLM-based PDF structure parser using a vLLM backend (OpenAI-compatible endpoint).

Two modes
---------
parse()   – Send raw pdfplumber page text to LLM; receive structured Markdown.
refine()  – Post-process heuristic Markdown to fix structural issues (cheaper).

Reliability features
--------------------
- Dynamic page-batch sizing: shrinks batch if raw text exceeds llm_max_input_chars.
- Heading-aware splitting in refine(): batches cut only at heading boundaries.
- _check_page_markers(): warns when LLM drops <!--page:N--> annotations.
- _check_retention(): warns when LLM drops > content_loss_warn_threshold of words.
- seed=0 on every call for deterministic, reproducible output.
"""

from __future__ import annotations

import re

from pdf_config import ParserConfig

try:
    import pdfplumber  # type: ignore[import]  # pip install pdfplumber
except ImportError as exc:  # pragma: no cover
    raise ImportError("pdfplumber is required: pip install pdfplumber") from exc


class LLMPDFParser:
    """LLM-based PDF structure parser (vLLM backend)."""

    _PARSE_SYSTEM = (
        "You are an expert document structure analyst.\n"
        "Convert raw PDF page text into well-structured Markdown.\n\n"
        "Rules:\n"
        "- Identify the document title as # (H1)\n"
        "- Infer section headings from content and context as ## ### #### etc.\n"
        "- Keep all body text exactly as-is in paragraphs\n"
        "- Remove repeated page headers/footers (repeated document titles, page numbers)\n"
        "- PRESERVE every <!--page:N--> marker exactly as-is on its own line\n"
        "- Output ONLY the final Markdown — no commentary, no code fences."
    )

    _PARSE_USER = (
        "Convert the following PDF page text to Markdown.\n"
        "Preserve every word exactly; only add heading markers (#, ##, ###…).\n\n"
        "{text}"
    )

    _REFINE_SYSTEM = "You are a Markdown structure expert."

    _REFINE_USER = (
        "Fix the heading hierarchy in the Markdown below:\n"
        "1. Ensure logical nesting: H1 > H2 > H3 (no skipped levels)\n"
        "2. Demote incorrectly tagged headings (bold labels, captions, list items)\n"
        "3. Remove any residual repeated headers/footers or page numbers\n"
        "4. Preserve ALL body text exactly, do not paraphrase\n"
        "5. PRESERVE every <!--page:N--> marker exactly as-is on its own line\n"
        "6. Output only the corrected Markdown.\n\n"
        "{markdown}"
    )

    def __init__(self, cfg: ParserConfig | None = None):
        self.cfg = cfg or ParserConfig()
        self._client = None

    @property
    def _oai(self):
        if self._client is None:
            from openai import OpenAI
            # vLLM uses "EMPTY" as the api_key placeholder
            self._client = OpenAI(base_url=self.cfg.llm_base_url, api_key="EMPTY")
        return self._client

    def _model_name(self) -> str:
        """Return the model name to use in API calls."""
        return self.cfg.llm_model

    def _call(self, system: str, user: str) -> str:
        resp = self._oai.chat.completions.create(
            model=self._model_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=self.cfg.llm_max_tokens,
            temperature=self.cfg.llm_temperature,
            seed=0,   # deterministic sampling — reduces hallucination variance
        )
        return resp.choices[0].message.content or ""

    # ── Hallucination guard helpers ──────────────────────────────────────────

    def _check_page_markers(self, input_text: str, output_text: str, label: str) -> None:
        """Warn if the LLM dropped any <!--page:N--> markers from the output."""
        in_markers  = set(re.findall(r"<!--page:\d+-->", input_text))
        out_markers = set(re.findall(r"<!--page:\d+-->", output_text))
        missing = in_markers - out_markers
        if missing:
            print(
                f"  WARNING [{label}]: {len(missing)} page marker(s) missing from LLM output: "
                + ", ".join(sorted(missing))
            )

    def _check_retention(self, input_text: str, output_text: str, label: str) -> None:
        """Warn if the LLM dropped a significant fraction of input words.

        Page markers are stripped from both sides before comparing so that
        intentionally removed markers don't skew the word-count ratio.
        """
        strip = re.compile(r"<!--page:\d+-->")
        in_words  = len(strip.sub("", input_text).split())
        out_words = len(strip.sub("", output_text).split())
        if in_words == 0:
            return
        retention = out_words / in_words
        threshold = 1.0 - self.cfg.content_loss_warn_threshold
        if retention < threshold:
            print(
                f"  WARNING [{label}]: Word retention {retention:.0%} is below "
                f"{threshold:.0%} ({in_words} words in → {out_words} out). "
                "The LLM may have hallucinated or dropped content."
            )

    @staticmethod
    def _split_at_headings(markdown: str, max_chars: int) -> list[str]:
        """Split Markdown into batches at heading boundaries ≤ max_chars each.

        Splitting at headings keeps each batch contextually coherent so that
        the LLM does not receive fragments that cut mid-section.  If a single
        section alone exceeds max_chars it is emitted as its own oversized
        batch rather than being split mid-paragraph.
        """
        batches: list[str] = []
        buf = ""
        for line in markdown.splitlines(keepends=True):
            is_heading = bool(re.match(r"^#{1,6}\s", line))
            if is_heading and buf and len(buf) + len(line) > max_chars:
                batches.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            batches.append(buf)
        return batches or [markdown]

    # ── Public API ───────────────────────────────────────────────────────────

    def parse(self, pdf_path: str) -> str:
        """Full LLM-based PDF parsing."""
        with pdfplumber.open(pdf_path) as doc:
            pages = [page.extract_text() or "" for page in doc.pages]

        parts: list[str] = []
        i = 0
        while i < len(pages):
            # Dynamically shrink the batch if the raw text would breach the
            # configured input-character limit (context-window guard).
            bs = self.cfg.llm_page_batch
            while bs > 1:
                batch = pages[i: i + bs]
                annotated = "\n".join(
                    f"<!--page:{i + j + 1}-->\n{t}" for j, t in enumerate(batch)
                )
                if len(annotated) <= self.cfg.llm_max_input_chars:
                    break
                bs -= 1
            else:
                annotated = f"<!--page:{i + 1}-->\n{pages[i]}"
                batch = pages[i: i + 1]

            end_pg = i + len(batch)
            print(
                f"  LLM parsing pages {i + 1}–{end_pg} / {len(pages)} "
                f"({len(annotated):,} chars, batch={len(batch)}) …"
            )
            result = self._call(self._PARSE_SYSTEM, self._PARSE_USER.format(text=annotated))

            # Hallucination guards — verify markers and word-count retention
            self._check_page_markers(annotated, result, f"pages {i + 1}–{end_pg}")
            self._check_retention(annotated, result, f"pages {i + 1}–{end_pg}")

            parts.append(result)
            i = end_pg

        return "\n\n".join(parts)

    def refine(self, markdown: str) -> str:
        """Post-process heuristic Markdown with LLM to fix structural anomalies."""
        max_chars = self.cfg.llm_max_input_chars
        if len(markdown) <= max_chars:
            result = self._call(self._REFINE_SYSTEM, self._REFINE_USER.format(markdown=markdown))
            self._check_retention(markdown, result, "refine")
            return result

        # Split at heading boundaries so each batch is contextually coherent.
        print(f"  Document too large ({len(markdown)} chars); refining in sections …")
        batches = self._split_at_headings(markdown, max_chars)

        refined_parts = []
        for i, batch_md in enumerate(batches):
            print(f"  Refining section {i + 1}/{len(batches)} ({len(batch_md):,} chars) …")
            result = self._call(self._REFINE_SYSTEM, self._REFINE_USER.format(markdown=batch_md))
            self._check_retention(batch_md, result, f"refine section {i + 1}")
            refined_parts.append(result)

        return "\n\n".join(refined_parts)
