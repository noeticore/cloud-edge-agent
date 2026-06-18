"""API schemas for chat and document endpoints."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Incoming chat request."""

    query: str = Field(..., min_length=1, description="User message")
    session_id: str | None = Field(default=None, description="Session ID for context")


class ChatResponseSchema(BaseModel):
    """Outgoing chat response."""

    answer: str
    session_id: str
    mode: str = Field(description="Collaborate mode used")
    privacy_level: str = Field(description="Detected privacy level (S1/S2/S3)")
    complexity: int = Field(description="Detected complexity level (1-5)")
    latency_ms: float


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "0.1.0"


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str = ""


# --- Document schemas ---


class DocumentUploadRequest(BaseModel):
    """Request to upload a document for RAG ingestion."""

    text: str = Field(..., min_length=1, description="Document text content")
    metadata: dict | None = Field(
        default=None,
        description="Optional metadata (source, title, etc.)",
    )
    doc_id: str | None = Field(
        default=None,
        description="Optional document ID (auto-generated if not provided)",
    )


class DocumentUploadResponse(BaseModel):
    """Response after document ingestion."""

    doc_id: str
    chunk_count: int
    chunk_ids: list[str]


class DocumentSearchResult(BaseModel):
    """A single document search result."""

    content: str
    score: float
    metadata: dict


class DocumentSearchResponse(BaseModel):
    """Response for document search."""

    query: str
    results: list[DocumentSearchResult]
    count: int
