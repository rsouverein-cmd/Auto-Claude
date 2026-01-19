"""
VISION VectorDB Client for Auto-Claude
======================================

Provides semantic search integration with the DMI VISION VectorDB.
Implements ADR-021 RIGID compliant authentication.

Usage:
    from vision_client import get_vision_context

    # In async context
    context = await get_vision_context("n8n webhook patterns")
"""

import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Configuration
VISION_GATEWAY_URL = "https://roger10four.app.n8n.cloud/webhook/vision-context"
DEFAULT_NAMESPACE = "learnings"
DEFAULT_TOP_K = 5
REQUEST_TIMEOUT = 30.0  # seconds


def _load_dmi_secret() -> str:
    """
    Load DMI_SECRET from environment with security best practices.

    Priority:
    1. Environment variable (production)
    2. .env file in repo root (development)

    Returns empty string if not found (graceful degradation).
    """
    # Try environment variable first
    secret = os.environ.get("DMI_SECRET", "")

    if secret:
        logger.debug("DMI_SECRET loaded from environment")
        return secret

    # Fallback: try loading from .env file
    env_paths = [
        Path(__file__).parent.parent.parent.parent / ".env",  # Auto-Claude root
        Path.home() / ".credentials" / "apis" / "dmi" / "dmi-secret.env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("DMI_SECRET=") and not line.startswith("#"):
                            secret = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if secret:
                                logger.debug(f"DMI_SECRET loaded from {env_path}")
                                return secret
            except Exception as e:
                logger.warning(f"Failed to read {env_path}: {e}")

    logger.warning("DMI_SECRET not found - VISION client will operate without auth")
    return ""


# Load secret at module initialization
_DMI_SECRET = _load_dmi_secret()


def is_vision_enabled() -> bool:
    """Check if VISION integration is available."""
    return bool(_DMI_SECRET)


async def query_vision(
    query: str,
    namespace: str = DEFAULT_NAMESPACE,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """
    Query the VISION VectorDB via n8n Gateway.

    Args:
        query: Search query string
        namespace: VISION namespace (learnings, skills, protocols, etc.)
        top_k: Number of results to return

    Returns:
        List of matching documents with scores

    Raises:
        httpx.HTTPError: On network/API errors
    """
    if not _DMI_SECRET:
        logger.debug("VISION query skipped - no DMI_SECRET configured")
        return []

    headers = {
        "Content-Type": "application/json",
        "x-dmi-secret": _DMI_SECRET,
    }

    payload = {
        "query": query,
        "namespace": namespace,
        "topK": top_k,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                VISION_GATEWAY_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

            data = response.json()
            matches = data.get("matches", [])

            logger.debug(
                f"VISION query returned {len(matches)} results",
                extra={"query": query[:50], "namespace": namespace},
            )
            return matches

    except httpx.TimeoutException:
        logger.warning(f"VISION query timed out: {query[:50]}...")
        return []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            logger.error("VISION authentication failed - check DMI_SECRET")
        else:
            logger.warning(f"VISION query failed: {e.response.status_code}")
        return []
    except Exception as e:
        logger.warning(f"VISION query error: {e}")
        return []


async def get_vision_context(
    subtask_description: str,
    namespaces: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> str | None:
    """
    Get formatted context from VISION for a subtask.

    Queries multiple namespaces and formats results for agent consumption.

    Args:
        subtask_description: Description of the current subtask
        namespaces: List of namespaces to query (default: learnings, skills, protocols)
        top_k: Results per namespace

    Returns:
        Formatted context string or None if unavailable
    """
    if not is_vision_enabled():
        return None

    namespaces = namespaces or ["learnings", "skills", "protocols"]
    all_results: dict[str, list[dict]] = {}

    # Query all namespaces
    for ns in namespaces:
        results = await query_vision(subtask_description, namespace=ns, top_k=top_k)
        if results:
            all_results[ns] = results

    if not all_results:
        logger.debug("No VISION results found")
        return None

    # Format results
    sections = ["## VISION Memory Context\n"]
    sections.append("_Retrieved from DMI knowledge base:_\n")

    for namespace, results in all_results.items():
        ns_title = namespace.replace("_", " ").title()
        sections.append(f"### {ns_title}\n")

        for item in results:
            # Handle different response formats
            content = (
                item.get("metadata", {}).get("content")
                or item.get("content")
                or item.get("text", "")
            )
            title = (
                item.get("metadata", {}).get("title")
                or item.get("title")
                or ""
            )
            score = item.get("score", 0)

            if title:
                sections.append(f"- **{title}** (score: {score:.2f})")
                if content:
                    # Truncate long content
                    truncated = content[:300] + "..." if len(content) > 300 else content
                    sections.append(f"  {truncated}")
            elif content:
                truncated = content[:400] + "..." if len(content) > 400 else content
                sections.append(f"- {truncated} (score: {score:.2f})")

            sections.append("")

    return "\n".join(sections)


# Export public interface
__all__ = [
    "get_vision_context",
    "query_vision",
    "is_vision_enabled",
]
