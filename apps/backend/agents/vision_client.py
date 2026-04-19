"""
VISION VectorDB Client for Auto-Claude
======================================

Provides semantic search integration with the DMI VISION VectorDB.
Implements ADR-021 RIGID compliant authentication.

Usage:
    from vision_client import get_vision_context_for_phase

    # In async context
    context = await get_vision_context_for_phase("n8n webhook patterns", phase="coding")
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Configuration
# Override via env-var for dev/staging swap (P3-1)
VISION_GATEWAY_URL = os.environ.get(
    "VISION_GATEWAY_URL",
    "https://roger10four.app.n8n.cloud/webhook/vision-context",
)
DEFAULT_NAMESPACE = "learnings"
DEFAULT_TOP_K = 5
REQUEST_TIMEOUT = 30.0  # seconds

# Observable logging marker - grep for "[VISION]" to verify queries
VISION_LOG_PREFIX = "[VISION]"

# Phase-specific namespace mapping (ADR COMPLIANT)
# CRITICAL: All phases include 'protocols' to ensure ADR patterns are available
PHASE_NAMESPACES = {
    "spec": ["protocols", "learnings", "skills"],
    "planning": ["protocols", "learnings", "skills", "errors"],
    "coding": ["protocols", "learnings", "skills", "errors", "templates", "workflows"],
    "qa": ["protocols", "learnings", "errors"],
    "review": ["protocols", "learnings", "errors"],
    "critique": ["protocols", "learnings", "errors"],
    "deploy": ["protocols", "skills"],
    "fixer": ["protocols", "errors", "learnings"],  # Error-focused for self-healing
}


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
        Path(__file__).parent.parent / ".env",  # apps/backend/.env (primary)
        Path(__file__).parent.parent.parent.parent / ".env",  # Auto-Claude root (legacy)
        Path.home() / ".credentials" / "apis" / "dmi" / "dmi-secret.env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            try:
                # P3-2: explicit utf-8 encoding for Windows cross-platform safety
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("DMI_SECRET=") and not line.startswith("#"):
                            secret = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if secret:
                                logger.debug(f"DMI_SECRET loaded from {env_path}")
                                return secret
            except OSError as e:
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
        List of matching documents with scores. Returns empty list on
        any error (fail-open — VISION is optional context enrichment).
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

            # Handle n8n array response format: [{usage: {...}, result: {hits: [...]}}]
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            # Extract hits from Pinecone response structure
            matches = data.get("result", {}).get("hits", []) or data.get("matches", [])

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
    except (httpx.HTTPError, ValueError) as e:
        # ValueError catches JSON decode errors; HTTPError catches
        # connection/network issues not covered by the specific subclasses above.
        logger.warning(f"VISION query error: {e}")
        return []


def _extract_match_fields(item: dict) -> tuple[str, str, float | None]:
    """
    Extract title, content and score from a VISION match item.

    Handles divergent response shapes (Pinecone "fields" vs "metadata"
    vs flat). Returns score as None when truly absent — never fabricates
    `0` as a placeholder (LRN-2026-04-076: silent default sentinels are
    analytics lies).
    """
    fields = item.get("fields", {})
    metadata = item.get("metadata", {})
    content = (
        fields.get("text")
        or metadata.get("content")
        or item.get("content")
        or item.get("text", "")
    )
    title = (
        fields.get("title")
        or metadata.get("title")
        or item.get("title")
        or item.get("_id", "")
    )
    raw_score = item.get("_score")
    if raw_score is None:
        raw_score = item.get("score")
    score = raw_score if isinstance(raw_score, (int, float)) else None
    return title, content, score


def _format_vision_context(
    all_results: dict[str, list[dict]],
    header_extra: str = "",
) -> str:
    """
    Format multi-namespace VISION results into agent-consumable Markdown.

    Single source of truth for VISION context formatting — both the
    legacy `get_vision_context()` and the phase-aware
    `get_vision_context_for_phase()` route through here. Score `None`
    is rendered as `"n/a"` instead of fabricating `0.00` (P2-2 fix).
    """
    sections = ["## VISION Memory Context\n"]
    sections.append(f"_{header_extra}Retrieved from DMI knowledge base:_\n")

    for namespace, results in all_results.items():
        ns_title = namespace.replace("_", " ").title()
        sections.append(f"### {ns_title}\n")

        for item in results:
            title, content, score = _extract_match_fields(item)
            score_str = f"{score:.2f}" if score is not None else "n/a"

            if title:
                sections.append(f"- **{title}** (score: {score_str})")
                if content:
                    truncated = content[:300] + "..." if len(content) > 300 else content
                    sections.append(f"  {truncated}")
            elif content:
                truncated = content[:400] + "..." if len(content) > 400 else content
                sections.append(f"- {truncated} (score: {score_str})")

            sections.append("")

    return "\n".join(sections)


async def _query_namespaces_parallel(
    description: str,
    namespaces: list[str],
    top_k: int,
) -> tuple[dict[str, list[dict]], list[str], list[str]]:
    """
    Query multiple namespaces in parallel via asyncio.gather.

    P2-1 fix: replaces sequential namespace loop. For 5 namespaces with
    30s timeout each, wall-clock drops from up-to-150s to ~30s in the
    worst case.

    Returns:
        (results_per_namespace, successful_namespaces, empty_namespaces)
    """
    tasks = [query_vision(description, namespace=ns, top_k=top_k) for ns in namespaces]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: dict[str, list[dict]] = {}
    successful: list[str] = []
    empty: list[str] = []

    for ns, result in zip(namespaces, raw_results):
        if isinstance(result, Exception):
            logger.debug(f"Namespace {ns} query failed: {result}")
            empty.append(ns)
            continue
        if result:
            all_results[ns] = result
            successful.append(ns)
        else:
            empty.append(ns)

    return all_results, successful, empty


async def get_vision_context(
    subtask_description: str,
    namespaces: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> str | None:
    """
    Get formatted context from VISION for a subtask.

    Generic (non-phase-aware) variant. Most call-sites should prefer
    `get_vision_context_for_phase()` which selects namespaces based on
    pipeline phase (ADR compliant).

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
    all_results, _, _ = await _query_namespaces_parallel(
        subtask_description, namespaces, top_k
    )

    if not all_results:
        logger.debug("No VISION results found")
        return None

    return _format_vision_context(all_results)


async def get_vision_context_for_phase(
    description: str,
    phase: str,
    top_k: int = DEFAULT_TOP_K,
) -> str | None:
    """
    Get phase-appropriate VISION context with ADR compliance.

    All phases include 'protocols' namespace to ensure ADR patterns are
    available. This is the recommended function for phase-specific
    context retrieval.

    Args:
        description: Task/subtask description
        phase: Auto-Claude phase (spec, planning, coding, qa, review,
               critique, deploy, fixer)
        top_k: Results per namespace

    Returns:
        Formatted context string or None if unavailable
    """
    if not is_vision_enabled():
        logger.info(
            f"{VISION_LOG_PREFIX} Query SKIPPED | phase={phase} | "
            f"reason=VISION_NOT_ENABLED"
        )
        return None

    namespaces = PHASE_NAMESPACES.get(phase, ["protocols", "learnings", "skills"])

    logger.info(
        f"{VISION_LOG_PREFIX} Query START | phase={phase} | namespaces={namespaces}"
    )

    all_results, successful, empty = await _query_namespaces_parallel(
        description, namespaces, top_k
    )
    total_results = sum(len(r) for r in all_results.values())

    if not all_results:
        logger.info(
            f"{VISION_LOG_PREFIX} Query END | phase={phase} | "
            f"namespaces_queried={len(namespaces)} | results=0 | "
            f"empty_namespaces={empty}"
        )
        return None

    context = _format_vision_context(all_results, header_extra=f"Phase: {phase} | ")

    logger.info(
        f"{VISION_LOG_PREFIX} Query END | phase={phase} | "
        f"namespaces_queried={len(namespaces)} | results={total_results} | "
        f"context_chars={len(context)} | successful={successful}"
    )

    return context


# Export public interface
__all__ = [
    "get_vision_context",
    "get_vision_context_for_phase",
    "query_vision",
    "is_vision_enabled",
    "PHASE_NAMESPACES",
    "VISION_LOG_PREFIX",
]
