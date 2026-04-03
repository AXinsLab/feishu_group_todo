"""飞书 API 客户端。

包含：
- TokenManager：自动刷新 tenant_access_token（异步锁保护）
- RateLimiter：接口级最小请求间隔控制（100ms）
- with_retry：指数退避重试装饰器（最多 3 次）
- FeishuClient：飞书 Open API 封装
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Any, Callable

import httpx
import lark_oapi as lark

logger = logging.getLogger(__name__)

# ── 自定义异常 ────────────────────────────────────────────


class FeishuAPIError(Exception):
    """飞书 API 调用失败。"""


class FeishuRateLimitError(FeishuAPIError):
    """飞书 API 触发限流（429）。"""


# ── 重试装饰器 ────────────────────────────────────────────


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Callable:  # type: ignore[type-arg]
    """指数退避重试装饰器。

    仅对 FeishuRateLimitError 重试，其他异常直接抛出。

    Args:
        max_retries: 最大重试次数（不含首次调用）。
        base_delay: 首次重试等待秒数，后续 2^n 倍增。
    """

    def decorator(func: Callable) -> Callable:  # type: ignore[type-arg]
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except FeishuRateLimitError:
                    if attempt == max_retries:
                        raise
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "Rate limited, retry %d/%d in %.1fs",
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
            return None  # unreachable

        return wrapper

    return decorator


# ── TokenManager ─────────────────────────────────────────


class TokenManager:
    """飞书 tenant_access_token 自动刷新管理器。

    有效期 2 小时，提前 refresh_buffer 秒刷新。
    使用 asyncio.Lock 防止并发重复刷新。
    """

    _TOKEN_URL = (
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    )

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        refresh_buffer: int = 300,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._refresh_buffer = refresh_buffer
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """返回有效的 access_token，必要时自动刷新。"""
        async with self._lock:
            if time.time() >= self._expires_at - self._refresh_buffer:
                await self._refresh()
            return self._access_token

    async def _refresh(self) -> None:
        """向飞书请求新 token 并更新本地状态。"""
        payload = {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self._TOKEN_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(f"Token refresh failed: {data.get('msg')}")

        self._access_token = data["tenant_access_token"]
        # 飞书返回剩余有效秒数
        self._expires_at = time.time() + data.get("expire", 7200)
        logger.debug("Token refreshed, expires in %ds", data.get("expire"))


# ── RateLimiter ──────────────────────────────────────────


class RateLimiter:
    """接口级请求速率限制器。

    保证同一接口相邻两次调用间隔不低于 min_interval_ms 毫秒。
    """

    def __init__(self, min_interval_ms: int = 100) -> None:
        self._min_interval = min_interval_ms / 1000.0
        self._last_call: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, endpoint_key: str) -> None:
        """等待直到满足最小请求间隔。

        Args:
            endpoint_key: 接口唯一标识（如方法名）。
        """
        async with self._lock:
            last = self._last_call.get(endpoint_key, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call[endpoint_key] = time.monotonic()


# ── FeishuClient ─────────────────────────────────────────


class FeishuClient:
    """飞书 Open API 封装。

    所有方法均为异步，内置限流和重试。
    """

    def __init__(self, settings: Any) -> None:
        self._token_mgr = TokenManager(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret.get_secret_value(),
            refresh_buffer=settings.token_refresh_buffer_seconds,
        )
        self._rate_limiter = RateLimiter(
            min_interval_ms=settings.rate_limit_interval_ms
        )
        self._lark_client = (
            lark.Client.builder()
            .app_id(settings.feishu_app_id)
            .app_secret(settings.feishu_app_secret.get_secret_value())
            .build()
        )

    async def _get_headers(self) -> dict[str, str]:
        """构造带 token 的请求头。"""
        token = await self._token_mgr.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @with_retry(max_retries=3)
    async def get_group_messages(
        self,
        group_id: str,
        start_time: int,
        end_time: int,
    ) -> list[dict]:
        """拉取群历史消息。

        Args:
            group_id: 群 ID（chat_id）。
            start_time: 开始时间戳（毫秒）。
            end_time: 结束时间戳（毫秒）。

        Returns:
            消息列表，每条包含 message_id、sender_open_id、
            text、create_time 字段。
        """
        await self._rate_limiter.acquire("get_group_messages")
        headers = await self._get_headers()
        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        params = {
            "container_id_type": "chat",
            "container_id": group_id,
            "start_time": str(start_time // 1000),
            "end_time": str(end_time // 1000),
            "page_size": 50,
        }
        messages: list[dict] = []
        page_token: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 429:
                    raise FeishuRateLimitError(
                        "Rate limited on get_group_messages"
                    )
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 0:
                    raise FeishuAPIError(
                        f"get_group_messages failed: {data.get('msg')}"
                    )

                items = data.get("data", {}).get("items", [])
                for item in items:
                    body_content = item.get("body", {}).get("content", "{}")
                    try:
                        content = json.loads(body_content)
                        text = content.get("text", "")
                    except (json.JSONDecodeError, TypeError):
                        text = body_content

                    messages.append(
                        {
                            "message_id": item.get("message_id", ""),
                            "sender_open_id": item.get("sender", {}).get(
                                "id", ""
                            ),
                            "sender_type": item.get("sender", {}).get(
                                "sender_type", "user"
                            ),
                            "text": text,
                            "create_time": item.get("create_time", ""),
                        }
                    )

                has_more = data.get("data", {}).get("has_more", False)
                if not has_more:
                    break
                page_token = data.get("data", {}).get("page_token")

        return messages

    @with_retry(max_retries=3)
    async def get_group_members(self, group_id: str) -> list[dict]:
        """拉取群成员列表。

        Args:
            group_id: 群 ID（chat_id）。

        Returns:
            成员列表，每条包含 open_id、name、
            en_name、nickname 字段。
        """
        await self._rate_limiter.acquire("get_group_members")
        headers = await self._get_headers()
        url = (
            f"https://open.feishu.cn/open-apis/im/v1/chats/{group_id}/members"
        )
        members: list[dict] = []
        page_token: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict[str, Any] = {
                    "member_id_type": "open_id",
                    "page_size": 100,
                }
                if page_token:
                    params["page_token"] = page_token

                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 429:
                    raise FeishuRateLimitError(
                        "Rate limited on get_group_members"
                    )
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 0:
                    raise FeishuAPIError(
                        f"get_group_members failed: {data.get('msg')}"
                    )

                items = data.get("data", {}).get("items", [])
                for item in items:
                    members.append(
                        {
                            "open_id": item.get("member_id", ""),
                            "name": item.get("name", ""),
                            "en_name": item.get("en_name", ""),
                            "nickname": item.get("nickname", ""),
                        }
                    )

                has_more = data.get("data", {}).get("has_more", False)
                if not has_more:
                    break
                page_token = data.get("data", {}).get("page_token")

        return members

    @with_retry(max_retries=3)
    async def send_message(
        self,
        receive_id: str,
        content: str,
        msg_type: str = "text",
        reply_to_message_id: str | None = None,
    ) -> bool:
        """发送消息到群或用户。

        Args:
            receive_id: 接收方 ID（chat_id 或 open_id）。
            content: 消息内容（text 类型为 JSON 字符串）。
            msg_type: 消息类型，text 或 interactive（卡片）。
            reply_to_message_id: 引用回复的消息 ID。

        Returns:
            发送成功返回 True。
        """
        await self._rate_limiter.acquire("send_message")
        headers = await self._get_headers()

        if reply_to_message_id:
            url = (
                f"https://open.feishu.cn/open-apis/im/v1"
                f"/messages/{reply_to_message_id}/reply"
            )
            payload = {
                "content": content,
                "msg_type": msg_type,
            }
        else:
            url = "https://open.feishu.cn/open-apis/im/v1/messages"
            payload = {
                "receive_id": receive_id,
                "content": content,
                "msg_type": msg_type,
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"receive_id_type": "chat_id"} if not reply_to_message_id else {}
            resp = await client.post(url, headers=headers, json=payload, params=params)
            if resp.status_code == 429:
                raise FeishuRateLimitError("Rate limited on send_message")
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(f"send_message failed: {data.get('msg')}")
        return True

    @with_retry(max_retries=3)
    async def create_bitable(self, name: str) -> str:
        """创建飞书多维表格。

        Args:
            name: 多维表格名称。

        Returns:
            新建多维表格的 app_token。
        """
        await self._rate_limiter.acquire("create_bitable")
        headers = await self._get_headers()
        url = "https://open.feishu.cn/open-apis/bitable/v1/apps"
        payload = {"name": name}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                raise FeishuRateLimitError("Rate limited on create_bitable")
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(f"create_bitable failed: {data.get('msg')}")
        return data["data"]["app"]["app_token"]

    @with_retry(max_retries=3)
    async def create_bitable_table(
        self,
        app_token: str,
        table_name: str,
        fields: list[dict],
    ) -> str:
        """在多维表格中创建数据表（Sheet）。

        Args:
            app_token: 多维表格 App Token。
            table_name: 数据表名称。
            fields: 字段定义列表，每项包含 field_name 和 type。

        Returns:
            新建数据表的 table_id。
        """
        await self._rate_limiter.acquire("create_bitable_table")
        headers = await self._get_headers()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1"
            f"/apps/{app_token}/tables"
        )
        payload = {
            "table": {
                "name": table_name,
                "fields": fields,
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                raise FeishuRateLimitError(
                    "Rate limited on create_bitable_table"
                )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(
                f"create_bitable_table failed: {data.get('msg')}"
            )
        return data["data"]["table_id"]

    @with_retry(max_retries=3)
    async def get_chat_info(self, chat_id: str) -> dict:
        """获取群基本信息。

        Args:
            chat_id: 群 ID。

        Returns:
            群信息字典，包含 name、description 等字段。
        """
        await self._rate_limiter.acquire("get_chat_info")
        headers = await self._get_headers()
        url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                raise FeishuRateLimitError("Rate limited on get_chat_info")
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(f"get_chat_info failed: {data.get('msg')}")
        return data.get("data", {})

    @with_retry(max_retries=3)
    async def get_bot_info(self) -> dict:
        """获取机器人自身信息（open_id、名称等）。

        Returns:
            包含 open_id、app_name 等字段的字典。
        """
        await self._rate_limiter.acquire("get_bot_info")
        headers = await self._get_headers()
        url = "https://open.feishu.cn/open-apis/bot/v3/info"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                raise FeishuRateLimitError("Rate limited on get_bot_info")
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise FeishuAPIError(f"get_bot_info failed: {data.get('msg')}")
        return data.get("bot", {})
    @with_retry(max_retries=3)
    async def list_bitable_fields(
        self,
        app_token: str,
        table_id: str,
    ) -> list[dict]:
        """查询数据表中已有的字段列表。

        Args:
            app_token: 多维表格 App Token。
            table_id: 数据表 ID。

        Returns:
            字段定义列表，每项包含 field_name、type 等。
        """
        await self._rate_limiter.acquire("list_bitable_fields")
        headers = await self._get_headers()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1"
            f"/apps/{app_token}/tables/{table_id}/fields"
        )
        fields: list[dict] = []
        page_token: str | None = None

        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict = {"page_size": 100}
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 429:
                    raise FeishuRateLimitError("Rate limited on list_bitable_fields")
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    raise FeishuAPIError(f"list_bitable_fields failed: {data.get('msg')}")
                for item in data.get("data", {}).get("items", []):
                    fields.append(item)
                if not data.get("data", {}).get("has_more"):
                    break
                page_token = data.get("data", {}).get("page_token")

        return fields

    @with_retry(max_retries=3)
    async def add_bitable_field(
        self,
        app_token: str,
        table_id: str,
        field_def: dict,
    ) -> str:
        """向已有数据表添加一个字段。

        Args:
            app_token: 多维表格 App Token。
            table_id: 数据表 ID。
            field_def: 字段定义，包含 field_name、type（以及可选 property）。

        Returns:
            新字段的 field_id。
        """
        await self._rate_limiter.acquire("add_bitable_field")
        headers = await self._get_headers()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1"
            f"/apps/{app_token}/tables/{table_id}/fields"
        )
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=field_def)
            if resp.status_code == 429:
                raise FeishuRateLimitError("Rate limited on add_bitable_field")
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(
                f"add_bitable_field failed: {data.get('msg')} | field={field_def.get('field_name')}"
            )
        return data["data"]["field"]["field_id"]

