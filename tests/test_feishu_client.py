"""feishu_client 模块单元测试。

覆盖：TokenManager、RateLimiter、重试装饰器、
FeishuClient 各 API 方法。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.feishu_client import (
    FeishuAPIError,
    FeishuRateLimitError,
    RateLimiter,
    TokenManager,
    with_retry,
)

# ── TokenManager 测试 ─────────────────────────────────────


class TestTokenManager:
    """TokenManager 自动刷新逻辑测试。"""

    async def test_get_token_triggers_refresh_on_first_call(
        self,
    ) -> None:
        """首次调用时应触发 token 刷新。"""
        mgr = TokenManager("app_id", "app_secret")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "tenant_access_token": "token_abc",
            "expire": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            token = await mgr.get_token()

        assert token == "token_abc"

    async def test_get_token_uses_cached_token(
        self,
    ) -> None:
        """token 未过期时应复用缓存，不重复刷新。"""
        mgr = TokenManager("app_id", "app_secret")
        mgr._access_token = "cached_token"
        mgr._expires_at = time.time() + 7000  # 未过期

        token = await mgr.get_token()

        assert token == "cached_token"

    async def test_get_token_refreshes_before_expiry(
        self,
    ) -> None:
        """token 即将过期（提前 300s）时应自动刷新。"""
        mgr = TokenManager("app_id", "app_secret", refresh_buffer=300)
        mgr._access_token = "old_token"
        # 距过期不足 300s
        mgr._expires_at = time.time() + 100

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "tenant_access_token": "new_token",
            "expire": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            token = await mgr.get_token()

        assert token == "new_token"

    async def test_refresh_raises_on_api_error(
        self,
    ) -> None:
        """飞书 API 返回错误码时应抛出 FeishuAPIError。"""
        mgr = TokenManager("app_id", "app_secret")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 10003,
            "msg": "app_secret invalid",
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(FeishuAPIError):
                await mgr._refresh()


# ── RateLimiter 测试 ──────────────────────────────────────


class TestRateLimiter:
    """RateLimiter 速率控制测试。"""

    async def test_first_call_no_wait(self) -> None:
        """首次调用不应等待。"""
        limiter = RateLimiter(min_interval_ms=100)
        start = time.monotonic()
        await limiter.acquire("test_endpoint")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # 首次不等待

    async def test_second_call_respects_interval(
        self,
    ) -> None:
        """短时间内第二次调用应等待满足间隔。"""
        limiter = RateLimiter(min_interval_ms=100)
        await limiter.acquire("test_endpoint")
        start = time.monotonic()
        await limiter.acquire("test_endpoint")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.09  # 至少等待约 100ms

    async def test_different_endpoints_independent(
        self,
    ) -> None:
        """不同接口 key 的限流相互独立。"""
        limiter = RateLimiter(min_interval_ms=200)
        await limiter.acquire("endpoint_a")
        start = time.monotonic()
        # endpoint_b 首次调用，不应等待
        await limiter.acquire("endpoint_b")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05


# ── with_retry 装饰器测试 ─────────────────────────────────


class TestWithRetry:
    """with_retry 指数退避重试测试。"""

    async def test_no_retry_on_success(self) -> None:
        """调用成功时不触发重试。"""
        call_count = 0

        @with_retry(max_retries=3)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_rate_limit(self) -> None:
        """遇到限流错误时应重试，成功后停止。"""
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise FeishuRateLimitError("429")
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 3

    async def test_raises_after_max_retries(
        self,
    ) -> None:
        """超过最大重试次数后应抛出异常。"""

        @with_retry(max_retries=2, base_delay=0.01)
        async def func() -> None:
            raise FeishuRateLimitError("429")

        with pytest.raises(FeishuRateLimitError):
            await func()

    async def test_non_rate_limit_error_not_retried(
        self,
    ) -> None:
        """非限流错误不应重试，直接抛出。"""
        call_count = 0

        @with_retry(max_retries=3)
        async def func() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("unexpected error")

        with pytest.raises(ValueError):
            await func()

        assert call_count == 1
