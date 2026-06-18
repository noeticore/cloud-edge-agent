"""MiniLM embedder — local sentence-transformers based embedding.

Uses the all-MiniLM-L6-v2 model (384 dimensions) for fast local embeddings.
No API key required — runs entirely offline.
"""

from app.core.logger.logger import get_logger
from app.domain.rag.rag import Embedder

logger = get_logger(__name__)

# Default model for MiniLM embeddings
DEFAULT_MODEL = "all-MiniLM-L6-v2"


class MiniLMEmbedder(Embedder):
    """Embedder using sentence-transformers MiniLM model.

    Runs locally — no API calls, no network latency.
    Produces 384-dimensional embeddings.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        """Initialize the MiniLM embedder.

        Args:
            model_name: HuggingFace model name (default: all-MiniLM-L6-v2).
        """
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        """Lazily load the sentence-transformers model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("minilm_loading", model=self._model_name)
                self._model = SentenceTransformer(self._model_name)
                # get_sentence_embedding_dimension was renamed to get_embedding_dimension
                get_dim = getattr(
                    self._model,
                    "get_embedding_dimension",
                    getattr(self._model, "get_sentence_embedding_dimension", None),
                )
                dimension = get_dim() if get_dim else 384
                logger.info(
                    "minilm_loaded",
                    model=self._model_name,
                    dimension=dimension,
                )
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for MiniLMEmbedder. "
                    "Install it with: pip install sentence-transformers"
                )

    async def embed(self, text: str) -> list[float]:
        """Return embedding vector for a single text."""
        self._ensure_model()
        # sentence-transformers encode() returns numpy array
        embedding = self._model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for a batch of texts."""
        self._ensure_model()
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [vec.tolist() for vec in embeddings]
