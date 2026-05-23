"""
LLM Provider Abstraction Layer
Supports: Anthropic, OpenAI, Gemini, OpenRouter, DeepSeek, Qwen
"""

import json
import uuid
from abc import ABC, abstractmethod
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI


# ─── Provider Registry ──────────────────────────────────────────────────────────

PROVIDER_REGISTRY = {
    "anthropic": {
        "name": "Anthropic",
        "models": [
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        "default_model": "claude-sonnet-4-20250514",
        "env_key": "ANTHROPIC_API_KEY",
        "icon": "anthropic",
    },
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"],
        "default_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "icon": "openai",
        "base_url": None,
    },
    "gemini": {
        "name": "Gemini",
        "models": [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        "default_model": "gemini-2.0-flash",
        "env_key": "GEMINI_API_KEY",
        "icon": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    "openrouter": {
        "name": "OpenRouter",
        "models": [
            "anthropic/claude-sonnet-4-20250514",
            "openai/gpt-4o",
            "google/gemini-2.0-flash-exp",
            "meta-llama/llama-3.1-405b-instruct",
            "deepseek/deepseek-chat",
        ],
        "default_model": "anthropic/claude-sonnet-4-20250514",
        "env_key": "OPENROUTER_API_KEY",
        "icon": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "deepseek": {
        "name": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "icon": "deepseek",
        "base_url": "https://api.deepseek.com",
    },
    "qwen": {
        "name": "Qwen",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "default_model": "qwen-plus",
        "env_key": "QWEN_API_KEY",
        "icon": "qwen",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    },
}


# ─── Normalized Response ────────────────────────────────────────────────────────

class LLMResponse:
    """Normalized response from any LLM provider."""

    def __init__(self, content: str | None, tool_calls: list | None, raw: dict | None = None):
        self.content = content
        self.tool_calls = tool_calls or []  # [{"id", "name", "arguments"}]
        self.raw = raw

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ─── Base Provider ──────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    """Abstract LLM provider."""

    @abstractmethod
    async def chat(self, messages: list, tools: list, model: str) -> LLMResponse:
        """
        Send messages + tools to the LLM and return a normalized response.

        Args:
            messages: List of message dicts in internal format
            tools: List of tool dicts in OpenAI format
            model: Model name string
        """
        ...

    @abstractmethod
    def build_assistant_message(self, response: LLMResponse) -> dict:
        """Build an assistant message dict from an LLM response for history."""
        ...

    @abstractmethod
    def build_tool_result_message(self, tool_call_id: str, tool_name: str, result_text: str) -> dict:
        """Build a tool result message dict for history."""
        ...


# ─── Anthropic Provider ─────────────────────────────────────────────────────────

class AnthropicProvider(BaseProvider):
    """Uses the Anthropic SDK (unique message format)."""

    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    def _convert_tools(self, tools: list) -> list:
        """Convert OpenAI-format tools to Anthropic format."""
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", tool)
            anthropic_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools

    def _convert_messages(self, messages: list) -> list:
        """Convert internal message format to Anthropic format."""
        anthropic_msgs = []
        for msg in messages:
            role = msg["role"]

            if role == "system":
                continue  # handled separately

            if role == "user":
                anthropic_msgs.append({"role": "user", "content": msg["content"]})

            elif role == "assistant":
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    tc_func = tc.get("function", tc)
                    arguments = tc_func.get("arguments", {})
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", str(uuid.uuid4())),
                        "name": tc_func["name"],
                        "input": arguments,
                    })
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})

            elif role == "tool":
                # Anthropic expects tool results as user messages
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }
                # Check if previous message is already a user message with tool results
                if anthropic_msgs and anthropic_msgs[-1]["role"] == "user" and isinstance(anthropic_msgs[-1]["content"], list):
                    anthropic_msgs[-1]["content"].append(tool_result_block)
                else:
                    anthropic_msgs.append({"role": "user", "content": [tool_result_block]})

        return anthropic_msgs

    async def chat(self, messages: list, tools: list, model: str) -> LLMResponse:
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
        anthropic_msgs = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": anthropic_msgs,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        if system_msg:
            kwargs["system"] = system_msg

        response = await self.client.messages.create(**kwargs)

        # Normalize
        content = None
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content = (content or "") + block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return LLMResponse(content=content, tool_calls=tool_calls, raw=response)

    def build_assistant_message(self, response: LLMResponse) -> dict:
        msg = {"role": "assistant", "content": response.content or ""}
        if response.has_tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]) if isinstance(tc["arguments"], dict) else tc["arguments"],
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    def build_tool_result_message(self, tool_call_id: str, tool_name: str, result_text: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result_text,
        }


# ─── OpenAI-Compatible Provider ─────────────────────────────────────────────────

class OpenAICompatibleProvider(BaseProvider):
    """Works with OpenAI, Gemini, OpenRouter, DeepSeek, Qwen (all OpenAI-compatible)."""

    def __init__(self, api_key: str, base_url: str | None = None):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)

    async def chat(self, messages: list, tools: list, model: str) -> LLMResponse:
        # Prepare messages (filter out None content for assistant messages without text)
        clean_messages = []
        for msg in messages:
            m = dict(msg)
            # Convert arguments to string if dict
            if "tool_calls" in m:
                m["tool_calls"] = [
                    {
                        **tc,
                        "function": {
                            **tc["function"],
                            "arguments": (
                                json.dumps(tc["function"]["arguments"])
                                if isinstance(tc["function"]["arguments"], dict)
                                else tc["function"]["arguments"]
                            ),
                        },
                    }
                    for tc in m["tool_calls"]
                ]
            clean_messages.append(m)

        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": clean_messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        content = choice.message.content
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                arguments = tc.function.arguments
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": arguments,
                })

        return LLMResponse(content=content, tool_calls=tool_calls, raw=response)

    def build_assistant_message(self, response: LLMResponse) -> dict:
        msg = {"role": "assistant", "content": response.content or ""}
        if response.has_tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]) if isinstance(tc["arguments"], dict) else tc["arguments"],
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    def build_tool_result_message(self, tool_call_id: str, tool_name: str, result_text: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result_text,
        }


# ─── Factory ────────────────────────────────────────────────────────────────────

def create_provider(provider_name: str, api_key: str) -> BaseProvider:
    """Create a provider instance by name."""
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key)

    registry = PROVIDER_REGISTRY.get(provider_name)
    if not registry:
        raise ValueError(f"Unknown provider: {provider_name}")

    base_url = registry.get("base_url")
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)
