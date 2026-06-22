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
        """Lazily load the sentence-transformers model.

        Tries online first; if the network is unavailable (e.g. behind a
        firewall) and the model is already cached locally, falls back to
        offline mode automatically.
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for MiniLMEmbedder. "
                    "Install it with: pip install sentence-transformers"
                )

            logger.info("minilm_loading", model=self._model_name)
            try:
                self._model = SentenceTransformer(self._model_name)
            except Exception as exc:
                # Network failure — try offline mode with cached model
                logger.warning(
                    "minilm_online_load_failed",
                    model=self._model_name,
                    error=str(exc),
                    hint="Retrying with local_files_only (cached model)",
                )
                try:
                    self._model = SentenceTransformer(
                        self._model_name, local_files_only=True
                    )
                except Exception as offline_exc:
                    raise RuntimeError(
                        f"Failed to load embedder model '{self._model_name}'. "
                        f"Online error: {exc}. Offline error: {offline_exc}. "
                        "Download the model once with a working network, or "
                        "set HF_HUB_OFFLINE=1 after caching."
                    ) from offline_exc

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
