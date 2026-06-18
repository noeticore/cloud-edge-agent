"""Tests for FixedSizeChunker — fixed-size text splitting with overlap."""

import pytest

from app.domain.rag.rag import Document
from app.infrastructure.rag.chunker import FixedSizeChunker


class TestFixedSizeChunker:
    """Unit tests for FixedSizeChunker."""

    def test_basic_split(self) -> None:
        chunker = FixedSizeChunker(chunk_size=10, overlap=0)
        doc = Document(content="0123456789abcdefghij", doc_id="d1")

        chunks = chunker.chunk(doc)

        assert len(chunks) == 2
        assert chunks[0].content == "0123456789"
        assert chunks[1].content == "abcdefghij"

    def test_overlap(self) -> None:
        chunker = FixedSizeChunker(chunk_size=10, overlap=3)
        doc = Document(content="0123456789abcdefghij", doc_id="d1")

        chunks = chunker.chunk(doc)

        assert len(chunks) == 3
        assert chunks[0].content == "0123456789"
        # Second chunk starts at position 7 (10 - 3), length 10
        assert chunks[1].content == "789abcdefg"
        # Third chunk starts at position 14
        assert chunks[2].content == "efghij"

    def test_empty_document(self) -> None:
        chunker = FixedSizeChunker(chunk_size=10, overlap=0)
        doc = Document(content="", doc_id="d1")

        chunks = chunker.chunk(doc)

        assert chunks == []

    def test_short_text_single_chunk(self) -> None:
        chunker = FixedSizeChunker(chunk_size=100, overlap=0)
        doc = Document(content="hello", doc_id="d1")

        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert chunks[0].content == "hello"

    def test_metadata_preserved(self) -> None:
        chunker = FixedSizeChunker(chunk_size=5, overlap=0)
        doc = Document(content="0123456789", doc_id="d1", metadata={"source": "test"})

        chunks = chunker.chunk(doc)

        assert len(chunks) == 2
        for i, c in enumerate(chunks):
            assert c.metadata["source"] == "test"
            assert c.metadata["chunk_index"] == i
            assert c.metadata["source_doc_id"] == "d1"

    def test_chunk_ids_generated(self) -> None:
        chunker = FixedSizeChunker(chunk_size=5, overlap=0)
        doc = Document(content="0123456789", doc_id="d1")

        chunks = chunker.chunk(doc)

        assert chunks[0].doc_id == "d1_chunk0"
        assert chunks[1].doc_id == "d1_chunk1"

    def test_invalid_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            FixedSizeChunker(chunk_size=0, overlap=0)

    def test_invalid_overlap_equal(self) -> None:
        with pytest.raises(ValueError, match="overlap must be"):
            FixedSizeChunker(chunk_size=10, overlap=10)

    def test_invalid_overlap_greater(self) -> None:
        with pytest.raises(ValueError, match="overlap must be"):
            FixedSizeChunker(chunk_size=5, overlap=10)
