"""Documents API router — upload and search documents for RAG."""

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.chat import (
    DocumentSearchResponse,
    DocumentSearchResult,
    DocumentUploadRequest,
    DocumentUploadResponse,
    ErrorResponse,
)
from app.core.exceptions.exceptions import BaseAppException

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentUploadResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def upload_document(
    request: Request, body: DocumentUploadRequest
) -> DocumentUploadResponse:
    """Upload a text document for RAG ingestion.

    The document is:
    1. Split into chunks (configurable size/overlap)
    2. Each chunk is embedded and stored in the vector database
    3. Returns chunk IDs for reference
    """
    rag_pipeline = request.app.state.components.rag_pipeline
    if rag_pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="RAG pipeline not available (vector store not configured)",
        )

    try:
        chunk_ids = await rag_pipeline.ingest(
            text=body.text,
            metadata=body.metadata,
            doc_id=body.doc_id,
        )
        return DocumentUploadResponse(
            doc_id=body.doc_id or "auto",
            chunk_count=len(chunk_ids),
            chunk_ids=chunk_ids,
        )
    except BaseAppException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/search",
    response_model=DocumentSearchResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def search_documents(
    request: Request,
    query: str,
    top_k: int = 5,
) -> DocumentSearchResponse:
    """Search ingested documents by semantic similarity.

    Args:
        query: search query.
        top_k: maximum number of results to return.
    """
    rag_pipeline = request.app.state.components.rag_pipeline
    if rag_pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="RAG pipeline not available (vector store not configured)",
        )

    try:
        results = await rag_pipeline.retrieve(query, top_k=top_k)
        return DocumentSearchResponse(
            query=query,
            results=[
                DocumentSearchResult(
                    content=r.document.content,
                    score=r.score,
                    metadata=r.document.metadata,
                )
                for r in results
            ],
            count=len(results),
        )
    except BaseAppException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
