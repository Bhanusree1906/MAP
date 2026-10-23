"""Shared invoke/trace loop used by both the CLI and the standalone test script."""

from __future__ import annotations


def as_text(content: str | list) -> str:
    """Gemini returns AIMessage.content as a list of blocks (with signature/
    thought metadata attached), not a plain string - pull out just the text."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


async def run_query(agent, query: str, trace: bool) -> None:
    input_state = {"messages": [{"role": "user", "content": query}]}

    if not trace:
        result = await agent.ainvoke(input_state)
        print(as_text(result["messages"][-1].content))
        return

    # Stream every graph step so a human can watch each model call, tool
    # call, and tool result as it happens instead of only the final answer.
    async for step in agent.astream(input_state, stream_mode="updates"):
        for node_name, node_output in step.items():
            print(f"\n[trace] === node: {node_name} ===")
            messages = node_output.get("messages", []) if isinstance(node_output, dict) else []
            for msg in messages:
                role = getattr(msg, "type", msg.__class__.__name__)
                print(f"[trace] {role}: {getattr(msg, 'content', msg)!r}")
                for tool_call in getattr(msg, "tool_calls", None) or []:
                    print(f"[trace]   -> tool_call: {tool_call['name']}(args={tool_call['args']})")
