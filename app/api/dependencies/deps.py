"""FastAPI dependency injection — wires up all components.

This module creates singleton instances of all services and provides
them as FastAPI dependencies. Uses lifespan events for initialization.
"""

import structlog
from dataclasses import dataclass

from app.core.config.settings import Settings, get_settings
from app.domain.agent.agent import AgentRole
from app.domain.agent.react_agent import ReActAgent
from app.domain.tool.registry import ToolRegistry
from app.infrastructure.cache.cache import InMemoryCache
from app.infrastructure.database.conversation_store import SQLiteConversationStore
from app.infrastructure.database.mapping_store import SQLiteSanitizationMappingStore
from app.infrastructure.database.session_repository import InMemorySessionStore
from app.infrastructure.llm.client_factory import (
    create_cloud_llm_client,
    create_edge_llm_client,
)
from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient
from app.infrastructure.rag.chunker import FixedSizeChunker
from app.infrastructure.rag.pipeline import RAGPipeline
from app.infrastructure.rag.reranker import LLMReranker
from app.infrastructure.rag.retriever import MemoryRetriever
from app.infrastructure.vectorstore.qdrant_store import QdrantMemoryStore
from app.services.agent_orchestrator import CollaborativeOrchestrator
from app.services.chat_service import ChatService
from app.services.privacy_engine import (
    RegexSanitizer,
    ThreeLayerPrivacyDetector,
)
from tools.calculator_tool import CalculatorTool
from tools.search_tool import SearchTool
from tools.time_tool import TimeTool

logger = structlog.get_logger(__name__)


def _resolve_slm_model(settings: Settings) -> str:
    """Pick the best available model for SLM privacy judge.

    Never returns a cloud model — the SLM judge decides whether data is
    safe to send to cloud, so running it on cloud defeats the purpose.

    Fallback: dedicated SLM model → edge main model.
    """
    slm_model = settings.privacy.slm_model
    edge_model = settings.edge_llm.model_name

    # Check if dedicated SLM model is pulled in Ollama
    try:
        import httpx

        resp = httpx.get(
            settings.edge_llm.base_url.replace("/v1", "/api/tags"),
            timeout=3.0,
        )
        if resp.status_code == 200:
            available = {m["name"] for m in resp.json().get("models", [])}
            if slm_model in available:
                logger.info("slm_dedicated_model_found", model=slm_model)
                return slm_model
            logger.info(
                "slm_model_not_found",
                requested=slm_model,
                fallback=edge_model,
            )
            return edge_model
    except Exception:
        pass

    # If we can't check, assume the dedicated model might not exist,
    # fall back to edge main model which we know works
    logger.info("slm_fallback_to_edge", model=edge_model)
    return edge_model


def _check_edge_available(settings: Settings) -> bool:
    """Check if edge LLM (Ollama) is available."""
    import httpx

    try:
        base_url = settings.edge_llm.base_url
        # Try to hit the Ollama API
        resp = httpx.get(base_url.replace("/v1", "/api/tags"), timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def _check_qdrant_available(settings: Settings) -> bool:
    """Check if Qdrant is available (local or cloud)."""
    import httpx

    try:
        url = settings.vector_store.url
        api_key = settings.vector_store.api_key

        # For cloud Qdrant, just check if URL is configured
        if api_key and url.startswith("https://"):
            logger.info("qdrant_cloud_configured", url=url)
            return True

        # For local Qdrant, try health check
        resp = httpx.get(f"{url}/healthz", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


@dataclass
class AppComponents:
    """All application-wide components wired together."""

    settings: Settings
    chat_service: ChatService
    session_store: InMemorySessionStore
    cache: InMemoryCache
    rag_pipeline: RAGPipeline | None = None


async def _create_rag_pipeline(
    settings: Settings,
) -> RAGPipeline | None:
    """Create RAG pipeline if vector store is available.

    Uses Qdrant as a document store for RAG retrieval.
    Returns None if vector store is not configured (graceful degradation).

    Eagerly initializes the Qdrant connection so the first user request
    doesn't incur connection latency.
    """
    try:
        from app.infrastructure.rag.minilm_embedder import MiniLMEmbedder

        embedder = MiniLMEmbedder()
        # Eagerly load the embedding model — avoids ~9s lag on first query
        embedder._ensure_model()
        logger.info("minilm_preloaded")

        vector_store = QdrantMemoryStore(
            embedder=embedder,
            settings=settings.vector_store,
        )
    except Exception as exc:
        logger.warning("rag_vector_store_init_failed", error=str(exc))
        return None

    # Eagerly connect to Qdrant and verify collection — avoids lag on first request
    await vector_store.initialize()

    try:
        chunker = FixedSizeChunker(chunk_size=512, overlap=64)
        retriever = MemoryRetriever(store=vector_store)

        reranker_client = OpenAICompatibleClient(
            provider=settings.cloud_llm.provider,
            model_name=settings.cloud_llm.model_name,
            base_url=settings.cloud_llm.base_url,
            api_key=settings.cloud_llm.api_key,
            temperature=0.0,
            max_tokens=256,
        )
        reranker = LLMReranker(llm_client=reranker_client)

        pipeline = RAGPipeline(
            chunker=chunker,
            vector_store=vector_store,
            retriever=retriever,
            reranker=reranker,
        )
        logger.info("rag_pipeline_created")
        return pipeline
    except Exception as exc:
        logger.warning("rag_pipeline_init_failed", error=str(exc))
        return None


def _create_tool_registry() -> ToolRegistry:
    """Create a tool registry with all available tools."""
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(TimeTool())
    registry.register(SearchTool())
    return registry


async def create_components() -> AppComponents:
    """Factory that wires up all application components.

    Called once during app startup (lifespan).

    LLM architecture:
    - Edge (local): Ollama API at http://localhost:11434/v1
    - Cloud: DeepSeek API at https://api.deepseek.com/v1
    - Both use the same OpenAICompatibleClient interface
    - If Ollama unavailable, edge falls back to cloud

    Storage architecture:
    - All conversations: SQLite (local only, dual-content)
    - Sanitization mappings: SQLite (cross-session reuse)
    - RAG documents: Qdrant (cloud, for retrieval only)
    """
    settings = get_settings()

    # Check service availability
    edge_available = _check_edge_available(settings)
    qdrant_available = _check_qdrant_available(settings)

    logger.info(
        "service_availability",
        edge_ollama=edge_available,
        qdrant=qdrant_available,
    )

    # === Unified LLM Interface ===
    cloud_client = create_cloud_llm_client(settings.cloud_llm)

    if edge_available:
        edge_client = create_edge_llm_client(settings.edge_llm)
        logger.info("using_edge_llm", model=settings.edge_llm.model_name)
    else:
        edge_client = cloud_client
        logger.warning(
            "edge_unavailable_using_cloud",
            hint="Run 'scripts/start_local_llm.bat' to enable local LLM",
            fallback=settings.cloud_llm.model_name,
        )

    # Tools
    tool_registry = _create_tool_registry()

    # Agents
    edge_agent = ReActAgent(
        llm_client=edge_client,
        tool_registry=tool_registry,
        role=AgentRole.EDGE,
    )
    cloud_agent = ReActAgent(
        llm_client=cloud_client,
        tool_registry=tool_registry,
        role=AgentRole.CLOUD,
    )

    # === Privacy Engine ===
    # SLM judge must NEVER use cloud — it decides if data can go to cloud.
    if edge_available:
        slm_model = _resolve_slm_model(settings)
        slm_client = OpenAICompatibleClient(
            provider="ollama",
            model_name=slm_model,
            base_url=settings.edge_llm.base_url,
            api_key=settings.edge_llm.api_key,
            temperature=0.1,
            max_tokens=256,
        )
        logger.info("slm_client_created", model=slm_model)
    else:
        slm_client = None
        logger.warning("slm_unavailable_edge_down")

    privacy_detector = ThreeLayerPrivacyDetector(slm_client=slm_client)

    # Sanitization mapping store (cross-session reuse)
    db_path = "data/local_memory.db"
    mapping_store = SQLiteSanitizationMappingStore(db_path=db_path)
    sanitizer = RegexSanitizer(mapping_store=mapping_store)
    logger.info("sanitizer_initialized", db_path=db_path)

    # === Storage ===
    session_store = InMemorySessionStore()
    cache = InMemoryCache()

    # Conversation store (all conversations, dual-content)
    conversation_store = SQLiteConversationStore(db_path=db_path)
    logger.info("conversation_store_initialized", db_path=db_path)

    # RAG pipeline (Qdrant for document retrieval only)
    rag_pipeline = None
    if qdrant_available:
        rag_pipeline = await _create_rag_pipeline(settings)
        logger.info("rag_pipeline_enabled")
    else:
        logger.warning("qdrant_unavailable_rag_disabled")

    # === Orchestrator ===
    orchestrator = CollaborativeOrchestrator(
        edge_client=edge_client,
        cloud_client=cloud_client,
        edge_agent=edge_agent,
        cloud_agent=cloud_agent,
        privacy_detector=privacy_detector,
        sanitizer=sanitizer,
        edge_available=edge_available,
    )

    # === Chat Service ===
    chat_service = ChatService(
        orchestrator=orchestrator,
        session_store=session_store,
        conversation_store=conversation_store,
        rag_pipeline=rag_pipeline,
    )

    return AppComponents(
        settings=settings,
        chat_service=chat_service,
        session_store=session_store,
        cache=cache,
        rag_pipeline=rag_pipeline,
    )
