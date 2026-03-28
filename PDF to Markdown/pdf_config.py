"""
PDF Parser Configuration
========================
Shared configuration dataclasses used by all PDF parsing components.

Classes
-------
ParserConfig   All tuneable knobs for heuristic parsing, LLM calls, and
               auto-mode fallback thresholds.
ParseStats     Content-integrity report produced by HeuristicPDFParser
               after each parse() call.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParserConfig:
    """All tuneable knobs for the PDF parser and Markdown chunker."""

    # ── Header / footer detection ──────────────────────────────────────────
    header_zone: float = 0.07       # Top N% of page height treated as header zone
    footer_zone: float = 0.07       # Bottom N% of page height treated as footer zone
    hf_min_pages: int = 2           # Minimum pages text must appear on to count as HF
    hf_ratio: float = 0.35          # Minimum fraction of total pages for HF detection

    # ── Heading detection ──────────────────────────────────────────────────
    max_heading_levels: int = 6     # Maximum distinct heading levels to detect
    heading_size_ratio: float = 1.05  # Font must be ≥ body × ratio to be a heading
    heading_cluster_pt: float = 1.2 # Sizes within this pt range → same heading level
    size_tolerance: float = 0.4     # Floating-point tolerance for font size matching

    bold_heading: bool = True           # Treat all-bold single-line blocks as headings
    bold_heading_max_chars: int = 200   # Max characters for bold → heading rule
    min_toc_entries: int = 3            # Min TOC bookmarks to activate TOC-assisted mode

    # ── LLM (vLLM) ────────────────────────────────────────────────────────
    llm_base_url: str = "http://localhost:8000/v1"   # vLLM endpoint
    llm_model: str = "mistral-small-24b"             # vLLM model name
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_page_batch: int = 5         # PDF pages per LLM request
    llm_max_input_chars: int = 24_000  # max chars sent per LLM call (context-window guard, ~6K tokens)
    content_loss_warn_threshold: float = 0.15   # warn if output retains < (1-threshold) of body chars

    # ── Table & footnote handling ─────────────────────────────────────────
    merge_tables: bool = True             # Merge continuation tables across pages
    inline_footnotes: bool = True         # Detect footnote defs and inline references
    superscript_size_ratio: float = 0.85  # Chars with size < body*ratio = superscript

    # ── Auto-mode LLM fallback thresholds ─────────────────────────────────
    auto_min_retention_pct: float = 85.0   # trigger if content_retention_pct < this
    auto_max_empty_drop_ratio: float = 0.30  # trigger if empty_drops / total_blocks > this
    auto_min_chars_per_page: float = 100.0  # trigger if avg output chars/page < this
    auto_min_heading_density: float = 0.005  # trigger if heading_lines / total_lines < this (0 headings)
    auto_max_heading_density: float = 0.20   # trigger if heading_lines / total_lines > this (too many)


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT-INTEGRITY REPORT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParseStats:
    """
    Content-integrity report stored on the parser after each parse() call.
    Access via  parser.last_parse_stats  to audit for information loss.

    Fields
    ------
    total_blocks          All text/table blocks extracted from the PDF.
    empty_blocks_dropped  Paragraph groups silently skipped because pdfplumber
                          could not extract any characters (e.g. image-over-text,
                          scanned pages, or pure whitespace groups).
    hf_blocks_dropped     Blocks removed by the header/footer repetition detector.
                          ** This is the highest-risk deletion path. **
                          Inspect hf_dropped_samples when retention is low.
    chars_body_input      Total characters across all non-HF body blocks
                          before Markdown assembly (the "expected" content).
    chars_output          Non-whitespace characters in the final Markdown
                          (heading markers and structural characters included).
    content_retention_pct (chars_output / chars_body_input) × 100.
                          Values close to 100% are expected.
                          A value well below (1 − content_loss_warn_threshold)×100
                          triggers a printed warning.
    hf_dropped_samples    First 10 blocks deleted as header/footer, as
                          (1-based page number, text snippet) tuples.
                          Review these when retention is unexpectedly low.
    warnings              Non-empty list of warning strings when retention
                          falls below the configured threshold.
    """
    total_blocks:          int   = 0
    empty_blocks_dropped:  int   = 0
    hf_blocks_dropped:     int   = 0
    chars_body_input:      int   = 0
    chars_output:          int   = 0
    content_retention_pct: float = 0.0
    hf_dropped_samples:    list        = field(default_factory=list)
    warnings:              list        = field(default_factory=list)
    detected_title:        str | None  = None
