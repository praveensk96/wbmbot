"""
Heuristic PDF Parser
====================
Font-size and layout based PDF → Markdown parser.

Pipeline
--------
1. Extract text blocks with full font metadata (size, bold, position, page)
2. Classify each block as header-zone / body / footer-zone
3. Detect repeating header/footer patterns (normalize page numbers) and drop them
4. Find body text size = statistical mode of all body-zone font sizes
5. Cluster font sizes above body → heading levels H1…H6
6. Optionally use PDF bookmarks/TOC to reinforce heading detection
7. Identify bold single-line short blocks as sub-headings (with guards against
   false positives such as bold paragraph labels, list items, captions)
8. Assemble clean Markdown with blank lines around headings

Outputs page-number markers (<!--page:N-->) in the Markdown so that the
downstream chunker can populate per-chunk page provenance metadata.

After every parse() call, parse quality is recorded in last_parse_stats
(a ParseStats instance) to enable automated content-integrity auditing.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from pdf_config import ParserConfig, ParseStats

try:
    import pdfplumber  # type: ignore[import]  # pip install pdfplumber
except ImportError as exc:  # pragma: no cover
    raise ImportError("pdfplumber is required: pip install pdfplumber") from exc

try:
    from pypdf import PdfReader  # type: ignore[import]  # pip install pypdf
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment,misc]  # TOC extraction disabled


class HeuristicPDFParser:
    """Font-size and layout based PDF → Markdown parser."""

    def __init__(self, cfg: ParserConfig | None = None):
        self.cfg = cfg or ParserConfig()
        self.last_parse_stats: ParseStats | None = None   # populated after each parse()

    # ── 1. Block extraction ──────────────────────────────────────────────────

    @staticmethod
    def _table_to_markdown(raw: list[list]) -> list[str]:
        """
        Convert pdfplumber table data (list-of-rows of cells) to Markdown table lines.

        Returns an empty list when the table has no usable content.
        """
        cleaned: list[list[str]] = []
        for row in raw:
            cells = [
                str(c).replace("\n", " ").strip() if c is not None else ""
                for c in row
            ]
            if any(c for c in cells):   # skip fully-empty rows
                cleaned.append(cells)

        if not cleaned:
            return []

        # Normalise column count across all rows
        num_cols = max(len(r) for r in cleaned)
        for r in cleaned:
            while len(r) < num_cols:
                r.append("")

        header = cleaned[0]
        lines = [
            "| " + " | ".join(header) + " |",
            "|" + "|".join(["---"] * num_cols) + "|",
        ]
        for row in cleaned[1:]:
            lines.append("| " + " | ".join(row[:num_cols]) + " |")
        return lines

    def _extract_blocks(self, doc: "pdfplumber.PDF") -> tuple[list[dict], int]:  # type: ignore[name-defined]
        """
        Return list of text-block dicts (and Markdown table blocks) for every page.

        Pipeline per page
        -----------------
        1. Detect tables with pdfplumber's find_tables(); format as Markdown.
        2. Extract text lines, skipping any line whose vertical midpoint falls
           inside a detected table bounding box (prevents duplicate content).
        3. Group remaining text lines into paragraph blocks.
        4. Sort all blocks (text + table) by their top-y position so the final
           document preserves reading order.
        """
        blocks_out: list[dict] = []
        empty_drops = 0   # paragraph groups with no extractable characters

        for page_num, page in enumerate(doc.pages):
            ph = float(page.height)
            pw = float(page.width)
            page_blocks: list[dict] = []

            # ── 1a. Extract tables ───────────────────────────────────────────
            table_bboxes: list[tuple] = []   # (x0, top, x1, bottom) per table
            for tbl in page.find_tables():
                tbl_bbox = tbl.bbox          # (x0, top, x1, bottom)
                raw = tbl.extract()
                if not raw:
                    continue
                table_bboxes.append(tbl_bbox)
                md_lines = self._table_to_markdown(raw)
                if not md_lines:
                    continue

                top_y, bot_y = tbl_bbox[1], tbl_bbox[3]
                zone = "body"
                if top_y < ph * self.cfg.header_zone:
                    zone = "header"
                elif bot_y > ph * (1 - self.cfg.footer_zone):
                    zone = "footer"

                page_blocks.append({
                    "page":        page_num,
                    "text":        "\n".join(md_lines),
                    "size":        0.0,
                    "all_bold":    False,
                    "single_line": False,
                    "zone":        zone,
                    "bbox":        (tbl_bbox[0], top_y, tbl_bbox[2], bot_y),
                    "is_table":    True,
                })

            # ── 1b. Extract text lines, skipping inside table regions ────────
            def _in_table(ln_bbox: tuple) -> bool:
                """True when the line's vertical midpoint falls inside any table."""
                _, lt, _, lb = ln_bbox
                mid = (lt + lb) / 2
                for tx0, tt, tx1, tb in table_bboxes:
                    if tt <= mid <= tb:
                        return True
                return False

            # extract_text_lines returns list of {text, chars, x0, top, x1, bottom} dicts
            lines = page.extract_text_lines(return_chars=True, strip=False)
            if lines and table_bboxes:
                lines = [ln for ln in lines if not _in_table((ln["x0"], ln["top"], ln["x1"], ln["bottom"]))]

            if not lines:
                # Still sort/emit any table blocks found on this page
                page_blocks.sort(key=lambda b: b["bbox"][1])
                blocks_out.extend(page_blocks)
                continue

            # ── Compute a gap threshold for paragraph detection ──────────────
            # Use 80% of the median line height as the minimum gap that signals
            # a paragraph break (larger gap = different visual block).
            heights = [ln["bottom"] - ln["top"] for ln in lines]
            median_h = sorted(heights)[len(heights) // 2] if heights else 12.0
            gap_threshold = max(2.0, median_h * 0.8)

            # ── Group lines into visual blocks ───────────────────────────────
            para_groups: list[list[dict]] = [[lines[0]]]
            for prev, curr in zip(lines, lines[1:]):
                gap = curr["top"] - prev["bottom"]
                if gap > gap_threshold:
                    para_groups.append([])
                para_groups[-1].append(curr)

            # ── Process each paragraph block ─────────────────────────────────
            for group in para_groups:
                all_chars = [
                    c for ln in group
                    for c in ln.get("chars", [])
                    if c.get("text", "").strip()
                ]
                if not all_chars:
                    empty_drops += 1
                    continue

                total_ch = sum(len(c["text"]) for c in all_chars)
                if total_ch == 0:
                    empty_drops += 1
                    continue

                # Weighted-average font size by character count
                dom_size = round(
                    sum(c["size"] * len(c["text"]) for c in all_chars) / total_ch,
                    1,
                )

                # Bold detection: all chars must have 'Bold' in their fontname.
                # Guards against mixed-bold paragraphs being tagged as headings.
                all_bold = all(
                    "bold" in c.get("fontname", "").lower()
                    for c in all_chars
                )

                line_texts = [ln["text"].strip() for ln in group if ln["text"].strip()]
                if not line_texts:
                    empty_drops += 1
                    continue

                block_text = " ".join(line_texts)
                is_single_line = len(group) == 1

                top_y = group[0]["top"]
                bot_y = group[-1]["bottom"]

                zone = "body"
                if top_y < ph * self.cfg.header_zone:
                    zone = "header"
                elif bot_y > ph * (1 - self.cfg.footer_zone):
                    zone = "footer"

                page_blocks.append({
                    "page":        page_num,
                    "text":        block_text,
                    "size":        dom_size,
                    "all_bold":    all_bold,
                    "single_line": is_single_line,
                    "zone":        zone,
                    "bbox":        (0.0, top_y, pw, bot_y),
                })

            # ── Sort all blocks for this page by vertical position ───────────
            page_blocks.sort(key=lambda b: b["bbox"][1])
            blocks_out.extend(page_blocks)

        return blocks_out, empty_drops

    # ── 2 & 3. Header / footer detection ────────────────────────────────────

    @staticmethod
    def _normalize_hf(text: str) -> str:
        """Strip numbers (page numbers) and normalise whitespace for comparison."""
        t = re.sub(r"\b\d+\b", "#", text)
        return re.sub(r"\s+", " ", t).strip().lower()

    def _detect_hf_patterns(self, blocks: list[dict]) -> set:
        """Return normalised text patterns that recur in header/footer zones."""
        n_pages = (max(b["page"] for b in blocks) + 1) if blocks else 1
        min_pg = max(self.cfg.hf_min_pages, int(n_pages * self.cfg.hf_ratio))

        seen: dict[str, set] = defaultdict(set)
        for b in blocks:
            if b["zone"] in ("header", "footer"):
                norm = self._normalize_hf(b["text"])
                if norm:
                    seen[norm].add(b["page"])

        return {n for n, pages in seen.items() if len(pages) >= min_pg}

    def _is_hf(self, block: dict, patterns: set) -> bool:
        if block["zone"] not in ("header", "footer"):
            return False
        return self._normalize_hf(block["text"]) in patterns

    # ── 4 & 5. Heading hierarchy detection ──────────────────────────────────

    def _build_heading_map(
        self, blocks: list[dict]
    ) -> tuple[float, dict[float, int]]:
        """
        Returns (body_size, {font_size → heading_level}).
        body_size = mode of all body-zone font sizes.
        heading_level 1 = H1 (largest) … N = HN (smallest).
        """
        body_blocks = [b for b in blocks if b["zone"] == "body"] or blocks

        size_cnt = Counter(b["size"] for b in body_blocks)
        body_size: float = size_cnt.most_common(1)[0][0] if size_cnt else 12.0

        # Only sizes meaningfully larger than body qualify as heading candidates
        threshold = body_size * self.cfg.heading_size_ratio
        larger = sorted(
            {b["size"] for b in body_blocks if b["size"] > threshold},
            reverse=True,
        )

        # Cluster adjacent sizes within heading_cluster_pt into the same heading level
        clusters: list[list[float]] = []
        for s in larger:
            if not clusters or abs(s - clusters[-1][0]) > self.cfg.heading_cluster_pt:
                clusters.append([s])
            else:
                clusters[-1].append(s)

        heading_map: dict[float, int] = {}
        for lvl, cluster in enumerate(clusters[: self.cfg.max_heading_levels], 1):
            for s in cluster:
                heading_map[s] = lvl

        return body_size, heading_map

    def _get_heading_level(
        self,
        block: dict,
        body_size: float,
        heading_map: dict[float, int],
        max_level: int,
    ) -> int | None:
        """
        Return heading level (1-based) or None for body text.

        Order of precedence:
          1. Exact font-size match in heading_map
          2. Tolerance match  (±size_tolerance pt)
          3. Bold single-line short block (with false-positive guards)
        """
        size = block["size"]

        # 1. Exact match
        if size in heading_map:
            return heading_map[size]

        # 2. Tolerance match (handles floating-point PDF font sizes like 11.98 → 12)
        for ms, lvl in heading_map.items():
            if abs(size - ms) <= self.cfg.size_tolerance:
                return lvl

        # 3. Bold heading rule – strict guards to prevent false positives:
        #    • entire block must be bold (no mixed bold/normal)
        #    • must be a single line (not a multi-line bold paragraph)
        #    • must not be smaller than body text
        #    • must not end with a sentence-ending period
        #    • must not look like a list item (bullet / numbered)
        #    • must be short enough to be a heading, not a bold sentence
        if (
            self.cfg.bold_heading
            and block["all_bold"]
            and block["single_line"]
            and size >= body_size * 0.95
            and len(block["text"]) <= self.cfg.bold_heading_max_chars
            and not block["text"].rstrip().endswith(".")
            and not re.match(r"^[\u2022\u2023\u25e6\-\*\d]+[\.\)]\s", block["text"])
        ):
            # Assign level just after the deepest size-based heading
            return min(max_level + 1, 6)

        return None

    # ── 6. TOC-assisted heading matching ────────────────────────────────────

    def _get_toc(self, pdf_path: str) -> list:
        """
        Extract PDF outline/bookmarks via pypdf.
        Returns list of (level, title, 1-based-page) tuples.
        """
        if PdfReader is None:
            return []
        try:
            reader = PdfReader(pdf_path)
            outline = reader.outline
            if not outline:
                return []
            toc: list = []
            self._flatten_outline(outline, 1, toc, reader)
            return toc
        except Exception:
            return []

    def _flatten_outline(self, items: list, level: int, toc: list, reader: "PdfReader") -> None:  # type: ignore[name-defined]
        """Recursively flatten a pypdf outline into (level, title, page_1based) tuples."""
        for item in items:
            if isinstance(item, list):
                self._flatten_outline(item, level + 1, toc, reader)
            else:
                try:
                    title    = item.title
                    page_num = reader.get_destination_page_number(item) + 1  # 1-based
                    toc.append((level, title, page_num))
                except Exception:
                    pass

    def _build_toc_map(
        self, blocks: list[dict], toc: list
    ) -> dict[int, int]:
        """
        Map block indices to heading levels using PDF bookmark titles.
        Uses prefix matching to handle minor text extraction differences.
        """
        by_page: dict[int, list] = defaultdict(list)
        for lvl, title, pg in toc:
            clean = re.sub(r"\s+", " ", title.strip())
            by_page[pg - 1].append((lvl, clean))   # convert to 0-indexed page

        toc_map: dict[int, int] = {}
        for idx, blk in enumerate(blocks):
            pg = blk["page"]
            if pg not in by_page:
                continue
            blk_text = re.sub(r"\s+", " ", blk["text"]).strip()
            for lvl, title in by_page[pg]:
                # Match if block text starts with the first 40 chars of the TOC title
                if blk_text.lower().startswith(title.lower()[:40]):
                    toc_map[idx] = lvl
                    break

        return toc_map

    # ── 7 & 8. Markdown assembly ─────────────────────────────────────────────

    # Page-marker format embedded in the Markdown output.
    # Invisible in rendered Markdown (HTML comment), easy to strip, easy to parse.
    _PAGE_MARKER = "<!--page:{page}-->"
    _PAGE_MARKER_RE = re.compile(r"^<!--page:(\d+)-->$")

    def _assemble_markdown(
        self,
        blocks: list[dict],
        hf_patterns: set,
        body_size: float,
        heading_map: dict,
        toc_map: dict | None = None,
    ) -> str:
        toc_map = toc_map or {}
        max_level = len(heading_map)
        lines: list[str] = []
        current_page: int = -1   # tracks last emitted page number

        for idx, blk in enumerate(blocks):
            if self._is_hf(blk, hf_patterns):
                continue

            text = blk["text"].strip()
            if not text:
                continue

            # Emit a page-break marker whenever the page number advances.
            # Use 1-based page numbers to match human-readable PDF page numbers.
            blk_page = blk["page"] + 1
            if blk_page != current_page:
                lines.append(self._PAGE_MARKER.format(page=blk_page))
                current_page = blk_page

            # Table blocks: emit with surrounding blank lines, skip heading logic
            if blk.get("is_table"):
                if lines:
                    lines.append("")
                lines.append(text)
                lines.append("")
                continue

            # TOC match takes precedence over heuristic level
            level = toc_map.get(idx) or self._get_heading_level(
                blk, body_size, heading_map, max_level
            )

            if level is not None:
                prefix = "#" * min(level, 6)
                if lines:
                    lines.append("")        # blank line before heading
                lines.append(f"{prefix} {text}")
                lines.append("")            # blank line after heading
            else:
                lines.append(text)

        md = "\n".join(lines)
        md = re.sub(r"\n{3,}", "\n\n", md)   # collapse excessive blank lines
        return md.strip()

    # ── Title detection ──────────────────────────────────────────────────────

    @staticmethod
    def extract_title_from_pdf_obj(
        pdf,
        max_pages: int = 3,
        max_title_length: int = 80,
        min_title_length: int = 15,
    ) -> str | None:
        """
        Detect the document title from the first *max_pages* pages by selecting
        the largest-font text block that is not a TOC entry or section header.

        Returns the title string, or ``None`` if nothing suitable is found.
        """
        STOPWORDS = frozenset(
            ["inhaltsverzeichnis", "inhalt", "verzeichnis", "anhang", "kapitel"]
        )
        _DOTTED = re.compile(r"\.{5,}")
        _SPACES = re.compile(r"\s{2,}")

        candidates: list[dict] = []

        for page_idx, page in enumerate(pdf.pages[:max_pages]):
            line_infos: list[dict] = []

            for line in page.extract_text_lines(layout=True):
                text = line.get("text", "").strip()
                chars = line.get("chars", [])
                if not text or not chars:
                    continue
                # character-length guard (was incorrectly word-count before)
                if len(text) > max_title_length or len(text) < min_title_length:
                    continue
                if text.lower() in STOPWORDS:
                    continue
                if _DOTTED.search(text):
                    continue
                sizes = [c["size"] for c in chars if "size" in c]
                if not sizes:
                    continue
                avg_size = sum(sizes) / len(sizes)
                top = min(c["top"] for c in chars)
                line_infos.append({"text": text, "size": avg_size, "top": top})

            # Merge consecutive lines that share the same font size AND are
            # vertically adjacent (multi-line titles).  The proximity threshold
            # is 2.5× the line's own font size to tolerate normal leading.
            blocks: list[list[dict]] = []
            current: list[dict] = []
            for line in line_infos:
                if not current:
                    current.append(line)
                    continue
                prev = current[-1]
                same_size = abs(prev["size"] - line["size"]) < 1.0
                close_vert = (line["top"] - prev["top"]) < prev["size"] * 2.5
                if same_size and close_vert and len(current) < 5:
                    current.append(line)
                else:
                    blocks.append(current)
                    current = [line]
            if current:
                blocks.append(current)

            for group in blocks:
                combined = " ".join(x["text"] for x in group)
                combined = _SPACES.sub(" ", combined.replace("\n", " ").replace("\t", " ")).strip()
                candidates.append(
                    {
                        "text": combined,
                        "avg_size": sum(x["size"] for x in group) / len(group),
                        "top": min(x["top"] for x in group),
                        "page": page_idx + 1,
                    }
                )

        if not candidates:
            return None

        candidates.sort(key=lambda x: (-x["avg_size"], x["page"], x["top"]))
        return candidates[0]["text"]

    # ── Public API ───────────────────────────────────────────────────────────

    def parse(self, pdf_path: str) -> str:
        """Parse a PDF file and return a Markdown string."""
        toc = self._get_toc(pdf_path)
        with pdfplumber.open(pdf_path) as doc:
            blocks, empty_drops = self._extract_blocks(doc)
            detected_title = self.extract_title_from_pdf_obj(doc)

        if not blocks:
            return ""

        hf_patterns = self._detect_hf_patterns(blocks)
        body_size, heading_map = self._build_heading_map(blocks)

        print(f"  Body text size  : {body_size} pt")
        print(f"  Heading sizes   : {sorted(heading_map, reverse=True)}")
        print(f"  HF patterns     : {len(hf_patterns)} recurring pattern(s) removed")

        toc_map: dict | None = None
        if toc and len(toc) >= self.cfg.min_toc_entries:
            print(f"  TOC bookmarks   : {len(toc)} (TOC-assisted mode active)")
            toc_map = self._build_toc_map(blocks, toc)
            matched = len(toc_map)
            print(f"  TOC matches     : {matched} block(s) matched")

        md = self._assemble_markdown(blocks, hf_patterns, body_size, heading_map, toc_map)

        # ── Content-integrity audit ──────────────────────────────────────────
        hf_blocks   = [b for b in blocks if self._is_hf(b, hf_patterns)]
        body_blocks = [
            b for b in blocks
            if not self._is_hf(b, hf_patterns) and b["text"].strip()
        ]
        chars_in  = sum(len(b["text"]) for b in body_blocks)
        chars_out = len(re.sub(r"\s+", "", re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)))
        retention = round(chars_out / chars_in * 100, 1) if chars_in > 0 else 100.0

        hf_samples = [(b["page"] + 1, b["text"][:100]) for b in hf_blocks[:10]]
        warnings: list[str] = []
        threshold_pct = (1.0 - self.cfg.content_loss_warn_threshold) * 100
        if retention < threshold_pct:
            msg = (
                f"Content retention {retention:.1f}% is below the "
                f"{threshold_pct:.0f}% warning threshold. "
                f"{len(hf_blocks)} block(s) removed as header/footer — "
                "inspect last_parse_stats.hf_dropped_samples for false positives."
            )
            warnings.append(msg)
            print(f"  WARNING: {msg}")

        self.last_parse_stats = ParseStats(
            total_blocks=len(blocks),
            empty_blocks_dropped=empty_drops,
            hf_blocks_dropped=len(hf_blocks),
            chars_body_input=chars_in,
            chars_output=chars_out,
            content_retention_pct=retention,
            hf_dropped_samples=hf_samples,
            warnings=warnings,
            detected_title=detected_title,
        )
        print(f"  Content retention : {retention:.1f}%  "
              f"({chars_out:,} / {chars_in:,} body chars)")
        if empty_drops:
            print(f"  Empty groups dropped (no chars): {empty_drops}")
        if warnings:
            print(f"  ⚠  Inspect parser.last_parse_stats for details")

        return md
