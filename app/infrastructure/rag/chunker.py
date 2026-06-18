"""Fixed-size text chunker with overlap — splits documents into chunks."""

from app.domain.rag.rag import Chunker, Document


class FixedSizeChunker(Chunker):
    """Split documents into fixed-size chunks with configurable overlap.

    Suitable for most RAG pipelines. For structured documents (code, markdown),
    consider a semantic chunker that respects boundaries.
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 64) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be >= 0 and < chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, document: Document) -> list[Document]:
        """Split a document into overlapping text chunks.

        Args:
            document: source document to split.

        Returns:
            List of Document chunks with inherited metadata and chunk index.
        """
        text = document.content
        if not text:
            return []

        chunks: list[Document] = []
        start = 0
        idx = 0

        while start < len(text):
            end = start + self._chunk_size
            chunk_text = text[start:end]

            # Skip whitespace-only trailing chunks
            if not chunk_text.strip():
                start += self._chunk_size - self._overlap
                idx += 1
                continue

            chunk_metadata = {
                **document.metadata,
                "chunk_index": idx,
                "source_doc_id": document.doc_id,
            }
            chunks.append(
                Document(
                    content=chunk_text,
                    metadata=chunk_metadata,
                    doc_id=f"{document.doc_id}_chunk{idx}" if document.doc_id else "",
                )
            )

            start += self._chunk_size - self._overlap
            idx += 1

            # start always advances since overlap < chunk_size

        return chunks
