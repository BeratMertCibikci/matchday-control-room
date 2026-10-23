"""Chat clients for the agent crew.

MCR_LLM selects the backend:
  offline       deterministic template client (default; no cloud needed)
  foundry       Microsoft Foundry project (FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL), Entra ID auth
  azure_openai  Azure OpenAI deployment (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_KEY)
"""

from __future__ import annotations

import json
import os
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message

from mcr.agents import templates


class OfflineChatClient(BaseChatClient):
    """Answers crew prompts with grounded template text. Keeps the Agent Framework pipeline intact
    so swapping to a real model is a configuration change, not a code change."""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs: Any) -> ChatResponse:
        payload = json.loads(messages[-1].text)
        return ChatResponse(messages=Message("assistant", [render_offline(payload)]), model="offline-templates")


def render_offline(p: dict) -> str:
    task = p["task"]
    if task == "explain":
        return templates.explain(p["moment_type"], p["facts"], "en")
    if task == "narrate":
        return templates.narrate(p["moment_type"], p["facts"], p["language"])
    if task == "personas":
        return json.dumps(templates.personas(
            p["moment_type"], p["facts"], templates.explain(p["moment_type"], p["facts"], p["language"]),
            p["language"], p["home"], p["away"], p.get("team_is_home"),
        ), ensure_ascii=False)
    if task == "recap":
        return json.dumps(templates.recap(p["kind"], p["facts"], p["moments"], p["language"], p["home"], p["away"]),
                          ensure_ascii=False)
    if task == "ask":
        return p.get("offline_answer", "")
    raise ValueError(f"unknown task {task}")


def backend_name() -> str:
    return os.getenv("MCR_LLM", "offline").lower()


def build_chat_client():
    backend = backend_name()
    if backend == "foundry":
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential

        return FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.getenv("FOUNDRY_MODEL", "gpt-4.1-mini"),
            credential=DefaultAzureCredential(),
        )
    if backend == "azure_openai":
        from agent_framework.openai import OpenAIChatCompletionClient

        return OpenAIChatCompletionClient(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OfflineChatClient()
