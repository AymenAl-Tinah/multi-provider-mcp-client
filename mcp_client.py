"""
MCP Client — connects to MCP servers, processes tool calls via any LLM provider.
"""

import json
from typing import Optional, AsyncGenerator
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from providers import BaseProvider, LLMResponse


class MCPClient:
    """Provider-agnostic MCP client."""

    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.provider: Optional[BaseProvider] = None
        self.model: Optional[str] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self.session is not None

    def set_provider(self, provider: BaseProvider, model: str):
        """Set the LLM provider and model."""
        self.provider = provider
        self.model = model

    async def connect_to_server(self, server_script_path: str) -> list[dict]:
        """
        Connect to an MCP server via a script path.
        Auto-detects the command based on file extension.

        Args:
            server_script_path: Path to the server script (.py or .js)

        Returns:
            List of available tool descriptions
        """
        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file. For custom commands use Connect via config.json.")

        command = "python" if is_python else "node"
        return await self.connect_with_command(command, [server_script_path])

    async def connect_with_command(
        self, command: str, args: list[str], env: dict | None = None
    ) -> list[dict]:
        """
        Connect to an MCP server with an explicit command and args.
        Supports arbitrary commands like npx, uvx, docker, etc.

        Args:
            command: The command to run (e.g. "python", "npx", "node")
            args: Arguments for the command
            env: Optional environment variables

        Returns:
            List of available tool descriptions
        """
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()
        self._connected = True

        return await self.list_tools()

    async def list_tools(self) -> list[dict]:
        """Get the list of tools from the connected MCP server."""
        if not self.session:
            return []

        response = await self.session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
            for tool in response.tools
        ]

    def _format_tools_for_llm(self, tools: list[dict]) -> list[dict]:
        """Convert MCP tool list to OpenAI-format tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    def _format_tool_result(self, result) -> str:
        """Convert MCP tool result to string."""
        text_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                text_parts.append(content.text)
            elif hasattr(content, "data"):
                text_parts.append(f"[Binary data: {getattr(content, 'mimeType', 'unknown')}]")
        return "\n".join(text_parts) if text_parts else "Tool executed successfully (no output)"

    async def process_query(
        self,
        query: str,
        conversation_history: list[dict],
    ) -> AsyncGenerator[dict, None]:
        """
        Process a user query with the MCP tools and LLM.

        Yields events:
            {"type": "status", "message": "..."}
            {"type": "tool_call", "name": "...", "arguments": {...}}
            {"type": "tool_result", "name": "...", "result": "..."}
            {"type": "response", "content": "...", "messages": [...]}
            {"type": "error", "message": "..."}
        """
        if not self.provider or not self.model:
            yield {"type": "error", "message": "No provider configured. Please select a provider and enter an API key."}
            return

        # Get available tools
        mcp_tools = await self.list_tools()
        llm_tools = self._format_tools_for_llm(mcp_tools)

        # Build message list
        messages = list(conversation_history)
        messages.append({"role": "user", "content": query})

        max_iterations = 10
        for iteration in range(max_iterations):
            yield {"type": "status", "message": f"Thinking... (step {iteration + 1})"}

            try:
                response: LLMResponse = await self.provider.chat(messages, llm_tools, self.model)
            except Exception as e:
                yield {"type": "error", "message": f"LLM API error: {str(e)}"}
                return

            if not response.has_tool_calls:
                # Final response — no more tool calls
                messages.append({"role": "assistant", "content": response.content or ""})
                yield {
                    "type": "response",
                    "content": response.content or "",
                    "messages": messages,
                }
                return

            # Has tool calls — execute them
            assistant_msg = self.provider.build_assistant_message(response)
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}

                yield {
                    "type": "tool_call",
                    "name": tool_name,
                    "arguments": tool_args,
                }

                try:
                    result = await self.session.call_tool(tool_name, tool_args)
                    result_text = self._format_tool_result(result)
                except Exception as e:
                    result_text = f"Error executing tool: {str(e)}"

                yield {
                    "type": "tool_result",
                    "name": tool_name,
                    "result": result_text,
                }

                tool_msg = self.provider.build_tool_result_message(tc["id"], tool_name, result_text)
                messages.append(tool_msg)

        # Safety: max iterations reached
        messages.append({"role": "assistant", "content": "I've reached the maximum number of tool call iterations."})
        yield {
            "type": "response",
            "content": "I've reached the maximum number of tool call iterations. Please try a simpler query.",
            "messages": messages,
        }

    async def disconnect(self):
        """Disconnect from the current MCP server."""
        if self._connected:
            await self.exit_stack.aclose()
            self.exit_stack = AsyncExitStack()
            self.session = None
            self._connected = False

    async def cleanup(self):
        """Clean up all resources."""
        await self.disconnect()
