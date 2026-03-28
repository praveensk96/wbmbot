# Document Parsing & Chunking Pipeline

End-to-end pipeline for converting PDF, HTML, and Word documents into
context-aware, RAG-ready text chunks with structured metadata.

## Architecture Overview

```
┌─────────────┐   ┌─────────────┐   ┌──────────────┐
│  PDF file    │   │  HTML file   │   │  Word file   │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                  │                   │
       ▼                  ▼                   ▼
 PDFTextExtractor   HTML2MarkdownParser  WordTextExtractor
  (BaseReader)        (BaseReader)         (BaseReader)
       │                  │                   │
       ▼                  ▼                   ▼
       └──────────────────┼───────────────────┘
                          │
                   List[Document]
                   (Markdown text)
                          │
                          ▼
            ┌─────────────────────────┐
            │  _chunk_markdown_documents  │
            │  (unified chunking entry)   │
            └────────────┬────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     preprocess_tables()    MarkdownChunker
     (table_processing.py)  (markdown_chunker.py)
              │                     │
              └──────────┬──────────┘
                         │
                         ▼
                  List[TextNode]
              (ready for embedding)
```

## Module Reference

### `pdf_to_markdown.py` — PDF Parser

Converts PDF files to Markdown using font-size analysis, layout
heuristics, and optional LLM refinement.

**Key classes:**

| Class | Purpose |
|---|---|
| `ParserConfig` | Dataclass with all tuneable parameters (header/footer zones, heading detection thresholds, LLM settings including context-window guard and hallucination thresholds, vLLM config) |
| `HeuristicPDFParser` | Font-size + layout analysis parser. Extracts text blocks with full font metadata, detects repeating headers/footers, clusters font sizes into heading levels H1–H6, and uses PDF bookmarks (TOC) to reinforce heading detection. |
| `LLMPDFParser` | Sends raw or heuristic-parsed text to an LLM (vLLM) for structural analysis. Two modes: `parse()` (full LLM conversion) and `refine()` (post-process heuristic output). Includes built-in hallucination guards and dynamic context-window management. |
| `PDFToMarkdown` | Orchestrator that ties strategies together: `"heuristic"`, `"llm"`, or `"heuristic+llm"`. |
| `PDFTextExtractor` | LlamaIndex `BaseReader` wrapper. Passes directly to `SimpleDirectoryReader` as a `file_extractor`. |

**PDF-specific features:**
- Page markers (`<!--page:N-->`) are embedded in Markdown output. These
  are invisible in rendered Markdown but tracked by the chunker to
  populate `page_start` / `page_end` / `pages` metadata on each chunk.
- Content-integrity reporting via `ParseStats` — tracks blocks dropped
  as headers/footers and warns when retention falls below threshold.

**LLM reliability features (`LLMPDFParser`):**

*Context-window management:*
- `llm_max_input_chars` (default `24_000` ≈ 6K tokens) caps every single
  LLM call — applies to both `parse()` and `refine()`.
- `parse()` dynamically shrinks the page batch one page at a time until
  the annotated text fits within the limit. A single oversized page is
  always sent alone rather than truncated mid-content.
- `refine()` splits large documents at **heading boundaries** via
  `_split_at_headings()`, ensuring the LLM always receives complete,
  coherent sections instead of arbitrary line-count chunks.

*Hallucination guards (run after every LLM call, warnings printed to stdout):*
- **Page-marker check** (`_check_page_markers`): verifies that every
  `<!--page:N-->` present in the input is preserved in the output.
  Missing markers indicate the LLM silently dropped page-boundary
  annotations it did not understand.
- **Word-retention check** (`_check_retention`): compares word count of
  input vs output (page markers stripped before comparison). If the LLM
  drops more than `content_loss_warn_threshold` (default 15%) of words,
  a warning is printed. Catches silent truncation, paraphrasing in refine
  mode, and hallucinated summaries.
- **Deterministic sampling** (`seed=0`): makes every LLM call reproducible
  so that any issue can be reliably reproduced and investigated.

**Parsing pipeline (heuristic mode):**
1. Extract text blocks with font metadata (size, bold, position) via pdfplumber
2. Classify each block as header-zone / body / footer-zone
3. Detect repeating header/footer patterns and remove them
4. Find body text size (statistical mode of font sizes)
5. Cluster larger font sizes → heading levels H1…H6
6. Optionally match headings against PDF bookmarks/TOC
7. Identify bold single-line short blocks as sub-headings (with false-positive guards)
8. Assemble Markdown with `<!--page:N-->` markers

**Key `ParserConfig` LLM fields:**

| Field | Default | Purpose |
|---|---|---|
| `llm_base_url` | `"http://localhost:8000/v1"` | vLLM endpoint |
| `llm_model` | `"mistral-small-24b"` | vLLM model name |
| `llm_page_batch` | `5` | Target PDF pages per LLM call (auto-reduced if content is too large) |
| `llm_max_input_chars` | `24_000` | Hard cap on characters sent per LLM call (~6K tokens); context-window guard |
| `llm_max_tokens` | `4096` | Maximum tokens in the LLM response |
| `llm_temperature` | `0.1` | Low temperature for deterministic, factual output |
| `content_loss_warn_threshold` | `0.15` | Warn if output retains < 85% of input words |

---

Converts HTML files to clean Markdown using `html2text` with
post-processing.

**Key class:** `HTML2MarkdownParser` (extends `BaseReader`)

**Pipeline:**
1. Detect file encoding via `chardet`
2. Load optional `.meta` JSON sidecar for document metadata
3. Convert HTML to Markdown via `html2text` (configured for GFM pipe tables)
4. Post-process: remove ZWNJ characters, rejoin hyphenated word-splits,
   deduplicate blank lines, normalise list indentation
5. Extract `<title>` as document metadata

**Dependencies:** `html2text`, `beautifulsoup4`, `chardet`

---

### `word_to_markdown.py` — Word Parser

Converts `.docx` files to Markdown using `pypandoc`.

**Key class:** `WordTextExtractor` (extends `BaseReader`)

**Pipeline:**
1. Convert Word document to `markdown_strict` format via pandoc
2. Return as a single LlamaIndex `Document`

**Dependencies:** `pypandoc` (requires pandoc installed on the system)

---

### `table_processing.py` — Table Preprocessing

Cleans up Markdown tables that span multiple pages — a common artefact
from PDF and Word conversion where page breaks interrupt table data.

**Public API:** `preprocess_tables(text: str) -> str`

This is the only function consumers need to call. It accepts a full
Markdown document and returns it with all multi-page table artefacts
resolved.

**Three-phase pipeline:**

#### Phase 1 — Segmentation
Splits the document into alternating `('table', lines)` and
`('text', lines)` segments. A table segment is any consecutive run of
lines that match pipe-table syntax (`| … |` or `|---|`).

#### Phase 2 — Fragment Merging
Looks for the pattern `table → text → table` and merges them when:

| Case | Condition | What happens |
|---|---|---|
| **Same header** | Both fragments have the same header (normalised, bold-stripped) | Fragments merged; duplicate header removed |
| **Bold header** | Second fragment starts with an all-bold row matching the first header | Treated as repeated header; merged and deduped |
| **Headerless continuation** | Second fragment has no header at all (just data rows) | Merged under the first fragment's header |
| **Continuation row** | Second fragment's first data row has an empty first column | Proves the table was split mid-row; text between is noise |

The intervening text is only dropped if it's classified as **page-break
noise** — blank lines, bare page numbers (`42`, `Page 3`, `Seite 12`),
or footnote URLs. Text containing structural markdown (headings, lists,
block-quotes) is always preserved.

#### Phase 3 — Row Processing (`_process_table_lines`)
Cleans the merged table:
- Identifies the primary header (first row + separator pair)
- Removes all duplicate header + separator pairs
- Trims noise columns beyond the expected count
- Merges continuation rows (empty first column) into the previous data row

**Header detection logic (`_extract_header_norm`):**

A row is recognised as a header if:
1. It is immediately followed by a separator line (`|---|---|`), **or**
2. It is the very first row and every non-empty cell is bold
   (`| **Name** | **Value** |`)

Headers are normalised by stripping bold markers, lowering case, and
removing all whitespace — so `**Umsetzungshinwei s**` matches
`Umsetzungshinweis`.

---

### `markdown_chunker.py` — Markdown Chunker

Splits any Markdown document into context-aware chunks with heading
breadcrumbs.

**Key classes:**

| Class | Purpose |
|---|---|
| `Chunk` | Dataclass for one chunk: `chunk_id`, `heading_path`, `content`, `full_text`, `page_start`, `page_end`, `pages`, `metadata` |
| `MarkdownChunker` | Configurable chunker with `max_chunk_size`, `doc_title`, `doc_metadata` |

**Chunking algorithm:**
1. Scan Markdown line-by-line
2. On heading: flush accumulated content, update the heading stack
3. Build a breadcrumb path: `"Introduction > Methodology > Data Collection"`
4. When flushing, if content exceeds the limit:
   - Split on paragraph breaks (double newlines)
   - If a paragraph is still too large, split on sentence boundaries
   - If a sentence is too large, hard-split at character limit
5. **Table-aware splitting:**
   - Column header row + separator repeated at top of every continuation chunk
   - Individual rows are never split mid-row
6. Page markers (`<!--page:N-->`) are stripped from content but tracked
   as metadata (`page_start`, `page_end`, `pages`)

**`full_text` format** (what goes into the vector store):
```
Introduction > Methodology > Data Collection

This section describes how data was collected using…
```

---

### `chunking.py` — Unified Chunking Entry Point

Ties together table preprocessing and the Markdown chunker, converting
LlamaIndex `Document` objects into `TextNode` objects.

**Key functions:**

| Function | Purpose |
|---|---|
| `_chunk_markdown_documents(documents, max_chunk_size, merge_tables)` | Unified entry point for all three parsers. Returns `List[TextNode]` ready for indexing. |

**`_chunk_markdown_documents` pipeline:**
1. Run `preprocess_tables()` on the Markdown text (dedup headers, merge
   cross-page fragments)
2. Chunk via `MarkdownChunker` (heading breadcrumbs, table-aware splitting,
   page-marker extraction)
3. Convert each `Chunk` to a LlamaIndex `TextNode` with structured metadata:
   - `heading_path` — breadcrumb string
   - `page_start`, `page_end`, `pages` — only for PDFs
   - All document-level metadata from the parser (title, source, etc.)
   - Deterministic `node.id_` based on filename + content hash

---

## Data Loader Integration

```python
from pathlib import Path
from llama_index.core import SimpleDirectoryReader

from html_to_markdown import HTML2MarkdownParser
from pdf_to_markdown import PDFTextExtractor
from word_to_markdown import WordTextExtractor
from chunking import _chunk_markdown_documents

# 1. Load documents — each parser returns Markdown as Document.text
reader = SimpleDirectoryReader(
    input_dir="./documents",
    required_exts=[".html", ".pdf", ".docx"],
    file_extractor={
        ".html": HTML2MarkdownParser(),
        ".pdf":  PDFTextExtractor(),
        ".docx": WordTextExtractor(),
    },
)
documents = reader.load_data()

# 2. Chunk — unified pipeline for all file types
nodes = _chunk_markdown_documents(documents, max_chunk_size=2000)

# 3. Use nodes for RAG indexing / embedding
for node in nodes:
    print(node.metadata["heading_path"], len(node.text), "chars")
```

## Dependencies

```
pip install pdfplumber pypdf openai html2text beautifulsoup4 chardet pypandoc llama-index-core
```

| Package | Used by |
|---|---|
| `pdfplumber` | PDF text/table extraction |
| `pypdf` | PDF TOC/bookmark extraction |
| `openai` | LLM parse/refine (vLLM) |
| `html2text` | HTML → Markdown conversion |
| `beautifulsoup4` | HTML title/metadata extraction |
| `chardet` | HTML encoding detection |
| `pypandoc` | Word → Markdown conversion (requires pandoc) |
| `llama-index-core` | Document/TextNode schema, BaseReader interface |
