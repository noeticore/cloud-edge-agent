"""Tests for LLMReranker — LLM-based document reranking."""

import json

import pytest

from app.domain.rag.rag import Document, RetrievalResult
from app.infrastructure.rag.reranker import LLMReranker


class FakeLLM:
    """Stub LLMClient that returns configurable scores."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self._call_count = 0

    async def invoke(self, messages):  # type: ignore[no-untyped-def]
        from app.domain.llm.llm_client import LLMResponse

        score = self._scores[self._call_count % len(self._scores)]
        self._call_count += 1
        return LLMResponse(
            content=json.dumps({"score": score}),
            model="fake",
        )


def _make_results(*contents: str) -> list[RetrievalResult]:
    """Helper to build RetrievalResult list from content strings."""
    return [
        RetrievalResult(document=Document(content=c, doc_id=f"d{i}"), score=0.5)
        for i, c in enumerate(contents)
    ]


class TestLLMReranker:
    """Unit tests for LLMReranker."""

    @pytest.mark.asyncio
    async def test_reranks_by_score(self) -> None:
        # First doc scores 0.3, second scores 0.9
        llm = FakeLLM([0.3, 0.9])
        reranker = LLMReranker(llm)

        results = await reranker.rerank(
            "query", _make_results("low relevance", "high relevance"), top_k=2
        )

        assert len(results) == 2
        assert results[0].document.content == "high relevance"
        assert results[0].score == 0.9
        assert results[1].document.content == "low relevance"
        assert results[1].score == 0.3

    @pytest.mark.asyncio
    async def test_top_k_limits_output(self) -> None:
        llm = FakeLLM([0.1, 0.5, 0.9])
        reranker = LLMReranker(llm)

        results = await reranker.rerank(
            "query", _make_results("a", "b", "c"), top_k=1
        )

        assert len(results) == 1
        assert results[0].document.content == "c"

    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        llm = FakeLLM([])
        reranker = LLMReranker(llm)

        results = await reranker.rerank("query", [], top_k=3)

        assert results == []

    @pytest.mark.asyncio
    async def test_llm_error_gives_zero_score(self) -> None:
        class BrokenLLM:
            async def invoke(self, messages):  # type: ignore[no-untyped-def]
                raise RuntimeError("LLM down")

        reranker = LLMReranker(BrokenLLM())

        results = await reranker.rerank(
            "query", _make_results("doc"), top_k=3
        )

        assert len(results) == 1
        assert results[0].score == 0.0

    @pytest.mark.asyncio
    async def test_score_clamped_to_0_1(self) -> None:
        # LLM returns out-of-range score
        llm = FakeLLM([1.5])
        reranker = LLMReranker(llm)

        results = await reranker.rerank(
            "query", _make_results("doc"), top_k=1
        )

        assert results[0].score == 1.0

    @pytest.mark.asyncio
    async def test_malformed_json_gives_zero_score(self) -> None:
        class GarbageLLM:
            async def invoke(self, messages):  # type: ignore[no-untyped-def]
                from app.domain.llm.llm_client import LLMResponse
                return LLMResponse(content="I don't know", model="fake")

        reranker = LLMReranker(GarbageLLM())

        results = await reranker.rerank(
            "query", _make_results("doc"), top_k=3
        )

        assert len(results) == 1
        assert results[0].score == 0.0

    @pytest.mark.asyncio
    async def test_custom_max_doc_chars(self) -> None:
        """Verify that truncation limit is configurable."""
        long_doc = "x" * 2000

        class CapturingLLM:
            def __init__(self) -> None:
                self.captured_doc: str = ""

            async def invoke(self, messages):  # type: ignore[no-untyped-def]
                from app.domain.llm.llm_client import LLMResponse
                # Extract document from the prompt
                self.captured_doc = messages[0].content.split("Document:\n")[-1]
                return LLMResponse(content='{"score": 0.5}', model="fake")

        llm = CapturingLLM()
        reranker = LLMReranker(llm, max_doc_chars=500)

        await reranker.rerank("query", _make_results(long_doc), top_k=1)

        assert len(llm.captured_doc) == 500
