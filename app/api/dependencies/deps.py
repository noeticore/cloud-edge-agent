"""FastAPI dependency injection — wires up all components.

This module creates singleton instances of all services and provides
them as FastAPI dependencies. Uses lifespan events for initialization.
"""

from dataclasses import dataclass

from app.core.config.settings import Settings, get_settings
from app.domain.memory.memory import MemoryStore
from app.domain.privacy.privacy import PrivacyBudgetTracker
from app.infrastructure.cache.cache import InMemoryCache
from app.infrastructure.database.session_repository import (
    InMemorySessionStore,
    InMemoryShortTermStore,
)
from app.infrastructure.llm.client_factory import (
    create_cloud_llm_client,
    create_edge_llm_client,
)
from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient
from app.infrastructure.rag.llm_embedder import LLMEmbedder
from app.infrastructure.vectorstore.qdrant_store import QdrantMemoryStore
from app.services.agent_orchestrator import CollaborativeOrchestrator
from app.services.chat_service import ChatService
from app.services.privacy_engine import (
    EpsilonBudgetTracker,
    RegexSanitizer,
    ThreeLayerPrivacyDetector,
)


@dataclass
class AppComponents:
    """All application-wide components wired together."""

    settings: Settings
    chat_service: ChatService
    session_store: InMemorySessionStore
    budget_tracker: PrivacyBudgetTracker
    cache: InMemoryCache


def _create_long_term_memory(
    settings: Settings,
) -> MemoryStore | None:
    """Create Qdrant-backed long-term memory if vector store is configured.

    Returns None if Qdrant is not available (graceful degradation).
    """
    try:
        # Use edge LLM for embeddings (Ollama embedding models)
        embedder_client = OpenAICompatibleClient(
            provider=settings.edge_llm.provider,
            model_name=settings.edge_llm.model_name,
            base_url=settings.edge_llm.base_url,
            api_key=settings.edge_llm.api_key,
            temperature=0.0,
            max_tokens=256,
        )
        embedder = LLMEmbedder(embedder_client)
        store = QdrantMemoryStore(
            embedder=embedder,
            settings=settings.vector_store,
        )
        return store
    except Exception as exc:
        import structlog

        logger = structlog.get_logger(__name__)
        logger.warning("long_term_memory_init_failed", error=str(exc))
        return None


def create_components() -> AppComponents:
    """Factory that wires up all application components.

    Called once during app startup (lifespan).
    """
    settings = get_settings()

    # LLM clients
    edge_client = create_edge_llm_client(settings.edge_llm)
    cloud_client = create_cloud_llm_client(settings.cloud_llm)

    # Privacy engine
    # For SLM judge, we use the edge client itself (same Ollama, smaller model)
    slm_client = OpenAICompatibleClient(
        provider="ollama",
        model_name=settings.privacy.slm_model,
        base_url=settings.edge_llm.base_url,
        api_key=settings.edge_llm.api_key,
        temperature=0.1,
        max_tokens=256,
    )
    privacy_detector = ThreeLayerPrivacyDetector(slm_client=slm_client)
    sanitizer = RegexSanitizer()
    budget_tracker = EpsilonBudgetTracker(default_epsilon=settings.privacy.default_epsilon)

    # Memory & session
    session_store = InMemorySessionStore()
    short_term_memory = InMemoryShortTermStore()
    long_term_memory = _create_long_term_memory(settings)
    cache = InMemoryCache()

    # Orchestrator
    orchestrator = CollaborativeOrchestrator(
        settings=settings,
        edge_client=edge_client,
        cloud_client=cloud_client,
        privacy_detector=privacy_detector,
        sanitizer=sanitizer,
        budget_tracker=budget_tracker,
    )

    # Chat service
    chat_service = ChatService(
        orchestrator=orchestrator,
        session_store=session_store,
        short_term_memory=short_term_memory,
        budget_tracker=budget_tracker,
        long_term_memory=long_term_memory,
    )

    return AppComponents(
        settings=settings,
        chat_service=chat_service,
        session_store=session_store,
        budget_tracker=budget_tracker,
        cache=cache,
    )
