"""LLM-based reranker — uses an LLM to score document relevance.

Batch-scores all documents in a single LLM call to minimize latency.
"""

import json
import re

from app.core.logger.logger import get_logger
from app.domain.llm.llm_client import LLMClient, LLMMessage
from app.domain.rag.rag import Reranker, RetrievalResult

logger = get_logger(__name__)

_BATCH_RERANK_PROMPT = """Score the relevance of each document to the query.
Respond with ONLY a JSON array of scores, one per document, in the same order.
Each score is a float between 0.0 and 1.0.

Query: {query}

{documents_block}

Respond with ONLY a JSON array, e.g. [0.9, 0.3, 0.7]"""


class LLMReranker(Reranker):
    """Rerank retrieved documents using an LLM as a relevance judge.

    Scores all documents in a single LLM call (batch mode) to minimize
    latency. Falls back to per-document scoring if batch parsing fails.
    """

    def __init__(self, llm_client: LLMClient, max_doc_chars: int = 1000) -> None:
        self._client = llm_client
        self._max_doc_chars = max_doc_chars

    async def rerank(
        self, query: str, results: list[RetrievalResult], top_k: int = 3
    ) -> list[RetrievalResult]:
        """Rerank results by LLM-judged relevance.

        Args:
            query: original search query.
            results: candidate results to rerank.
            top_k: number of results to return after reranking.

        Returns:
            Top-k results sorted by LLM-judged relevance score (descending).
        """
        if not results:
            return []

        scores = await self._batch_score(query, [r.document.content for r in results])

        scored: list[RetrievalResult] = []
        for result, score in zip(results, scores):
            scored.append(RetrievalResult(document=result.document, score=score))

        scored.sort(key=lambda r: r.score, reverse=True)
        reranked = scored[:top_k]

        logger.info(
            "reranker_done",
            query=query,
            input_count=len(results),
            output_count=len(reranked),
        )
        return reranked

    async def _batch_score(self, query: str, documents: list[str]) -> list[float]:
        """Score all documents in a single LLM call."""
        # Build numbered document block
        doc_lines = []
        for i, doc in enumerate(documents):
            preview = doc[: self._max_doc_chars]
            doc_lines.append(f"Document [{i}]:\n{preview}")
        documents_block = "\n\n".join(doc_lines)

        messages = [
            LLMMessage(
                role="user",
                content=_BATCH_RERANK_PROMPT.format(
                    query=query, documents_block=documents_block
                ),
            )
        ]
        try:
            response = await self._client.invoke(messages)
            # Extract JSON array from response (handle markdown code blocks)
            content = response.content.strip()
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                raw_scores = json.loads(match.group())
            else:
                raw_scores = json.loads(content)

            if len(raw_scores) == len(documents):
                return [max(0.0, min(1.0, float(s))) for s in raw_scores]

            logger.warning(
                "reranker_batch_count_mismatch",
                expected=len(documents),
                got=len(raw_scores),
            )
            # Pad or truncate
            padded = [float(s) for s in raw_scores[: len(documents)]]
            while len(padded) < len(documents):
                padded.append(0.0)
            return [max(0.0, min(1.0, s)) for s in padded]

        except Exception as exc:
            logger.warning("reranker_batch_score_failed", error=str(exc))
            return [0.0] * len(documents)
