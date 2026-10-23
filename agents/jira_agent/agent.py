"""Deep agent construction: wires the Gemini chat model, Atlassian MCP tools,
and system prompt together via deepagents.create_deep_agent.
"""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI

from .config import Settings, load_settings
from .mcp_tools import load_atlassian_tools
from .prompts import build_system_prompt


async def build_agent(settings: Settings | None = None):
    settings = settings or load_settings()
    tools = await load_atlassian_tools(settings)

    model = ChatGoogleGenerativeAI(
        model=settings.google_model,
        thinking_budget=settings.thinking_budget,
        max_output_tokens=settings.max_output_tokens,
    )

    return create_deep_agent(
        model=model,
        system_prompt=build_system_prompt(settings.project_key),
        tools=tools,
    )
    
    

