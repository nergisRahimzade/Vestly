"""The tool registry: every tool the agent can call, its strict schema, its seat, and the gate action it needs.

A tool is registered with a Pydantic input model (every field required, no extra fields, so
the schema is strict) and a runner. The registry refuses a runner that is not declared to
return ToolResult, which is how a tool added later is covered by the claim checker without
anyone remembering to wire it in.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import BaseModel, ConfigDict

from app.agent.results import ToolResult

KINDS = ("read", "compute", "gated")
SEATS = ("payroll", "equity")


class ToolInput(BaseModel):
    """Base for tool inputs: no extra fields, so the model cannot slip in an approver or an amount."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    seats: tuple[str, ...]
    # the policy action the gate checks (read_scoped, compute, propose_entry, ...)
    action: str
    description: str
    input_model: type[ToolInput]
    run: Callable

    def schema(self) -> dict:
        """The tool definition sent to the model: strict, every property required, nothing extra."""
        schema = _strict(self.input_model.model_json_schema())
        return {"name": self.name, "description": self.description, "strict": True, "input_schema": schema}


REGISTRY: dict[str, ToolSpec] = {}


def register(name: str, kind: str, seats: tuple[str, ...], action: str, description: str, input_model: type[ToolInput]):
    """Decorator. Refuses a tool whose runner does not promise a ToolResult."""

    def wrap(run: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"Tool {name} is already registered.")
        if kind not in KINDS or not set(seats) <= set(SEATS) or not seats:
            raise ValueError(f"Tool {name} has an unknown kind or seat.")
        if not issubclass(input_model, ToolInput):
            raise TypeError(f"Tool {name}'s input must be a ToolInput.")
        if get_type_hints(run).get("return") is not ToolResult:
            raise TypeError(f"Tool {name} must be declared to return ToolResult.")
        if list(inspect.signature(run).parameters) != ["ctx", "args"]:
            raise TypeError(f"Tool {name}'s runner must take (ctx, args).")
        REGISTRY[name] = ToolSpec(name, kind, tuple(seats), action, description.strip(), input_model, run)
        return run

    return wrap


def tools_for(seat: str) -> list[ToolSpec]:
    """A seat's tools, in a fixed order so the request prefix (and the prompt cache) stays the same."""
    return [spec for name, spec in sorted(REGISTRY.items()) if seat in spec.seats]


def _strict(schema: dict) -> dict:
    """Inline $defs, require every property and forbid extras, as strict tool use needs."""
    definitions = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(dict(definitions[node["$ref"].split("/")[-1]]))
            node = {key: resolve(value) for key, value in node.items() if key not in ("title", "default")}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = sorted(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
