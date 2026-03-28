"""
Table Processing
================
Preprocessing pipeline for Markdown tables extracted from multi-page
documents (PDF, Word, HTML).

Handles common artefacts produced when a single table is split across
multiple pages during conversion:

- **Repeated column headers** — deduplicated, keeping only the first.
- **Continuation rows** (empty first column) — merged into the preceding
  data row.
- **Extra noise columns** — trimmed to the expected column count.
- **Bold-only header rows** (``| **Name** | **Value** |``) — recognised
  as headers even without a separator line.
- **Page-break noise** between table fragments — detected and removed
  so fragments are re-joined into one contiguous table.

Public API
----------
``preprocess_tables(text)``
    Accept a full Markdown document string and return it with all
    multi-page table artefacts cleaned up.  This is the only function
    that consuming modules need to call.

Everything else (``_is_table_row``, ``_process_table_lines``, …) is
internal but importable for unit testing.
"""

import re
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _is_table_separator(line: str) -> bool:
    """Check if a line is a markdown table separator like |---|---|."""
    return bool(re.match(r'^\s*\|[\s\-:]+(\|[\s\-:]+)+\|?\s*$', line))


def _is_table_row(line: str) -> bool:
    """Check if a line looks like a markdown table row (starts with | and has more |)."""
    stripped = line.strip()
    return stripped.startswith('|') and '|' in stripped[1:]


def _parse_row_cells(line: str, expected_cols: Optional[int] = None) -> List[str]:
    """Parse a markdown table row into individual cell values.

    If *expected_cols* is given, any extra columns (typically page footer
    text appended after the last real column) are silently dropped and
    missing columns are padded with empty strings.
    """
    parts = line.split('|')
    # Strip the empty strings produced by leading / trailing pipes
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]

    cells = [p.strip() for p in parts]

    if expected_cols is not None:
        # Trim noise columns that exceed the expected count
        if len(cells) > expected_cols:
            cells = cells[:expected_cols]
        # Pad if a row has fewer columns than expected
        while len(cells) < expected_cols:
            cells.append('')

    return cells


def _cells_to_row(cells: List[str]) -> str:
    """Rebuild a markdown table row from cell values."""
    return '| ' + ' | '.join(cells) + ' |'


def _make_separator(num_cols: int) -> str:
    """Create a markdown separator row for *num_cols* columns."""
    return '|' + '|'.join(['---'] * num_cols) + '|'


def _strip_bold(text: str) -> str:
    """Remove markdown bold markers (**…** and __…__) from *text*."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    return text


def _normalize_header(cells: List[str]) -> Tuple[str, ...]:
    """Normalize header cells for comparison.

    Bold markers and whitespace are stripped so that formatting
    differences (e.g. ``**Name**`` vs ``Name``, OCR artefacts like
    "Umsetzungshinwei s" vs "Umsetzungshinweis") don't break matching.
    """
    return tuple(re.sub(r'\s+', '', _strip_bold(c).lower()) for c in cells)


def _is_all_bold_row(cells: List[str]) -> bool:
    """Return True when every non-empty cell is wrapped in bold markers."""
    non_empty = [c for c in cells if c.strip()]
    if not non_empty:
        return False
    return all(
        (c.strip().startswith('**') and c.strip().endswith('**'))
        or (c.strip().startswith('__') and c.strip().endswith('__'))
        for c in non_empty
    )


def _is_continuation_row(cells: List[str]) -> bool:
    """Return True when the first (key) column is empty but other cells carry content."""
    return not cells[0].strip() and any(c.strip() for c in cells[1:])


def _merge_row_cells(base: List[str], cont: List[str]) -> List[str]:
    """Append continuation-cell text to the corresponding base cells."""
    merged = []
    for b, c in zip(base, cont):
        if c.strip():
            merged.append((b + ' ' + c).strip())
        else:
            merged.append(b)
    return merged


# ---------------------------------------------------------------------------
# Core table processing
# ---------------------------------------------------------------------------

def _process_table_lines(table_lines: List[str]) -> List[str]:
    """Clean a set of table lines that logically belong to the same table.

    1. Identify the primary header (first header + separator pair).
    2. Remove all duplicate header + separator pairs.
    3. Trim extra noise columns beyond the expected column count.
    4. Merge continuation rows (empty first cell) into the previous data row.
    """
    # -- find primary header --------------------------------------------------
    primary_header: Optional[List[str]] = None
    primary_header_norm: Optional[Tuple[str, ...]] = None
    num_cols: Optional[int] = None
    start_idx = 0

    for j in range(len(table_lines)):
        line = table_lines[j]
        if _is_table_row(line) and not _is_table_separator(line):
            cells = _parse_row_cells(line)
            if j + 1 < len(table_lines) and _is_table_separator(table_lines[j + 1]):
                primary_header = cells
                primary_header_norm = _normalize_header(cells)
                num_cols = len(cells)
                start_idx = j + 2  # skip past header + separator
                break

    if primary_header is None or num_cols is None:
        return table_lines  # nothing to do

    # -- walk through remaining lines -----------------------------------------
    data_rows: List[List[str]] = []

    for j in range(start_idx, len(table_lines)):
        line = table_lines[j]

        if _is_table_separator(line):
            continue

        if not _is_table_row(line):
            continue  # stray non-table line inside the block – skip

        cells = _parse_row_cells(line, expected_cols=num_cols)

        # skip repeated headers
        if _normalize_header(cells) == primary_header_norm:
            continue

        # merge continuation rows into the previous data row
        if _is_continuation_row(cells) and data_rows:
            data_rows[-1] = _merge_row_cells(data_rows[-1], cells)
        elif _is_continuation_row(cells) and not data_rows:
            # Orphaned continuation row with no preceding data row –
            # treat it as a normal row rather than dropping it.
            data_rows.append(cells)
        else:
            data_rows.append(cells)

    # -- rebuild clean table --------------------------------------------------
    result = [_cells_to_row(primary_header), _make_separator(num_cols)]
    for row in data_rows:
        result.append(_cells_to_row(row))
    return result


# ---------------------------------------------------------------------------
# High-level table-aware preprocessor
# ---------------------------------------------------------------------------

def preprocess_tables(text: str) -> str:
    """Preprocess markdown text to clean up tables that span multiple pages.

    Handles:
    * Repeated column headers (from page breaks) → deduplicated.
    * Bold-only header rows without separators → recognised and matched.
    * Continuation rows (empty key column) → merged with previous row.
    * Extra noise columns beyond the expected count → trimmed.
    * Headerless table continuations after a page break → merged with
      the preceding table fragment.
    * Non-table text between two fragments of the *same* table (same
      header) → treated as page-break noise and removed.
    * Non-table text between genuinely *different* tables → preserved.
    """
    lines = text.split('\n')

    # -- Phase 1: segment lines into table / text blocks ----------------------
    segments: List[Tuple[str, List[str]]] = []
    i = 0
    while i < len(lines):
        if _is_table_row(lines[i]) or _is_table_separator(lines[i]):
            block: List[str] = []
            while i < len(lines) and (_is_table_row(lines[i]) or _is_table_separator(lines[i])):
                block.append(lines[i])
                i += 1
            segments.append(('table', block))
        else:
            block = []
            while i < len(lines) and not (_is_table_row(lines[i]) or _is_table_separator(lines[i])):
                block.append(lines[i])
                i += 1
            segments.append(('text', block))

    # -- Phase 2: merge table segments that share the same header -------------
    #   Pattern: table  [text  table]*  where all tables have the same header
    #   → the intermediate text is page-break noise and gets dropped.
    def _extract_header_norm(seg_lines: List[str]) -> Optional[Tuple[str, ...]]:
        for j in range(len(seg_lines)):
            if _is_table_row(seg_lines[j]) and not _is_table_separator(seg_lines[j]):
                cells = _parse_row_cells(seg_lines[j])
                # Standard header: row followed by a separator line
                if j + 1 < len(seg_lines) and _is_table_separator(seg_lines[j + 1]):
                    return _normalize_header(cells)
                # Bold header without separator: every cell is **bold**
                # Only trust this for the very first row to avoid
                # false-positives on bold data rows deeper in the table.
                if j == 0 and _is_all_bold_row(cells):
                    return _normalize_header(cells)
        return None

    def _next_table_starts_with_continuation(seg_lines: List[str]) -> bool:
        """Check if a table segment's first data row is a continuation row.

        If the first real data row (after the header+separator) has an
        empty first column, it can only make sense as a continuation of a
        preceding table — meaning any text between the two fragments is
        page-break noise, not meaningful content.
        """
        past_header = False
        for line in seg_lines:
            if _is_table_separator(line):
                past_header = True
                continue
            if not past_header:
                continue  # skip the header row itself
            if not _is_table_row(line):
                continue
            cells = _parse_row_cells(line)
            return _is_continuation_row(cells)
        return False

    def _is_page_break_noise(text_lines: List[str],
                             next_table_lines: Optional[List[str]] = None) -> bool:
        """Determine whether an inter-table text block is page-break noise.

        The decision is based on *structure*, not character count:

        1. If the text is blank → noise.
        2. If the text contains structural markdown (headings, lists,
           block-quotes) → real content, keep it.
        3. If the *next* table segment starts with a continuation row
           (empty first column) → the table was split by a page break,
           so the text between is definitely noise regardless of content.
        4. Without a continuation-row signal we only drop text if
           **every** non-blank line matches an explicit page-artefact
           pattern (page numbers, footnote URLs).  Short ambiguous text
           is preserved — better safe than sorry.
        """
        non_blank = [l.strip() for l in text_lines if l.strip()]
        if not non_blank:
            return True  # blank lines only → noise

        content = '\n'.join(non_blank)

        # ── structural markdown → real content ───────────────────────
        if re.search(r'^#{1,6}\s', content, re.MULTILINE):
            return False
        if re.search(r'^\s*[-*•]\s', content, re.MULTILINE):
            return False
        if re.search(r'^\s*\d+[.)]\s', content, re.MULTILINE):
            return False
        if re.search(r'^\s*>', content, re.MULTILINE):      # block-quote
            return False

        has_continuation = (
            next_table_lines is not None
            and _next_table_starts_with_continuation(next_table_lines)
        )

        # ── next table starts with a continuation row → noise ────────
        # The continuation row proves the table was split across pages,
        # so any text between the fragments is page-break artefact.
        if has_continuation:
            return True

        # ── per-line artefact detection (strict, no ambiguity) ───────
        # Without a continuation-row signal we only drop text when
        # **every** line matches an unambiguous artefact pattern.
        artefact_patterns = [
            # Bare page numbers: "16", "- 16 -", "Seite 3", "Page 12"
            r'^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$',
            r'^\s*(?:Seite|Page|S\.)\s*\d+\s*$',
            # Footnote references: "7 https://..."
            r'^\s*\d+\s+https?://',
        ]
        compiled = [re.compile(p, re.IGNORECASE) for p in artefact_patterns]

        all_artefacts = True
        for line in non_blank:
            if any(pat.match(line) for pat in compiled):
                continue
            all_artefacts = False
            break

        return all_artefacts

    merged_segments: List[Tuple[str, List[str]]] = []
    i = 0
    while i < len(segments):
        seg_type, seg_lines = segments[i]

        if seg_type == 'table':
            header_norm = _extract_header_norm(seg_lines)
            combined = list(seg_lines)

            # look ahead: text + table with same header (or headerless continuation) → merge
            k = i + 1
            while k + 1 < len(segments):
                if segments[k][0] == 'text' and segments[k + 1][0] == 'table':
                    next_header = _extract_header_norm(segments[k + 1][1])

                    # Case 1: Next table repeats the same header → merge
                    same_header = header_norm and next_header == header_norm

                    # Case 2: Next table has NO header at all (headerless
                    # continuation after a page break — the header was only
                    # on the first page).  Only merge when the current
                    # segment already owns a header and the intervening
                    # text is page-break noise.
                    headerless_continuation = (
                        header_norm is not None
                        and next_header is None
                    )

                    if same_header or headerless_continuation:
                        # Only merge if the intervening text is page-break
                        # noise. If it's substantive content, stop merging
                        # so both the text and the next table are preserved.
                        if _is_page_break_noise(segments[k][1],
                                                next_table_lines=segments[k + 1][1]):
                            combined.extend(segments[k + 1][1])
                            k += 2
                            continue
                break

            processed = _process_table_lines(combined)
            merged_segments.append(('table', processed))
            i = k
        else:
            merged_segments.append(('text', seg_lines))
            i += 1

    # -- Phase 3: reconstruct the document ------------------------------------
    result_lines: List[str] = []
    for _, seg_lines in merged_segments:
        result_lines.extend(seg_lines)
    return '\n'.join(result_lines)
