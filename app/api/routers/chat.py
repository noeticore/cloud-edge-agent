"""Chat API router."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas.chat import (
    ChatRequest,
    ChatResponseSchema,
    ErrorResponse,
)
from app.core.exceptions.exceptions import BaseAppException

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponseSchema,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def chat(request: Request, body: ChatRequest) -> ChatResponseSchema:
    """Send a message and get a response from the cloud-edge agent.

    The system automatically:
    - Detects privacy level (S1/S2/S3)
    - Analyzes task complexity (L1-L5)
    - Routes to edge or cloud with appropriate collaborate mode
    """
    chat_service = request.app.state.components.chat_service
    try:
        result = await chat_service.chat(
            query=body.query, session_id=body.session_id
        )
        return ChatResponseSchema(
            answer=result.answer,
            session_id=result.session_id,
            mode=result.mode,
            privacy_level=result.privacy_level,
            complexity=result.complexity,
            latency_ms=result.latency_ms,
        )
    except BaseAppException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    """Stream response tokens via Server-Sent Events (SSE).

    Runs the full orchestrator pipeline (privacy detection, routing, agent
    execution) and streams the final answer token by token.

    Event types:
    - metadata: session info, mode, privacy level, etc.
    - token: individual text chunks
    - done: signals completion
    """
    chat_service = request.app.state.components.chat_service

    async def event_generator():
        try:
            async for chunk in chat_service.chat_stream(
                query=body.query, session_id=body.session_id
            ):
                yield f"data: {chunk}\n\n"
        except BaseAppException as exc:
            error_data = json.dumps({"type": "error", "error": exc.message})
            yield f"data: {error_data}\n\n"
        except Exception as exc:
            error_data = json.dumps({"type": "error", "error": str(exc)})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
