"""
Requirements and Research Phase Implementations
================================================

Phases for requirements gathering, historical context, and research.

DMI Enhancement: VISION VectorDB integration for historical context.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from task_logger import LogEntryType, LogPhase

from .. import requirements, validator
from .models import MAX_RETRIES, PhaseResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RequirementsPhaseMixin:
    """Mixin for requirements and research phase methods."""

    async def phase_historical_context(self) -> PhaseResult:
        """
        Retrieve historical context from Graphiti AND VISION (DMI Enhancement).
        
        Queries both systems in parallel:
        - Graphiti: Project-specific knowledge graph (sessions, patterns, gotchas)
        - VISION: Global DMI knowledge (learnings, skills, ADRs, error patterns)
        """
        from graphiti_providers import get_graph_hints, is_graphiti_enabled

        hints_file = self.spec_dir / "graph_hints.json"

        if hints_file.exists():
            self.ui.print_status("graph_hints.json already exists", "success")
            self.task_logger.log(
                "Historical context already available",
                LogEntryType.SUCCESS,
                LogPhase.PLANNING,
            )
            return PhaseResult("historical_context", True, [str(hints_file)], [], 0)

        # Get task query
        task_query = self.task_description or ""
        req = requirements.load_requirements(self.spec_dir)
        if req:
            task_query = req.get("task_description", task_query)

        if not task_query:
            self.ui.print_status(
                "No task description for context query, skipping", "warning"
            )
            validator.create_empty_hints(
                self.spec_dir,
                enabled=True,
                reason="No task description available",
            )
            return PhaseResult("historical_context", True, [str(hints_file)], [], 0)

        # Query both Graphiti and VISION in parallel
        graphiti_enabled = is_graphiti_enabled()
        graphiti_hints = []
        vision_context = None
        errors = []

        self.ui.print_status("Querying historical context (Graphiti + VISION)...", "progress")
        self.task_logger.log(
            "Searching knowledge systems for relevant context...",
            LogEntryType.INFO,
            LogPhase.PLANNING,
        )

        # Build parallel tasks
        tasks = []
        
        # Graphiti task (if enabled)
        if graphiti_enabled:
            async def query_graphiti():
                return await get_graph_hints(
                    query=task_query,
                    project_id=str(self.project_dir),
                    max_results=10,
                )
            tasks.append(("graphiti", query_graphiti()))
        
        # VISION task (always try - graceful degradation if not configured)
        async def query_vision():
            try:
                from agents.vision_client import get_vision_context, is_vision_enabled
                if not is_vision_enabled():
                    logger.debug("VISION not enabled, skipping")
                    return None
                # Query with spec-relevant namespaces
                return await get_vision_context(
                    subtask_description=task_query,
                    namespaces=["learnings", "skills", "errors", "protocols"],
                    top_k=5,
                )
            except ImportError:
                logger.debug("VISION client not available")
                return None
            except Exception as e:
                logger.warning(f"VISION query failed: {e}")
                return None
        
        tasks.append(("vision", query_vision()))

        # Execute in parallel
        try:
            results = await asyncio.gather(
                *[t[1] for t in tasks],
                return_exceptions=True
            )
            
            for i, (name, _) in enumerate(tasks):
                result = results[i]
                if isinstance(result, Exception):
                    errors.append(f"{name}: {result}")
                    logger.warning(f"{name} query failed: {result}")
                elif name == "graphiti" and result:
                    graphiti_hints = result
                elif name == "vision" and result:
                    vision_context = result

        except Exception as e:
            errors.append(f"Parallel query failed: {e}")

        # Combine results
        combined_hints = []
        
        # Add Graphiti hints
        if graphiti_hints:
            combined_hints.extend(graphiti_hints)
            self.ui.print_status(f"Graphiti: {len(graphiti_hints)} hints", "success")
        
        # Add VISION context as a hint
        if vision_context:
            combined_hints.append({
                "type": "vision_context",
                "source": "DMI VISION VectorDB",
                "content": vision_context,
                "namespaces": ["learnings", "skills", "errors", "protocols"],
            })
            self.ui.print_status(f"VISION: context retrieved ({len(vision_context)} chars)", "success")
            logger.info(f"VISION context retrieved for spec phase: {len(vision_context)} chars")

        # Save combined hints
        with open(hints_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "enabled": True,
                    "query": task_query,
                    "hints": combined_hints,
                    "hint_count": len(combined_hints),
                    "sources": {
                        "graphiti": len(graphiti_hints) if graphiti_hints else 0,
                        "vision": 1 if vision_context else 0,
                    },
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        # Log summary
        if combined_hints:
            self.task_logger.log(
                f"Found {len(combined_hints)} hints (Graphiti: {len(graphiti_hints) if graphiti_hints else 0}, VISION: {'yes' if vision_context else 'no'})",
                LogEntryType.SUCCESS,
                LogPhase.PLANNING,
            )
        else:
            self.ui.print_status("No historical context found", "info")
            if not graphiti_enabled:
                self.task_logger.log(
                    "Graphiti not configured, VISION queried only",
                    LogEntryType.INFO,
                    LogPhase.PLANNING,
                )

        return PhaseResult("historical_context", True, [str(hints_file)], errors, 0)

    async def phase_requirements(self, interactive: bool = True) -> PhaseResult:
        """Gather requirements from user or task description."""
        requirements_file = self.spec_dir / "requirements.json"

        if requirements_file.exists():
            self.ui.print_status("requirements.json already exists", "success")
            return PhaseResult("requirements", True, [str(requirements_file)], [], 0)

        # Non-interactive mode with task description
        if self.task_description and not interactive:
            req = requirements.create_requirements_from_task(self.task_description)
            requirements.save_requirements(self.spec_dir, req)
            self.ui.print_status(
                "Created requirements.json from task description", "success"
            )
            task_preview = (
                self.task_description[:100] + "..."
                if len(self.task_description) > 100
                else self.task_description
            )
            self.task_logger.log(
                f"Task: {task_preview}",
                LogEntryType.SUCCESS,
                LogPhase.PLANNING,
            )
            return PhaseResult("requirements", True, [str(requirements_file)], [], 0)

        # Interactive mode
        if interactive:
            try:
                self.task_logger.log(
                    "Gathering requirements interactively...",
                    LogEntryType.INFO,
                    LogPhase.PLANNING,
                )
                req = requirements.gather_requirements_interactively(self.ui)

                # Update task description for subsequent phases
                self.task_description = req["task_description"]

                requirements.save_requirements(self.spec_dir, req)
                self.ui.print_status("Created requirements.json", "success")
                return PhaseResult(
                    "requirements", True, [str(requirements_file)], [], 0
                )
            except (KeyboardInterrupt, EOFError):
                print()
                self.ui.print_status("Requirements gathering cancelled", "warning")
                return PhaseResult("requirements", False, [], ["User cancelled"], 0)

        # Fallback: create minimal requirements
        req = requirements.create_requirements_from_task(
            self.task_description or "Unknown task"
        )
        requirements.save_requirements(self.spec_dir, req)
        self.ui.print_status("Created minimal requirements.json", "success")
        return PhaseResult("requirements", True, [str(requirements_file)], [], 0)

    async def phase_research(self) -> PhaseResult:
        """Research external integrations and validate assumptions."""
        research_file = self.spec_dir / "research.json"
        requirements_file = self.spec_dir / "requirements.json"

        if research_file.exists():
            self.ui.print_status("research.json already exists", "success")
            return PhaseResult("research", True, [str(research_file)], [], 0)

        if not requirements_file.exists():
            self.ui.print_status(
                "No requirements.json - skipping research phase", "warning"
            )
            validator.create_minimal_research(
                self.spec_dir,
                reason="No requirements file available",
            )
            return PhaseResult("research", True, [str(research_file)], [], 0)

        errors = []
        for attempt in range(MAX_RETRIES):
            self.ui.print_status(
                f"Running research agent (attempt {attempt + 1})...", "progress"
            )

            context_str = f"""
**Requirements File**: {requirements_file}
**Research Output**: {research_file}

Read the requirements.json to understand what integrations/libraries are needed.
Research each external dependency to validate:
- Correct package names
- Actual API patterns
- Configuration requirements
- Known issues or gotchas

Output your findings to research.json.
"""
            success, output = await self.run_agent_fn(
                "spec_researcher.md",
                additional_context=context_str,
                phase_name="research",
            )

            if success and research_file.exists():
                self.ui.print_status("Created research.json", "success")
                return PhaseResult("research", True, [str(research_file)], [], attempt)

            if success and not research_file.exists():
                validator.create_minimal_research(
                    self.spec_dir,
                    reason="Agent completed but created no findings",
                )
                return PhaseResult("research", True, [str(research_file)], [], attempt)

            errors.append(f"Attempt {attempt + 1}: Research agent failed")

        validator.create_minimal_research(
            self.spec_dir,
            reason="Research agent failed after retries",
        )
        return PhaseResult("research", True, [str(research_file)], errors, MAX_RETRIES)
