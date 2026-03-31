"""应用配置模块。

通过 pydantic-settings 从环境变量或 .env 文件加载配置，
启动时自动校验必填项，缺失时抛出清晰的错误提示。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。

    所有字段从环境变量或 .env 文件读取。
    SecretStr 类型字段在日志/repr 中自动屏蔽敏感值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Azure AI Foundry · DeepSeek-V3 ──────────────────
    azure_endpoint: str
    azure_deployment: str
    azure_api_version: str
    azure_api_key: SecretStr

    # ── 飞书应用配置 ─────────────────────────────────────
    feishu_app_id: str
    feishu_app_secret: SecretStr
    feishu_verification_token: str
    feishu_encrypt_key: str = ""

    # ── 飞书多维表格 ─────────────────────────────────────
    bitable_app_token: str = ""

    # ── 运维配置 ──────────────────────────────────────────
    ops_chat_id: str
    webhook_secret: SecretStr

    # ── 运行环境 ──────────────────────────────────────────
    env: str = "development"
    log_level: str = "INFO"

    # ── 内置常量（不从环境变量读取）──────────────────────
    scheduler_hour: int = 9
    scheduler_minute: int = 30
    rate_limit_interval_ms: int = 100
    max_api_retries: int = 3
    token_refresh_buffer_seconds: int = 300  # 提前 5 分钟刷新


@lru_cache
def get_settings() -> Settings:
    """返回全局 Settings 单例（延迟初始化，线程安全）。"""
    settings = Settings()  # type: ignore[call-arg]
    logging.basicConfig(level=settings.log_level.upper())
    return settings
