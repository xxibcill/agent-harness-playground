from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable

from agent_harness_contracts import RunRecord

from agent_harness_core.workflows.anthropic import build_anthropic_workflow
from agent_harness_core.workflows.demo_echo import create_demo_echo_workflow
from agent_harness_core.workflows.types import WorkflowDefinition


class UnknownWorkflowError(LookupError):
    """Raised when a run references a workflow that is not registered."""


@dataclass
class WorkflowRegistry:
    _workflows: dict[str, WorkflowDefinition | Callable[..., WorkflowDefinition]]

    def get(self, workflow_name: str, run: RunRecord | None = None) -> WorkflowDefinition:
        workflow = self._workflows.get(workflow_name)
        if workflow is None:
            raise UnknownWorkflowError(f"Unknown workflow: {workflow_name}")
        if callable(workflow):
            parameter_count = len(inspect.signature(workflow).parameters)
            if parameter_count == 0:
                return workflow()
            return workflow(run)
        return workflow

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._workflows))


def build_default_workflow_registry() -> WorkflowRegistry:
    return WorkflowRegistry(
        {
            "demo.echo": create_demo_echo_workflow(),
            "anthropic.respond": build_anthropic_workflow,
        }
    )
