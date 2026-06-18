"""Embedder backed by an LLMClient's embedding endpoint."""

from app.core.logger.logger import get_logger
from app.domain.llm.llm_client import LLMClient
from app.domain.rag.rag import Embedder

logger = get_logger(__name__)


class LLMEmbedder(Embedder):
    """Compute embeddings via any LLMClient that supports embedding().

    Delegates to the LLM provider's embedding API (Ollama, DeepSeek, etc.).
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._client = llm_client

    async def embed(self, text: str) -> list[float]:
        """Return embedding vector for a single text."""
        return await self._client.embedding(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for a batch of texts.

        Note: processes sequentially. Providers with native batch APIs
        can override this for better throughput.
        """
        return [await self._client.embedding(t) for t in texts]
