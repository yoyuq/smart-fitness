"""Configuration helpers for the Smart Fitness dedicated agent.

Agent-specific environment variables live in fitness_agent/.env.
backend/.env is still loaded first by main.py for global backend settings, then this
file is loaded as an override layer for agent-only provider/model choices.
"""
import os
from pathlib import Path
from typing import Optional

AGENT_DIR = Path(__file__).resolve().parent
AGENT_ENV_PATH = AGENT_DIR / ".env"

_LOADED = False


def load_agent_env(*, override: bool = True) -> Optional[str]:
    """Load fitness_agent/.env if python-dotenv is available.

    Returns the loaded path when present, otherwise None. Existing environment
    variables are overwritten by default so this agent folder is the single place
    to tune agent behavior after process start.
    """
    global _LOADED
    if _LOADED:
        return str(AGENT_ENV_PATH) if AGENT_ENV_PATH.exists() else None
    _LOADED = True
    if not AGENT_ENV_PATH.exists():
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(AGENT_ENV_PATH, override=override)
    except ImportError:
        # main.py already supports plain OS environment fallback.
        return None
    return str(AGENT_ENV_PATH)


def getenv(name: str, default: str = "") -> str:
    load_agent_env()
    return os.environ.get(name, default)
