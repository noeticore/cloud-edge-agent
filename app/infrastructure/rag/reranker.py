"""LLM-based reranker — uses an LLM to score document relevance."""

import json

from app.core.logger.logger import get_logger
from app.domain.llm.llm_client import LLMClient, LLMMessage
from app.domain.rag.rag import Reranker, RetrievalResult

logger = get_logger(__name__)

_RERANK_PROMPT = """Score the relevance of the following document to the query.
Respond with ONLY a JSON object: {{"score": <float between 0.0 and 1.0>}}

Query: {query}

Document:
{document}"""


class LLMReranker(Reranker):
    """Rerank retrieved documents using an LLM as a relevance judge.

    For each document, asks the LLM to output a relevance score in [0, 1],
    then returns the top-k highest-scoring results.
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

        scored: list[RetrievalResult] = []
        for result in results:
            score = await self._score(query, result.document.content)
            scored.append(
                RetrievalResult(document=result.document, score=score)
            )

        scored.sort(key=lambda r: r.score, reverse=True)
        reranked = scored[:top_k]

        logger.info(
            "reranker_done",
            query=query[:80],
            input_count=len(results),
            output_count=len(reranked),
        )
        return reranked

    async def _score(self, query: str, document: str) -> float:
        """Ask the LLM to score document relevance to the query."""
        # Truncate long documents to save tokens
        doc_preview = document[: self._max_doc_chars]
        messages = [
            LLMMessage(
                role="user",
                content=_RERANK_PROMPT.format(query=query, document=doc_preview),
            )
        ]
        try:
            response = await self._client.invoke(messages)
            parsed = json.loads(response.content.strip())
            score = float(parsed.get("score", 0.0))
            return max(0.0, min(1.0, score))
        except Exception as exc:
            logger.warning("reranker_score_failed", error=str(exc))
            return 0.0
