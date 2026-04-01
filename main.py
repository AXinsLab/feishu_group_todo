"""FastAPI 应用入口。

负责接收飞书 Webhook 事件和 Cron 调度请求，
按 event_type 分发到对应 LangGraph 执行。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import base64
import hashlib
import json as _json
from Crypto.Cipher import AES

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def _decrypt_feishu(encrypt_key: str, encrypted: str) -> dict:
    """解密飞书加密 Webhook 消息体（AES-CBC + SHA256 key）。"""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    buf = base64.b64decode(encrypted)
    iv, ciphertext = buf[:16], buf[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(ciphertext)
    decrypted = decrypted[: -decrypted[-1]]  # remove PKCS7 padding
    return _json.loads(decrypted)

# 已处理事件 ID 缓存（防飞书重试重复处理）
# 格式：{event_id: 过期时间戳}
_processed_events: dict[str, float] = {}
_EVENT_TTL = 300.0  # 秒


def _is_duplicate_event(event_id: str) -> bool:
    """判断事件 ID 是否已处理过。"""
    now = time.monotonic()
    # 清理过期记录
    expired = [eid for eid, exp in _processed_events.items() if now > exp]
    for eid in expired:
        _processed_events.pop(eid, None)

    if event_id in _processed_events:
        return True
    _processed_events[event_id] = now + _EVENT_TTL
    return False


async def _verify_webhook_secret(
    request: Request,
) -> bool:
    """校验 Scheduler Webhook 鉴权 Header。

    Raises:
        HTTPException: Header 缺失或不匹配时返回 403。
    """
    from config import get_settings

    secret = request.headers.get("X-Webhook-Secret", "")
    settings = get_settings()
    expected = settings.webhook_secret.get_secret_value()
    if secret != expected:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """应用生命周期管理：初始化所有客户端和 Graph。"""
    from config import get_settings
    from tools.bitable_client import BitableClient
    from tools.feishu_client import FeishuClient

    settings = get_settings()
    feishu_client = FeishuClient(settings)

    # 优先从 data/bitable_token.txt 加载 token（跨容器重建持久化）
    import os as _os
    _token_path = _os.path.join("data", "bitable_token.txt")
    if _os.path.exists(_token_path):
        try:
            with open(_token_path, "r", encoding="utf-8") as _f:
                _saved_token = _f.read().strip()
            if _saved_token:
                settings.bitable_app_token = _saved_token
                logger.info("Loaded BITABLE_APP_TOKEN=%s from %s", _saved_token, _token_path)
        except Exception as _exc:
            logger.warning("Failed to load BITABLE_APP_TOKEN from data/: %s", _exc)

    bitable_client = BitableClient(settings, feishu_client)

    # 获取机器人自身 open_id（用于 @mention 过滤）
    try:
        _bot_info = await feishu_client.get_bot_info()
        app.state.bot_open_id = _bot_info.get("open_id", "")
        logger.info("Bot open_id: %s", app.state.bot_open_id)
    except Exception as _exc:
        logger.warning("Failed to get bot info: %s", _exc)
        app.state.bot_open_id = ""

    app.state.settings = settings
    app.state.feishu_client = feishu_client
    app.state.storage = bitable_client

    # Graph 在导入后编译，避免循环依赖
    from graphs.message_graph import build_message_graph
    from graphs.onboard_graph import build_onboard_graph
    from graphs.scheduler_graph import build_scheduler_graph

    app.state.onboard_graph = build_onboard_graph(
        bitable_client, feishu_client
    )
    app.state.message_graph = build_message_graph(
        bitable_client, feishu_client
    )
    app.state.scheduler_graph = build_scheduler_graph(
        bitable_client, feishu_client
    )

    logger.info("Application started, all graphs compiled.")
    yield
    logger.info("Application shutting down.")


app = FastAPI(
    title="飞书群聊 Todo 追踪智能体",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查端点。"""
    return {"status": "ok"}


@app.post("/webhook/feishu")
async def feishu_webhook(request: Request) -> JSONResponse:
    """接收飞书所有 Webhook 事件，按 event_type 分发。

    飞书要求 3 秒内返回 200，Graph 执行使用 create_task 异步处理。
    """
    body: dict[str, Any] = await request.json()

    # 飞书加密消息解密
    if "encrypt" in body:
        from config import get_settings
        encrypt_key = get_settings().feishu_encrypt_key
        if encrypt_key:
            body = _decrypt_feishu(encrypt_key, body["encrypt"])

    # URL 验证握手（飞书配置 Webhook 时发起）
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge", "")})

    # 事件去重（防飞书重试）
    event_id: str = body.get("header", {}).get("event_id", "")
    if event_id and _is_duplicate_event(event_id):
        logger.debug("Duplicate event ignored: %s", event_id)
        return JSONResponse({"code": 0})

    event_type: str = body.get("header", {}).get("event_type", "")

    if event_type == "im.message.receive_v1":
        # 只响应 @机器人 的消息，忽略普通群聊消息
        _mentions = body.get("event", {}).get("message", {}).get("mentions", [])
        _bot_open_id = getattr(request.app.state, "bot_open_id", "")
        if _bot_open_id:
            _mentioned = any(
                m.get("id", {}).get("open_id") == _bot_open_id
                for m in _mentions
            )
            if not _mentioned:
                logger.debug("Message ignored: bot not @mentioned")
                return JSONResponse({"code": 0})
        asyncio.create_task(_run_message_graph(request.app.state, body))
    elif event_type == "im.chat.member.bot.added_v1":
        asyncio.create_task(_run_onboard_graph(request.app.state, body))
    else:
        logger.debug("Unhandled event_type: %s", event_type)

    return JSONResponse({"code": 0})


@app.post("/webhook/scheduler")
async def scheduler_webhook(
    request: Request,
    _authenticated: bool = Depends(_verify_webhook_secret),
) -> JSONResponse:
    """接收 Cron 调度触发，启动定时总结 Graph。

    请求体需包含 trigger_time（ISO 8601 格式时间戳）。
    Header 需携带 X-Webhook-Secret 进行鉴权。
    """
    from datetime import datetime

    body: dict[str, Any] = await request.json()
    trigger_time_str: str = body.get(
        "trigger_time", datetime.now().isoformat()
    )
    try:
        trigger_time = datetime.fromisoformat(trigger_time_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid trigger_time format",
        )

    asyncio.create_task(_run_scheduler_graph(request.app.state, trigger_time))
    return JSONResponse({"code": 0, "message": "Scheduler triggered"})


# ── 后台 Graph 执行函数 ────────────────────────────────────


async def _run_message_graph(state: Any, event: dict) -> None:
    """后台执行消息响应 Graph。"""
    try:
        await state.message_graph.ainvoke({"event_raw": event, "bot_open_id": getattr(state, "bot_open_id", "")})
    except Exception:
        logger.exception("MessageGraph failed for event: %s", event)


async def _run_onboard_graph(state: Any, event: dict) -> None:
    """后台执行入群初始化 Graph。"""
    try:
        await state.onboard_graph.ainvoke({"event_raw": event})
    except Exception:
        logger.exception("OnboardGraph failed for event: %s", event)


async def _run_scheduler_graph(state: Any, trigger_time: Any) -> None:
    """后台执行定时总结 Graph（带 SqliteSaver checkpointer）。"""
    from datetime import timedelta

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    time_window_end = trigger_time
    time_window_start = trigger_time - timedelta(hours=24)

    initial_state = {
        "trigger_time": trigger_time,
        "time_window_start": time_window_start,
        "time_window_end": time_window_end,
        "group_list": [],
        "current_group_id": "",
        "raw_messages": [],
        "filtered_messages": [],
        "active_todos": [],
        "completed_yesterday": [],
        "member_map": {},
        "llm_analysis": {},
        "update_operations": [],
        "errors": [],
    }

    thread_id = f"scheduler_{trigger_time.date()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        async with AsyncSqliteSaver.from_conn_string(
            "data/checkpoints.db"
        ) as checkpointer:
            await state.scheduler_graph.ainvoke(
                initial_state,
                config=config,
                checkpointer=checkpointer,
            )
    except Exception:
        logger.exception("SchedulerGraph failed for thread: %s", thread_id)
