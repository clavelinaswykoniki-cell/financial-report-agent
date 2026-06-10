from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatModelConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str | None = None


def has_llm_api_key(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> bool:
    return bool(resolve_chat_model_config(model, api_key=api_key, base_url=base_url, provider=provider).api_key)


def resolve_chat_model_config(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> ChatModelConfig:
    raw_model = model or os.getenv("FIN_AGENT_MODEL") or "openai:gpt-4o-mini"
    parsed_provider, model_id = parse_model_id(raw_model)
    provider = provider or parsed_provider

    if provider == "deepseek":
        return ChatModelConfig(
            provider=provider,
            model=model_id,
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("FIN_AGENT_API_KEY"),
            base_url=base_url
            or os.getenv("FIN_AGENT_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com",
        )
    if provider == "openai-compatible":
        return ChatModelConfig(
            provider=provider,
            model=model_id,
            api_key=api_key or os.getenv("FIN_AGENT_API_KEY"),
            base_url=base_url or os.getenv("FIN_AGENT_BASE_URL"),
        )
    return ChatModelConfig(
        provider="openai",
        model=model_id,
        api_key=api_key or os.getenv("OPENAI_API_KEY") or os.getenv("FIN_AGENT_API_KEY"),
        base_url=base_url or os.getenv("FIN_AGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE"),
    )


def parse_model_id(raw_model: str) -> tuple[str, str]:
    if ":" not in raw_model:
        return os.getenv("FIN_AGENT_PROVIDER", "openai"), raw_model
    provider, model_id = raw_model.split(":", 1)
    return provider.strip().lower(), model_id.strip()


def run_langchain_agent(
    snapshot: dict[str, Any],
    risk: dict[str, Any],
    model: str | None = None,
    timeout_seconds: float = 45,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> str | None:
    """Run an optional LangChain LLM pass over deterministic analysis output."""

    config = resolve_chat_model_config(model, api_key=api_key, base_url=base_url, provider=provider)
    if not config.api_key:
        raise RuntimeError(
            "LLM API key is not configured. Set OPENAI_API_KEY, DEEPSEEK_API_KEY, "
            "or FIN_AGENT_API_KEY in .env."
        )

    try:
        from langchain.agents import create_agent
        from langchain.tools import tool
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "LangChain LLM dependencies are not installed. Run `python3 -m pip install -e \".[llm]\"` first."
        ) from exc

    model_kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "timeout": timeout_seconds,
        "max_retries": 1,
    }
    if config.base_url:
        model_kwargs["base_url"] = config.base_url
    model_arg = ChatOpenAI(**model_kwargs)

    snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    risk_json = json.dumps(risk, ensure_ascii=False)

    @tool
    def get_financial_snapshot() -> str:
        """Return normalized SEC financial metrics and company metadata as JSON."""
        return snapshot_json

    @tool
    def get_rule_based_risk() -> str:
        """Return deterministic risk observations and score as JSON."""
        return risk_json

    system_prompt = (
        "你是审慎的财报分析 Agent。你必须先调用工具读取财报事实和规则风险结果，"
        "然后用中文输出简洁的业务解读。不要输出买卖建议、目标价或确定性预测。"
        "明确区分：确认事实、基于财报的推断、需要继续核查的问题。"
    )
    agent = create_agent(
        model=model_arg,
        tools=[get_financial_snapshot, get_rule_based_risk],
        system_prompt=system_prompt,
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"请分析 {snapshot['company_name']} ({snapshot['ticker']}) "
                        "最近年度财报，并给出可放入报告的风险解读。"
                    ),
                }
            ]
        }
    )
    messages = result.get("messages", [])
    if not messages:
        return None
    content = getattr(messages[-1], "content", None)
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)
