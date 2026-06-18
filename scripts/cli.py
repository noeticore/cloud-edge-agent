"""Minimal CLI — chat with the agent in terminal.

Usage:
    1. Copy configs/.env.example to .env and fill in CLOUD_LLM_API_KEY
    2. python scripts/cli.py

This CLI reuses the same ChatService as the Web API, ensuring consistent
behavior across both interfaces (privacy detection, tool routing, memory).
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.api.dependencies.deps import create_components
from app.core.logger.logger import get_logger, setup_logging

logger = get_logger("cli")

# ── Banner ──────────────────────────────────────────────────────────────
BANNER = r"""
  ╔═══════════════════════════════════════════════════╗
  ║   CloudEdgeAgent — CLI Interface                  ║
  ║   Privacy-First Cloud-Edge Collaborative Agent    ║
  ╚═══════════════════════════════════════════════════╝

  Type your message and press Enter.
  Type 'quit' or 'exit' to leave.
"""


async def main() -> None:
    """Interactive chat loop using ChatService."""
    # Load .env from project root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"  ✓ Loaded .env from {env_path}")
    else:
        print(f"  ⚠ No .env found at {env_path}")
        print("  Copy configs/.env.example to .env and fill in CLOUD_LLM_API_KEY")
        return

    # Create all components (same as Web API)
    print("  Initializing components...")
    components = create_components()
    setup_logging(components.settings.log_level)

    chat_service = components.chat_service
    session_id = "cli-session"

    print(f"  ✓ Edge LLM: {components.settings.edge_llm.provider} / {components.settings.edge_llm.model_name}")
    print(f"  ✓ Cloud LLM: {components.settings.cloud_llm.provider} / {components.settings.cloud_llm.model_name}")
    print(f"  ✓ Session: {session_id}")

    if components.rag_pipeline:
        print("  ✓ RAG pipeline: enabled")
    else:
        print("  ⚠ RAG pipeline: disabled (no vector store)")

    print(BANNER)

    while True:
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("  Bye!")
            break

        # Run through ChatService (same as Web API)
        print("  Thinking...")
        try:
            result = await chat_service.chat(
                query=user_input, session_id=session_id
            )

            # Show answer
            print(f"  Agent: {result.answer}")
            print(
                f"  [{result.latency_ms}ms | mode={result.mode} | "
                f"privacy={result.privacy_level} | complexity=L{result.complexity}]"
            )
            print()

        except Exception as exc:
            print(f"  Error: {exc}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
