"""
Requirements and Research Phase Implementations
================================================

Phases for requirements gathering, historical context, and research.

Memory Integration:
- Graphiti: Project-specific knowledge graph (when enabled)
- VISION: Cross-project learnings, ADRs, patterns from DMI VectorDB
"""

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING

from task_logger import LogEntryType, LogPhase

from .. import requirements, validator
from .models import MAX_RETRIES, PhaseResult

if TYPE_CHECKING:
    pass


class RequirementsPhaseMixin:
    """Mixin for requirements and research phase methods."""

    async def phase_historical_context(self) -> PhaseResult:
        """
        Retrieve historical context from Graphiti knowledge graph AND VISION VectorDB.
        
        Memory sources:
        - Graphiti: Project-specific patterns, gotchas, insights
        - VISION: Cross-project learnings, ADRs, error patterns from DMI
        """
        from graphiti_providers import get_graph_hints, is_graphiti_enabled

        hints_file = self.spec_dir / "graph_hints.json"
        vision_file = self.spec_dir / "vision_context.json"

        # Check if context files already exist
        if hints_file.exists() and vision_file.exists():
            self.ui.print_status("Historical context already available", "success")
            self.task_logger.log(
                "Historical context already available (Graphiti + VISION)",
                LogEntryType.SUCCESS,
                LogPhase.PLANNING,
            )
            return PhaseResult("historical_context", True, [str(hints_file), str(vision_file)], [], 0)

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
        self.ui.print_status("Querying Graphiti + VISION for context...", "progress")
        self.task_logger.log(
            "Searching knowledge graph and VISION VectorDB...",
            LogEntryType.INFO,
            LogPhase.PLANNING,
        )

        graphiti_hints = []
        vision_context = None
        errors = []

        # Define async tasks for parallel execution
        async def get_graphiti():
            if not is_graphiti_enabled():
                return []
            try:
                return await get_graph_hints(
                    query=task_query,
                    project_id=str(self.project_dir),
                    max_results=10,
                )
            except Exception as e:
                errors.append(f"Graphiti: {e}")
                return []

        async def get_vision():
            try:
                from agents.vision_client import get_vision_context_for_phase, is_vision_enabled
                if not is_vision_enabled():
                    return None
                return await get_vision_context_for_phase(
                    task_query,
                    phase="spec",  # Use spec phase namespaces: protocols, learnings, skills
                )
            except ImportError:
                return None
            except Exception as e:
                errors.append(f"VISION: {e}")
                return None

        # Run in parallel
        graphiti_hints, vision_context = await asyncio.gather(
            get_graphiti(),
            get_vision(),
            return_exceptions=False,
        )

        # Save Graphiti hints
        with open(hints_file, "w") as f:
            json.dump(
                {
                    "enabled": is_graphiti_enabled(),
                    "query": task_query,
                    "hints": graphiti_hints or [],
                    "hint_count": len(graphiti_hints) if graphiti_hints else 0,
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        # Save VISION context
        with open(vision_file, "w") as f:
            json.dump(
                {
                    "enabled": vision_context is not None,
                    "query": task_query,
                    "context": vision_context or "",
                    "context_length": len(vision_context) if vision_context else 0,
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        # Report results
        results = []
        if graphiti_hints:
            results.append(f"{len(graphiti_hints)} Graphiti hints")
        if vision_context:
            results.append("VISION context loaded")

        if results:
            self.ui.print_status(f"Retrieved: {', '.join(results)}", "success")
            self.task_logger.log(
                f"Historical context: {', '.join(results)}",
                LogEntryType.SUCCESS,
                LogPhase.PLANNING,
            )
        else:
            self.ui.print_status("No historical context found", "info")

        return PhaseResult(
            "historical_context", 
            True, 
            [str(hints_file), str(vision_file)], 
            errors, 
            0
        )

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
