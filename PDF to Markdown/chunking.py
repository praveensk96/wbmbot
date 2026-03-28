import hashlib
from typing import List

from llama_index.core.schema import BaseNode, Document, TextNode

from markdown_chunker import MarkdownChunker
from table_processing import preprocess_tables


# ---------------------------------------------------------------------------
# Unified chunking for all markdown document types (PDF, HTML, Word)
# ---------------------------------------------------------------------------

def _chunk_markdown_documents(
    documents: List[Document],
    max_chunk_size: int = 2000,
    merge_tables: bool = True,
) -> List[BaseNode]:
    """Chunk LlamaIndex Documents containing Markdown into TextNode objects.

    This is the single chunking entry-point for all three parsers
    (PDF, HTML, Word).  The pipeline is:

    1. Table preprocessing – deduplicate cross-page headers, merge
       continuation rows, trim noise columns.
    2. ``MarkdownChunker`` – heading-breadcrumb context, table-aware
       splitting, page-marker extraction (PDF only).
    3. Convert each ``Chunk`` into a LlamaIndex ``TextNode`` with
       structured metadata (heading path, page provenance, etc.).

    Parameters
    ----------
    documents : List of LlamaIndex ``Document`` objects whose ``.text``
        is Markdown.  Metadata from each Document is forwarded to every
        resulting node.
    max_chunk_size : Maximum character count per chunk (including the
        heading-path prefix).
    merge_tables : Run the cross-page table preprocessor before chunking.

    Returns
    -------
    List[BaseNode] (TextNode instances) ready for indexing / embedding.
    """
    nodes: List[BaseNode] = []

    for doc in documents:
        text = doc.text
        if not text or not text.strip():
            continue

        doc_meta = dict(doc.metadata) if doc.metadata else {}
        doc_title = doc_meta.get("title", doc_meta.get("file_name", ""))

        # 1. Table preprocessing
        if merge_tables:
            text = preprocess_tables(text)

        # 2. Chunk via MarkdownChunker
        chunker = MarkdownChunker(
            max_chunk_size=max_chunk_size,
            doc_title=doc_title,
            doc_metadata=doc_meta,
        )
        chunks = chunker.chunk(text)

        # 3. Convert to TextNode
        for chunk in chunks:
            node_meta = {**doc_meta, **chunk.metadata}
            node_meta["heading_path"] = chunk.heading_path

            # Page provenance — only populated for PDFs (which embed
            # <!--page:N--> markers); HTML / Word chunks will have
            # page_start == page_end == 0 and empty pages list.
            if chunk.pages:
                node_meta["page_start"] = chunk.page_start
                node_meta["page_end"] = chunk.page_end
                node_meta["pages"] = chunk.pages

            node = TextNode(
                text=chunk.full_text,
                metadata=node_meta,
            )
            node.id_ = hashlib.md5(
                (doc_meta.get("file_name", "") + ":" + chunk.full_text).encode()
            ).hexdigest()
            nodes.append(node)

    return nodes
