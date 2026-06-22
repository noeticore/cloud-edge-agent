"""Minimal CLI — chat with the agent in terminal.

Usage:
    1. Copy configs/.env.example to .env and fill in CLOUD_LLM_API_KEY
    2. python scripts/cli.py

This CLI reuses the same ChatService as the Web API, ensuring consistent
behavior across both interfaces (privacy detection, tool routing, memory).
"""

import asyncio
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.api.dependencies.deps import create_components
from app.core.logger.logger import get_logger, setup_logging

logger = get_logger("cli")

# ── Ollama auto-start ─────────────────────────────────────────────────────
OLLAMA_API_URL = "http://localhost:11434"
OLLAMA_STARTUP_TIMEOUT = 30  # seconds


def _ollama_installed() -> bool:
    """Check if ollama is on the system PATH."""
    try:
        result = subprocess.run(
            ["where", "ollama"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _ollama_running() -> bool:
    """Check if ollama server is already responding."""
    try:
        resp = httpx.get(f"{OLLAMA_API_URL}/api/tags", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def _ensure_ollama_running() -> bool:
    """Auto-start ollama if installed but not running.

    Returns True if ollama is running (or was started successfully),
    False otherwise.
    """
    if not _ollama_installed():
        print("  ⚠ Ollama not found on PATH. Install from https://ollama.com/download/windows")
        print("    Local LLM will be unavailable; falling back to cloud only.")
        return False

    if _ollama_running():
        print("  ✓ Ollama is already running")
        return True

    print("  ⏳ Starting Ollama in background...")
    try:
        # Start ollama serve in background (detached process)
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0,
        )
    except Exception as exc:
        print(f"  ⚠ Failed to start Ollama: {exc}")
        print("    Run 'scripts/start_local_llm.bat' manually to start it.")
        return False

    # Wait for ollama to be ready
    print(f"  ⏳ Waiting for Ollama to be ready (timeout: {OLLAMA_STARTUP_TIMEOUT}s)...")
    deadline = time.monotonic() + OLLAMA_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _ollama_running():
            print("  ✓ Ollama is now running")
            return True
        time.sleep(1.0)

    print(f"  ⚠ Ollama did not start within {OLLAMA_STARTUP_TIMEOUT}s")
    print("    Run 'scripts/start_local_llm.bat' manually and check for errors.")
    return False

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

    # Auto-start Ollama if installed but not running
    _ensure_ollama_running()

    # Create all components (same as Web API)
    print("  Initializing components...")
    components = await create_components()
    setup_logging(components.settings.log_level)

    chat_service = components.chat_service
    # Unique session per CLI run — avoids loading stale conversation history
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"cli-{ts}-{uuid.uuid4().hex[:4]}"

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
