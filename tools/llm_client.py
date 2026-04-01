"""LLM 客户端模块。

通过 lru_cache 保证全局共享单一 AzureChatOpenAI 实例，
所有 LLM 节点通过 get_llm() 获取，避免重复初始化。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import AzureChatOpenAI


@lru_cache
def get_llm() -> AzureChatOpenAI:
    """返回 AzureChatOpenAI 全局单例。

    使用 lru_cache 确保整个应用生命周期内只创建一个实例。
    """
    from config import get_settings

    settings = get_settings()
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_endpoint,
        azure_deployment=settings.azure_deployment,
        api_version=settings.azure_api_version,
        api_key=settings.azure_api_key.get_secret_value(),
    )
