"""
Markdown Chunker
================
Standalone chunker that splits any Markdown document into context-aware,
RAG-ready chunks.

Key features
------------
- Heading-breadcrumb path prepended to every chunk's full_text
- Tables: header row repeated in every continuation chunk; rows never split
- Paragraph → sentence → character fallback splitting for normal text
- Page-marker support (<!--page:N-->) — stripped from content, stored as metadata
- Optional document-level metadata dict forwarded to every Chunk

Usage
-----
    from markdown_chunker import MarkdownChunker

    chunker = MarkdownChunker(
        max_chunk_size=2000,
        doc_title="Annual Report 2024",
        doc_metadata={"source": "report.pdf", "author": "EY"},
    )
    chunks  = chunker.chunk(markdown_text)          # → list[Chunk]
    records = chunker.to_dicts(markdown_text)       # → list[dict]
    chunker.save_json(markdown_text, "chunks.json")
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# CHUNK DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """One chunk of a Markdown document with heading breadcrumb context."""
    chunk_id:       int
    heading_path:   str                      # "Intro > Background > Related Work"
    heading_levels: list[tuple[int, str]]    # [(1,"Intro"), (2,"Background"), …]
    content:        str                      # Raw chunk text (no path prefix, no page markers)
    full_text:      str                      # heading_path + "\n\n" + content  ← for embedding
    char_count:     int
    page_start:     int  = 0                 # first PDF page (1-based) in this chunk
    page_end:       int  = 0                 # last  PDF page (1-based) in this chunk
    pages:          list = field(default_factory=list)   # all page numbers covered
    metadata:       dict = field(default_factory=dict)   # document-level metadata (author, source, …)


# ─────────────────────────────────────────────────────────────────────────────
# MARKDOWN CHUNKER
# ─────────────────────────────────────────────────────────────────────────────

class MarkdownChunker:
    """
    Split a Markdown document into context-aware chunks ≤ max_chunk_size chars.

    Algorithm
    ---------
    1. Scan the Markdown line-by-line.
    2. When a heading is encountered, flush accumulated content as chunk(s)
       and update the internal heading stack.
    3. After the heading is recorded, accumulate body text of the new section.
    4. When flushing, if content exceeds the limit:
         a. Try splitting on paragraph breaks (double newlines).
         b. If a single paragraph is still too large, split on sentence boundaries.
         c. If a single sentence is still too large, hard-split at char limit.
    5. Tables receive special treatment:
         - Column header row + separator row repeated at top of every continuation chunk.
         - Individual rows are never split; the limit is exceeded rather than break a row.
    6. Every emitted chunk carries:
         • heading_path   – breadcrumb string, also prepended to full_text
         • heading_levels – [(level, title), …] for programmatic access
         • page_start / page_end / pages – PDF page provenance
         • metadata       – document-level dict (title, author, source, …)

    full_text format  (what goes into a vector store / RAG system):
    ───────────────────────────────────────────────────────────────
    Introduction > Methodology > Data Collection

    This section describes how data was collected…

    Usage
    -----
    chunker = MarkdownChunker(max_chunk_size=2000, doc_title="My Doc",
                              doc_metadata={"source": "file.pdf"})
    chunks  = chunker.chunk(markdown_text)          # → list[Chunk]
    records = chunker.to_dicts(markdown_text)       # → list[dict]  (JSON-ready)
    chunker.save_json(markdown_text, "chunks.json") # → writes JSON file
    """

    _HEADING_RE     = re.compile(r"^(#{1,6})\s+(.+)$")
    _PAGE_RE        = re.compile(r"^<!--page:(\d+)-->$")
    _PAGE_STRIP     = re.compile(r"<!--page:\d+-->\n?")   # strip markers from stored content
    # Matches a consecutive block of pipe-table lines (2 or more)
    _TABLE_BLOCK_RE = re.compile(r"((?:^\|[^\n]*(?:\n|$)){2,})", re.MULTILINE)
    # Matches a Markdown table separator row like | --- | :---: | ---: |
    _TABLE_SEP_RE   = re.compile(r"^\|[\s\-:|]+\|$")

    def __init__(
        self,
        max_chunk_size: int = 2000,
        path_separator: str = " > ",
        doc_title: str = "",        # document-level prefix for every heading path
        doc_metadata: dict | None = None,  # forwarded verbatim to every Chunk.metadata
    ):
        self.max_size     = max_chunk_size
        self.sep          = path_separator
        self.doc_title    = doc_title.strip()
        self.doc_metadata: dict = dict(doc_metadata or {})

    # ── Heading stack management ─────────────────────────────────────────────

    @staticmethod
    def _update_stack(
        stack: list[tuple[int, str]], level: int, title: str
    ) -> list[tuple[int, str]]:
        """Remove same/deeper headings then append the new one."""
        return [(l, t) for l, t in stack if l < level] + [(level, title)]

    def _build_path(self, stack: list[tuple[int, str]]) -> str:
        parts = [t for _, t in stack]
        if self.doc_title:
            parts = [self.doc_title] + parts
        return self.sep.join(parts) if parts else "(Preamble)"

    # ── Text splitting ───────────────────────────────────────────────────────

    def _split_paragraphs(self, text: str, limit: int) -> list[str]:
        """Split on paragraph breaks → sentence breaks → character hard-split."""
        if len(text) <= limit:
            return [text]

        results: list[str] = []
        buf = ""

        for para in re.split(r"\n\n+", text):
            candidate = (buf + "\n\n" + para).lstrip() if buf else para
            if len(candidate) <= limit:
                buf = candidate
            elif len(para) > limit:
                if buf:
                    results.append(buf.strip())
                    buf = ""
                results.extend(self._split_sentences(para, limit))
            else:
                if buf:
                    results.append(buf.strip())
                buf = para

        if buf.strip():
            results.append(buf.strip())

        return [r for r in results if r.strip()]

    def _split_content(self, text: str, limit: int) -> list[str]:
        """
        Split *text* respecting table integrity.

        Tables: header row repeated per continuation chunk; rows never split.
        Other content: paragraph → sentence → character fallback.
        """
        if len(text) <= limit:
            return [text]

        # Fast pre-check before running the regex
        if "\n|" in text or text.lstrip().startswith("|"):
            segments = self._TABLE_BLOCK_RE.split(text)
            if len(segments) > 1:
                results: list[str] = []
                for seg in segments:
                    seg = seg.strip()
                    if not seg:
                        continue
                    lines = seg.splitlines()
                    is_table = (
                        len(lines) >= 3
                        and lines[0].strip().startswith("|")
                        and bool(self._TABLE_SEP_RE.match(lines[1].replace(" ", "")))
                    )
                    if is_table:
                        results.extend(self._split_table_rows(seg, limit))
                    elif len(seg) <= limit:
                        results.append(seg)
                    else:
                        results.extend(self._split_paragraphs(seg, limit))
                return [r for r in results if r.strip()]

        return self._split_paragraphs(text, limit)

    @staticmethod
    def _split_table_rows(table_text: str, limit: int) -> list[str]:
        """
        Split a Markdown table into chunks, repeating the column header in each.

        Rules
        -----
        - The header row and separator row are prepended to every continuation chunk.
        - Individual data rows are never split; a row that alone exceeds *limit* is
          emitted as its own chunk (limit disregarded — never break mid-row).
        - If the table has no data rows, returned as-is.
        """
        lines = table_text.strip().splitlines()
        if len(lines) < 3:
            return [table_text.strip()]

        header_block = lines[0] + "\n" + lines[1]
        data_rows    = lines[2:]
        header_len   = len(header_block)

        result:    list[str] = []
        buf_rows:  list[str] = []
        buf_size = header_len

        for row in data_rows:
            row_cost = 1 + len(row)   # '\n' separator + row text
            if buf_rows and buf_size + row_cost > limit:
                result.append(header_block + "\n" + "\n".join(buf_rows))
                buf_rows = []
                buf_size = header_len
            buf_rows.append(row)
            buf_size += row_cost

        if buf_rows:
            result.append(header_block + "\n" + "\n".join(buf_rows))

        return result or [table_text.strip()]

    @staticmethod
    def _split_sentences(text: str, limit: int) -> list[str]:
        """Split on sentence boundaries; hard-split if a sentence is too long."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        results: list[str] = []
        buf = ""

        for sent in sentences:
            candidate = (buf + " " + sent).strip() if buf else sent
            if len(candidate) <= limit:
                buf = candidate
            elif len(sent) > limit:
                if buf:
                    results.append(buf)
                    buf = ""
                for i in range(0, len(sent), limit):
                    results.append(sent[i: i + limit])
            else:
                if buf:
                    results.append(buf)
                buf = sent

        if buf:
            results.append(buf)
        return results

    # ── Chunk emission ───────────────────────────────────────────────────────

    def _emit(
        self,
        content_lines: list[str],
        stack:         list[tuple[int, str]],
        chunks:        list[Chunk],
        chunk_id_ref:  list[int],
        buf_pages:     set | None = None,
    ) -> None:
        """Flush accumulated lines as one or more Chunk objects."""
        content_raw = "\n".join(content_lines).strip()
        if not content_raw:
            return

        # Strip page markers from stored content and full_text
        content = self._PAGE_STRIP.sub("", content_raw).strip()
        if not content:
            return

        page_list  = sorted(buf_pages or set())
        page_start = page_list[0]  if page_list else 0
        page_end   = page_list[-1] if page_list else 0

        path = self._build_path(stack)
        # Reserve path overhead so full_text length stays within max_size
        path_overhead  = len(path) + 2   # +2 for the "\n\n" separator
        content_limit  = max(100, self.max_size - path_overhead)

        for part in self._split_content(content, content_limit):
            part = part.strip()
            if not part:
                continue
            full_text = f"{path}\n\n{part}"
            chunks.append(Chunk(
                chunk_id      = chunk_id_ref[0],
                heading_path  = path,
                heading_levels= list(stack),
                content       = part,
                full_text     = full_text,
                char_count    = len(full_text),
                page_start    = page_start,
                page_end      = page_end,
                pages         = page_list,
                metadata      = dict(self.doc_metadata),   # shallow copy per chunk
            ))
            chunk_id_ref[0] += 1

    # ── Public API ───────────────────────────────────────────────────────────

    def chunk(self, markdown: str) -> list[Chunk]:
        """
        Split *markdown* into Chunk objects.

        Returns
        -------
        list[Chunk] — ordered list with heading path, page metadata, and
        document metadata on every chunk.
        """
        chunks:        list[Chunk]       = []
        chunk_id_ref:  list[int]         = [0]
        stack:         list[tuple[int, str]] = []
        buf:           list[str]         = []
        buf_pages:     set[int]          = set()
        current_page:  int               = 0

        for line in markdown.splitlines():
            # Page marker — update tracker only, do NOT add to content buffer
            pm = self._PAGE_RE.match(line)
            if pm:
                current_page = int(pm.group(1))
                continue

            m = self._HEADING_RE.match(line)
            if m:
                # Flush content accumulated under the previous heading
                self._emit(buf, stack, chunks, chunk_id_ref, buf_pages)
                buf = []
                # Seed the new section's page set with the heading's own page
                buf_pages = {current_page} if current_page else set()
                level = len(m.group(1))
                title = m.group(2).strip()
                stack = self._update_stack(stack, level, title)
            else:
                buf.append(line)
                if current_page:
                    buf_pages.add(current_page)

        # Flush final section
        self._emit(buf, stack, chunks, chunk_id_ref, buf_pages)
        return chunks

    def to_dicts(self, markdown: str) -> list[dict]:
        """Return chunks as a list of JSON-serialisable dictionaries."""
        return [
            {
                "chunk_id":      c.chunk_id,
                "heading_path":  c.heading_path,
                "heading_levels":c.heading_levels,
                "page_start":    c.page_start,
                "page_end":      c.page_end,
                "pages":         c.pages,
                "metadata":      c.metadata,
                "content":       c.content,
                "full_text":     c.full_text,
                "char_count":    c.char_count,
            }
            for c in self.chunk(markdown)
        ]

    def save_json(self, markdown: str, output_path: str) -> list[dict]:
        """Chunk *markdown* and write results to a JSON file."""
        records = self.to_dicts(markdown)
        Path(output_path).write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[Chunker] Saved {len(records)} chunks → {output_path}")
        return records

    def summary(self, markdown: str) -> dict:
        """Return statistics about the chunks without storing them."""
        chunks = self.chunk(markdown)
        if not chunks:
            return {"total_chunks": 0}
        sizes = [c.char_count for c in chunks]
        return {
            "total_chunks": len(chunks),
            "min_chars":    min(sizes),
            "max_chars":    max(sizes),
            "avg_chars":    round(sum(sizes) / len(sizes), 1),
            "total_chars":  sum(sizes),
            "unique_paths": len({c.heading_path for c in chunks}),
        }
