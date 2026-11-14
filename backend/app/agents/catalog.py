"""Single model catalog and tool registry shared by squad validation and the LLM layer.

The model list mirrors ``frontend/workflow-lab-server/handler.mjs`` so the Python runtime and the
Workflow Lab accept the same model ids.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

Provider = Literal["anthropic", "openai", "gemini"]

MODELS: dict[str, tuple[Provider, str]] = {
    "gpt-5-mini": ("openai", "GPT-5 Mini"),
    "gpt-5.4-mini": ("openai", "GPT-5.4 Mini"),
    "gpt-5.6-terra": ("openai", "GPT-5.6 Terra"),
    "claude-haiku-5-5": ("anthropic", "Claude Haiku 5.5"),
    "claude-sonnet-5-5": ("anthropic", "Claude Sonnet 5.5"),
    "claude-opus-5-5": ("anthropic", "Claude Opus 5.5"),
    "gemini-3.8-flash": ("gemini", "Gemini 3.8 Flash"),
    "gemini-3.5-flash-lite": ("gemini", "Gemini 3.5 Flash-Lite"),
}

PROVIDER_KEY_ENV: dict[Provider, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def dedicated_key_env(provider: Provider, squad: str, role: str) -> str:
    """``<PROVIDER>_API_KEY_<SQUAD>_<ROLE>``, same pattern as the Workflow Lab."""
    norm = lambda s: re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")
    return f"{PROVIDER_KEY_ENV[provider]}_{norm(squad)}_{norm(role)}"


class Permission(str, Enum):
    READ = "read"                # read files in the agent's draft / read-only office tools
    WRITE_DRAFT = "write_draft"  # change files inside the agent's own draft
    EXECUTE = "execute"          # run commands in the sandbox container
    EXTERNAL = "external"        # reach anything outside the machine (web, Notion, Outlook)


# Every tool an agent may be granted. Tools not listed here are denied.
TOOL_PERMISSIONS: dict[str, Permission] = {
    "list_directory": Permission.READ,
    "grep_search": Permission.READ,
    "read_file_lines": Permission.READ,
    "docling_convert": Permission.READ,
    "word_list": Permission.READ,
    "word_read": Permission.READ,
    "apply_search_replace_diff": Permission.WRITE_DRAFT,
    "write_file": Permission.WRITE_DRAFT,
    "write_note": Permission.WRITE_DRAFT,
    "delete_file": Permission.WRITE_DRAFT,
    "word_create_draft": Permission.WRITE_DRAFT,
    "run_in_sandbox": Permission.EXECUTE,
    "install_system_package": Permission.EXECUTE,
    "browser_navigate": Permission.EXTERNAL,
    "browser_get_text": Permission.EXTERNAL,
    "notion_search": Permission.EXTERNAL,
    "notion_read": Permission.EXTERNAL,
    "notion_append": Permission.EXTERNAL,
    "outlook_list": Permission.EXTERNAL,
    "outlook_create_draft": Permission.EXTERNAL,
}

# Irreversible or outward-facing tools: any agent granted one must list it in hitl_tools,
# so the graph places interrupt_before on it.
ALWAYS_HITL: frozenset[str] = frozenset({"delete_file", "install_system_package", "notion_append"})

# Promotion of a draft into the main workspace is done by the runtime after reviewer APPROVE and
# human approval (DraftManager.promote); no agent is ever granted a merge tool.
SUPERVISOR_PERMISSIONS: frozenset[Permission] = frozenset({Permission.READ})
REVIEWER_PERMISSIONS: frozenset[Permission] = frozenset({Permission.READ, Permission.EXECUTE})
