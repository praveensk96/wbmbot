"""
HTML → Markdown Parser
======================
Converts an HTML file to clean Markdown suitable for passing directly
to MarkdownChunker.

Based on the html2text library with post-processing to:
  - Remove ZWNJ (zero-width non-joiner) characters
  - Rejoin hyphenated word-splits (e.g. "übergeord- nete" → "übergeordnete")
  - Deduplicate blank lines
  - Normalise list indentation

Table handling
--------------
html2text is configured to output GFM pipe-table syntax (bypass_tables=False).
This is required for MarkdownChunker's table-aware splitting and header
carry-over to work correctly.

Usage
-----
    from html_parser import HTML2MarkdownParser
    from markdown_chunker import MarkdownChunker

    parser  = HTML2MarkdownParser()
    md      = parser.parse_file("report.html")

    chunker = MarkdownChunker(
        max_chunk_size=2000,
        doc_metadata=parser.last_metadata,   # title extracted from <title>
    )
    chunks = chunker.chunk(md)

Requirements
------------
    pip install html2text beautifulsoup4 chardet
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any, List

from llama_index.core.readers.base import BaseReader
from llama_index.core.schema import Document

try:
    import html2text as _html2text  # pip install html2text
except ImportError as exc:
    raise ImportError("html2text is required: pip install html2text") from exc

try:
    from bs4 import BeautifulSoup  # pip install beautifulsoup4
except ImportError as exc:
    raise ImportError("beautifulsoup4 is required: pip install beautifulsoup4") from exc

try:
    import chardet  # pip install chardet
except ImportError as exc:
    raise ImportError("chardet is required: pip install chardet") from exc


class HTML2MarkdownParser(BaseReader):
    """
    Parse an HTML file (or HTML string) into clean Markdown.

    Output is directly compatible with MarkdownChunker — headings become
    #-prefixed lines and tables are emitted as GFM pipe tables when possible.

    Implements LlamaIndex BaseReader so it can be used directly as a
    file_extractor in SimpleDirectoryReader.
    """

    def __init__(self) -> None:
        self.last_metadata: dict = {}   # populated after parse_file()

    # ── HTML → raw Markdown conversion ──────────────────────────────────────

    @staticmethod
    def _make_converter() -> "_html2text.HTML2Text":
        md = _html2text.HTML2Text()
        md.body_width     = 0        # no line wrapping
        md.unicode_snob   = True     # keep unicode characters as-is
        md.bypass_tables  = False    # attempt GFM pipe-table output
        md.ignore_tables  = False    # do not silently drop tables
        return md

    @staticmethod
    def _rejoin_hyphens(text: str) -> str:
        """Remove unnecessary soft word-splits like 'übergeord- nete'."""
        i = 0
        result = ""
        for match in re.finditer(r"[a-z](- )[a-z]", text):
            # Keep the split when followed by German conjunctions "und" / "oder"
            suffix_start = match.end() - 1
            if (
                text[suffix_start: suffix_start + 4] == "und "
                or text[suffix_start: suffix_start + 5] == "oder "
            ):
                continue
            result += text[i: match.start() + 1]
            i = match.end() - 1
        return result + text[i:]

    @classmethod
    def _parse_html(cls, html_text: str) -> str:
        """Convert *html_text* to clean Markdown. Returns Markdown string."""
        converter = cls._make_converter()
        text = converter.handle(html_text)

        # Remove ZWNJ characters
        text = text.replace("\u200c", "")

        # Rejoin hyphenated word-splits
        text = cls._rejoin_hyphens(text)

        buffer: list[str] = []
        is_list  = False
        is_break = True

        for row in text.split("\n"):
            # Unordered list item
            if re.match(r"^\s*[-*]\s.+", row):
                is_list = True
                buffer.append(row.lstrip())
                continue

            # Ordered list item (also catches headings mistakenly numbered)
            if re.match(r"^\s*[0-9]+\.\s.+", row):
                m = re.match(r"^\s*[0-9]+\.\s(#+\s.+)", row)
                if m:
                    buffer.append(m.group(1))
                else:
                    is_list = True
                    buffer.append(row.lstrip())
                continue

            if is_list:
                if row.strip():
                    is_list = False
                    buffer.append("")
                else:
                    continue

            elif not row.strip():
                if is_break:
                    continue   # deduplicate consecutive blank lines
                is_break = True
                buffer.append("")
                continue

            is_break = False
            buffer.append(row)

        return "\n".join(buffer)

    # ── Public API ───────────────────────────────────────────────────────────

    def parse_string(self, html_text: str, metadata: dict | None = None) -> str:
        """
        Convert an HTML string to Markdown.

        Parameters
        ----------
        html_text : Raw HTML content.
        metadata  : Optional document-level metadata dict merged into
                    self.last_metadata (title extracted from <title> wins
                    unless already present in *metadata*).
        """
        soup = BeautifulSoup(html_text, "html.parser")
        meta: dict = dict(metadata or {})
        if "title" not in meta:
            try:
                if soup.title and soup.title.string:
                    meta["title"] = soup.title.string.strip()
            except Exception:
                pass
        self.last_metadata = meta
        return self._parse_html(html.unescape(html_text))

    def parse_file(self, file_path: str, metadata: dict | None = None) -> str:
        """
        Read *file_path*, detect its encoding, and convert to Markdown.

        Also loads a `<file_path>.meta` JSON sidecar if it exists,
        matching the convention from the AdvancedHTMLNodeParser pipeline.

        Parameters
        ----------
        file_path : Path to the .html file.
        metadata  : Optional metadata dict; merged with sidecar + <title>.

        Returns
        -------
        Markdown string.
        """
        path = Path(file_path)
        print(f"-> Parsing HTML file: {path}")

        # ── Load .meta sidecar ───────────────────────────────────────────────
        meta_dict: dict = {}
        meta_file = str(path) + ".meta"
        if os.path.exists(meta_file):
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta_dict = json.load(f)
            except Exception:
                pass

        # Caller-supplied metadata takes precedence over sidecar
        if metadata:
            meta_dict = {**meta_dict, **metadata}

        # ── Detect encoding and read ─────────────────────────────────────────
        raw_bytes = path.read_bytes()
        detected  = chardet.detect(raw_bytes)
        encoding  = detected.get("encoding") or "utf-8"
        print(f"   Detected encoding: {encoding}")

        html_text = path.read_text(encoding=encoding)
        return self.parse_string(html_text, metadata=meta_dict)

    def load_data(self, file: Path, **kwargs: Any) -> List[Document]:
        """
        LlamaIndex BaseReader interface.

        Converts the HTML file to Markdown and returns it as a single
        Document, compatible with the unified ``_chunk_markdown_documents``
        pipeline.
        """
        extra_info = kwargs.get("extra_info", {})
        markdown_text = self.parse_file(str(file), metadata=extra_info)
        if not markdown_text:
            return []
        meta = {**self.last_metadata, **extra_info}
        return [Document(text=markdown_text, metadata=meta)]
