"""The model behind the agent: Claude through the Anthropic SDK, or a scripted stand-in (decision D11).

Both return a Turn: why the model stopped, its content as plain dicts (text and tool_use),
and the raw content to append to the conversation unchanged (thinking blocks included).
The scripted model is a first-class backend: tests and recorded demos run without an API
key and give the same result every time.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from app.agent.claims import report_schema

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Turn:
    stop_reason: str
    content: list[dict]
    raw_content: list


class ClaudeModel:
    """Claude Opus 5.5 with the settings in PLAN.md section 3: strict tools, adaptive thinking (always on),
    effort set per route, a structured final report, a cached prefix, and server-side refusal fallbacks."""

    name = "claude"

    def __init__(self, client=None, model: str | None = None, effort: str = "high") -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("VESTLY_MODEL", DEFAULT_MODEL)
        self.effort = effort

    def turn(self, system: str, tools: list[dict], messages: list[dict]) -> Turn:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            tool_choice={"type": "auto"},
            messages=messages,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": report_schema()}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        content = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return Turn(response.stop_reason, content, list(response.content))


Script = Callable[[list[dict], list[dict]], list[dict]]


class ScriptedModel:
    """Plays a script: a function from the conversation so far (and the tool list) to the next turn's blocks.

    A turn with tool_use blocks continues the loop; a turn with only text ends it.
    """

    name = "scripted"

    def __init__(self, script: Script | None = None) -> None:
        from app.agent.scripts import honest_script

        self.script = script or honest_script
        self.calls = 0

    def turn(self, system: str, tools: list[dict], messages: list[dict]) -> Turn:
        blocks = self.script(messages, tools)
        self.calls += 1
        for index, block in enumerate(blocks):
            if block["type"] == "tool_use":
                block.setdefault("id", f"toolu_{self.calls:03d}_{index:02d}")
        if blocks and blocks[0].get("type") == "refusal":
            return Turn("refusal", [], [])
        stop = "tool_use" if any(block["type"] == "tool_use" for block in blocks) else "end_turn"
        return Turn(stop, blocks, blocks)


def default_model():
    """Claude when VESTLY_MODEL_MODE=claude; the scripted model otherwise."""
    if os.environ.get("VESTLY_MODEL_MODE", "scripted") == "claude":
        return ClaudeModel()
    return ScriptedModel()


def tool_results(messages: list[dict]) -> list[dict]:
    """Every tool call so far with its parsed result, in order: {"name", "input", "result"}.

    The scripted model reads the conversation the way Claude would: from the tool results it was sent.
    """
    calls: dict[str, dict] = {}
    found = []
    for message in messages:
        if isinstance(message["content"], str):
            continue
        for block in message["content"]:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if message["role"] == "assistant" and kind == "tool_use":
                calls[block["id"]] = {"name": block["name"], "input": block["input"]}
            elif message["role"] == "user" and kind == "tool_result":
                found.append({**calls.get(block["tool_use_id"], {"name": None, "input": None}), "result": json.loads(block["content"])})
    return found
