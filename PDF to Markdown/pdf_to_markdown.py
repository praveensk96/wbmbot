"""
PDF to Markdown Converter
=========================

Three parsing strategies:
  1. heuristic        - Font-size + layout analysis via pdfplumber (fast, no GPU)
  2. llm              - Full LLM parse via Mistral Small24B / vLLM
  3. heuristic+llm    - Heuristic first, then LLM structural refinement
  4. auto             - Heuristic first; falls back to LLM if quality triggers fire

This module is the public entry point.  Implementation is split across:

  pdf_config.py            ParserConfig, ParseStats
  pdf_heuristic_parser.py  HeuristicPDFParser
  pdf_llm_parser.py        LLMPDFParser

All names are re-exported here so existing imports continue to work:

    from pdf_to_markdown import PDFToMarkdown, PDFTextExtractor
    from pdf_to_markdown import ParserConfig
    from pdf_to_markdown import HeuristicPDFParser, LLMPDFParser

Requirements:
  pip install pdfplumber pypdf openai
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List

from llama_index.core.readers.base import BaseReader
from llama_index.core.schema import Document

# Sub-module imports - re-exported for backward compatibility
from pdf_config import ParserConfig, ParseStats  # noqa: F401
from pdf_heuristic_parser import HeuristicPDFParser  # noqa: F401
from pdf_llm_parser import LLMPDFParser  # noqa: F401

# Chunker re-export (convenience)
from markdown_chunker import Chunk, MarkdownChunker  # noqa: F401

try:
    import pdfplumber  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise ImportError("pdfplumber is required: pip install pdfplumber") from exc


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------

class PDFToMarkdown:
    """
    Convert a PDF file to Markdown using the best available strategy.

    Strategies
    ----------
    "heuristic"       Font-size + layout heuristics (fast, no GPU required)
    "llm"             Full LLM parse via Mistral Small24B / vLLM
    "heuristic+llm"   Heuristic first, then LLM structural refinement pass
    "auto"            Heuristic first; falls back to LLM when quality triggers fire

    Usage
    -----
    converter = PDFToMarkdown()
    md = converter.convert("report.pdf", output_path="report.md")

    # With LLM refinement
    cfg = ParserConfig(llm_base_url="http://gpu-server:8000/v1")
    converter = PDFToMarkdown(cfg)
    md = converter.convert("report.pdf", strategy="heuristic+llm")
    """

    def __init__(self, cfg: ParserConfig | None = None):
        self.cfg = cfg or ParserConfig()
        self._heuristic = HeuristicPDFParser(self.cfg)
        self._llm_parser: LLMPDFParser | None = None

    @property
    def _llm(self) -> LLMPDFParser:
        if self._llm_parser is None:
            self._llm_parser = LLMPDFParser(self.cfg)
        return self._llm_parser

    def _llm_is_configured(self) -> bool:
        """Return True when a vLLM backend is configured for the auto fallback."""
        return bool(self.cfg.llm_base_url)

    def _evaluate_heuristic_quality(
        self, md: str, pdf_path: str
    ) -> tuple[bool, bool, list[str]]:
        """Evaluate heuristic output quality and decide whether to call the LLM.

        Returns
        -------
        needs_fallback   : True if any quality trigger fired.
        needs_full_parse : True when text extraction itself was poor -- full
                           LLMPDFParser.parse() needed instead of refine().
        triggers         : Human-readable descriptions of what fired.
        """
        stats = self._heuristic.last_parse_stats
        triggers: list[str] = []

        # Trigger 1: low content retention
        if (
            stats is not None
            and stats.content_retention_pct < self.cfg.auto_min_retention_pct
        ):
            triggers.append(
                f"content retention {stats.content_retention_pct:.1f}% "
                f"< threshold {self.cfg.auto_min_retention_pct:.0f}%"
            )

        # Trigger 2: high empty-block drop rate (scanned / image PDF)
        if stats is not None and stats.total_blocks > 0:
            drop_ratio = stats.empty_blocks_dropped / stats.total_blocks
            if drop_ratio > self.cfg.auto_max_empty_drop_ratio:
                triggers.append(
                    f"empty block ratio {drop_ratio:.0%} "
                    f"> threshold {self.cfg.auto_max_empty_drop_ratio:.0%}"
                )

        # Trigger 3: very little text extracted per page
        try:
            with pdfplumber.open(pdf_path) as doc:
                n_pages = len(doc.pages)
        except Exception:
            n_pages = 1
        chars_per_page = len(md) / n_pages if n_pages > 0 else 0
        if chars_per_page < self.cfg.auto_min_chars_per_page:
            triggers.append(
                f"avg {chars_per_page:.0f} chars/page "
                f"< threshold {self.cfg.auto_min_chars_per_page:.0f}"
            )

        # Triggers 4 & 5: heading density out of expected band
        non_empty_lines = [ln for ln in md.splitlines() if ln.strip()]
        total_lines = len(non_empty_lines) or 1
        heading_lines = sum(1 for ln in non_empty_lines if re.match(r"^#{1,6}\s", ln))
        density = heading_lines / total_lines

        if density < self.cfg.auto_min_heading_density:
            triggers.append(
                f"heading density {density:.1%} "
                f"< min threshold {self.cfg.auto_min_heading_density:.1%} (no structure detected)"
            )
        elif density > self.cfg.auto_max_heading_density:
            triggers.append(
                f"heading density {density:.1%} "
                f"> max threshold {self.cfg.auto_max_heading_density:.1%} (false-positive headings)"
            )

        needs_fallback = bool(triggers)

        # Decide between refine() vs full parse():
        # If text extraction itself is poor (scanned PDF signals), a full
        # LLM parse from raw pdfplumber text is needed.  Otherwise the
        # heuristic text is usable and refine() is cheaper.
        extraction_poor = chars_per_page < self.cfg.auto_min_chars_per_page or (
            stats is not None
            and stats.total_blocks > 0
            and stats.empty_blocks_dropped / stats.total_blocks
            > self.cfg.auto_max_empty_drop_ratio
        )
        needs_full_parse = needs_fallback and extraction_poor

        return needs_fallback, needs_full_parse, triggers

    def convert(
        self,
        pdf_path: str,
        output_path: str | None = None,
        strategy: str = "heuristic",
        doc_metadata: dict | None = None,
    ) -> str:
        """
        Convert PDF to Markdown.

        Parameters
        ----------
        pdf_path     : Path to the input PDF file.
        output_path  : Optional path to write the .md output.
        strategy     : One of "heuristic", "llm", "heuristic+llm", "auto".
        doc_metadata : Optional dict forwarded to every Chunk as metadata and
                       embedded as a JSON comment at the top of the output file.

        Returns
        -------
        Markdown string.
        """
        print(f"\n[PDF->MD] Input    : {pdf_path}")
        print(f"[PDF->MD] Strategy : {strategy}")

        if strategy == "llm":
            md = self._llm.parse(pdf_path)
        elif strategy == "heuristic+llm":
            print("[PDF->MD] Step 1/2 : Heuristic parsing ...")
            md = self._heuristic.parse(pdf_path)
            print("[PDF->MD] Step 2/2 : LLM refinement pass ...")
            md = self._llm.refine(md)
        elif strategy == "auto":
            md = self._heuristic.parse(pdf_path)
            needs_fallback, needs_full_parse, triggers = self._evaluate_heuristic_quality(
                md, pdf_path
            )
            if needs_fallback:
                if not self._llm_is_configured():
                    print(
                        "[PDF->MD] Auto     : Heuristic quality triggers fired but no LLM "
                        "is configured -- keeping heuristic output.\n"
                        "         Triggers : " + "; ".join(triggers)
                    )
                elif needs_full_parse:
                    print(
                        "[PDF->MD] Auto     : Poor text extraction detected -- falling back "
                        "to full LLM parse.\n"
                        "         Triggers : " + "; ".join(triggers)
                    )
                    md = self._llm.parse(pdf_path)
                else:
                    print(
                        "[PDF->MD] Auto     : Structural quality issues detected -- falling "
                        "back to LLM refinement.\n"
                        "         Triggers : " + "; ".join(triggers)
                    )
                    md = self._llm.refine(md)
            else:
                print("[PDF->MD] Auto     : Heuristic output passed quality checks -- no LLM fallback needed.")
        else:
            md = self._heuristic.parse(pdf_path)

        if doc_metadata:
            meta_comment = (
                "<!--metadata:\n"
                + json.dumps(doc_metadata, indent=2, ensure_ascii=False)
                + "\n-->\n"
            )
            md = meta_comment + md

        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            print(f"[PDF->MD] Saved -> {out}")

        return md


# ---------------------------------------------------------------------------
# LLAMA-INDEX READER WRAPPER
# ---------------------------------------------------------------------------

class PDFTextExtractor(BaseReader):
    """LlamaIndex BaseReader for PDF -> Markdown conversion.

    Wraps PDFToMarkdown so it can be passed directly as a file_extractor
    to SimpleDirectoryReader.  The returned Document contains Markdown text
    with <!--page:N--> markers that the unified chunker
    (_chunk_markdown_documents) will convert into page-level metadata on
    each node.
    """

    def __init__(
        self,
        cfg: ParserConfig | None = None,
        strategy: str = "heuristic",
    ) -> None:
        self._converter = PDFToMarkdown(cfg)
        self._strategy = strategy

    def load_data(self, file: Path, **kwargs: Any) -> List[Document]:
        extra_info = kwargs.get("extra_info", {})
        md = self._converter.convert(
            str(file),
            strategy=self._strategy,
            doc_metadata=extra_info,
        )
        if not md:
            return []
        meta = dict(extra_info)
        return [Document(text=md, metadata=meta)]

