"""LangChain tools for reading/writing files inside a live sandbox session.

Each tool needs an already-open SandboxManager (entered via `with
SandboxManager(...) as sandbox:`) - build_file_tools binds the tools to that
one session so every read/write lands in the same container.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from sandbox.manager import SandboxManager


def build_file_tools(sandbox: SandboxManager) -> list[BaseTool]:
    @tool
    def read_file(path: str) -> str:
        """Read a file from the repository inside the sandbox.

        Use this when you need to inspect existing source code,
        configuration files, documentation, or tests.
        """
        result = sandbox.read_file(path)
        if not result["success"]:
            return f"Failed to read file:\n{result['error']}"
        return result["content"]

    @tool
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file inside the repository sandbox.

        Use this when you need to create a new file or modify
        an existing file.
        """
        result = sandbox.write_file(path=path, content=content)
        if not result["success"]:
            return f"Failed to write file:\n{result['error']}"
        return result["message"]

    return [read_file, write_file]
