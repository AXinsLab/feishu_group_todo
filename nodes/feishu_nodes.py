"""飞书消息与群相关的 LangGraph 节点函数。

所有节点接收 State 字典，返回部分状态更新字典。
依赖通过模块级工厂函数注入，便于测试时 monkeypatch。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── 依赖工厂（测试时可 monkeypatch） ──────────────────────


def _get_feishu() -> Any:
    """获取 FeishuClient 实例（从应用状态）。"""
    from tools.feishu_client import FeishuClient

    return FeishuClient  # 实际使用时由 Graph 注入


def _get_storage() -> Any:
    """获取 StorageInterface 实例（从应用状态）。"""
    from tools.storage_interface import StorageInterface

    return StorageInterface  # 实际使用时由 Graph 注入


# ── SchedulerGraph 节点 ───────────────────────────────────


async def fetch_groups(
    state: dict,
    storage: Any,
) -> dict:
    """从存储层获取所有群配置列表。

    Args:
        state: 当前 SchedulerState。
        storage: StorageInterface 实例。

    Returns:
        包含 group_list 的部分状态更新。
    """
    groups = await storage.get_group(None)
    if not isinstance(groups, list):
        groups = []
    logger.info("Fetched %d groups", len(groups))
    return {"group_list": groups}


async def fetch_messages(
    state: dict,
    feishu: Any,
) -> dict:
    """拉取当前群在时间窗口内的消息。

    Args:
        state: 包含 current_group_id、time_window_start/end。
        feishu: FeishuClient 实例。

    Returns:
        包含 raw_messages 的部分状态更新。
    """
    group_id: str = state["current_group_id"]
    start: datetime = state["time_window_start"]
    end: datetime = state["time_window_end"]

    # 转为毫秒时间戳
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    try:
        messages = await feishu.get_group_messages(group_id, start_ms, end_ms)
    except Exception as exc:
        logger.error(
            "fetch_messages failed for %s: %s",
            group_id,
            exc,
        )
        return {
            "raw_messages": [],
            "errors": [
                {
                    "group_id": group_id,
                    "type": "system",
                    "message": f"拉取消息失败：{exc}",
                }
            ],
        }

    logger.info(
        "Fetched %d messages for group %s",
        len(messages),
        group_id,
    )
    return {"raw_messages": messages}


async def refresh_members(
    state: dict,
    feishu: Any,
    storage: Any,
) -> dict:
    """刷新群成员表（调用飞书 API 并 upsert 到存储层）。

    Args:
        state: 包含 current_group_id 或 group_id。
        feishu: FeishuClient 实例。
        storage: StorageInterface 实例。

    Returns:
        包含 member_map 的部分状态更新。
    """
    group_id: str = state.get("current_group_id") or state.get("group_id", "")

    try:
        members = await feishu.get_group_members(group_id)
        await storage.upsert_members(group_id, members)
    except Exception as exc:
        logger.error(
            "refresh_members failed for %s: %s",
            group_id,
            exc,
        )
        return {"member_map": {}}

    member_map: dict[str, dict] = {m["open_id"]: m for m in members}
    logger.info(
        "Refreshed %d members for group %s",
        len(members),
        group_id,
    )
    return {"member_map": member_map}


async def send_empty_report(
    state: dict,
    feishu: Any,
) -> dict:
    """发送空状态报告（昨日无任务）。

    Args:
        state: 包含 current_group_id。
        feishu: FeishuClient 实例。

    Returns:
        空字典（无状态更新）。
    """
    from prompts.report import EMPTY_REPORT_TEXT

    group_id: str = state.get("current_group_id", "") or state.get(
        "group_id", ""
    )
    content = json.dumps({"text": EMPTY_REPORT_TEXT}, ensure_ascii=False)
    try:
        await feishu.send_message(group_id, content)
    except Exception as exc:
        logger.error(
            "send_empty_report failed for %s: %s",
            group_id,
            exc,
        )
    return {}


async def send_report(
    state: dict,
    feishu: Any,
    storage: Any,
) -> dict:
    """发送每日报告消息卡片，并更新群最后同步时间。

    Args:
        state: 包含 current_group_id 和 reply_text。
        feishu: FeishuClient 实例。
        storage: StorageInterface 实例。

    Returns:
        空字典（无状态更新）。
    """
    group_id: str = state.get("current_group_id", "")
    reply_text: str = state.get("reply_text", "")

    try:
        # reply_text 由 generate_report 生成，格式为 JSON 字符串：
        # {"msg_type": "text", "content": "{\"text\": \"...\"}"}
        # 需要解包取出 content，并使用正确的 msg_type
        import json as _json
        try:
            card = _json.loads(reply_text)
            msg_type = card.get("msg_type", "text")
            content = card.get("content", reply_text)
        except (_json.JSONDecodeError, TypeError):
            msg_type = "text"
            content = _json.dumps({"text": reply_text}, ensure_ascii=False)
        await feishu.send_message(group_id, content, msg_type=msg_type)
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        await storage.upsert_group({"群ID": group_id, "最后同步时间": now_ms})
    except Exception as exc:
        logger.error(
            "send_report failed for %s: %s",
            group_id,
            exc,
        )
    return {}


# ── MessageGraph 节点 ─────────────────────────────────────


async def parse_event(state: dict) -> dict:
    """解析飞书 im.message.receive_v1 事件，提取基本信息。

    @mention 处理逻辑：
    - bot 自身的 mention key 从文本中删除
    - 其他用户的 mention key 替换为 @{真实姓名}（供 LLM 识别负责人）
    - 非 bot mention 用户存入 mentioned_users，供 execute_operation 直接使用

    Args:
        state: 包含 event_raw、bot_open_id 的 MessageState。

    Returns:
        包含 group_id、sender_open_id、message_id、message_text、
        mentioned_users 的部分状态更新。
    """
    import re

    event = state.get("event_raw", {})
    message = event.get("event", {}).get("message", {})
    bot_open_id: str = state.get("bot_open_id", "")

    group_id = message.get("chat_id", "")
    message_id = message.get("message_id", "")
    sender_open_id = (
        event.get("event", {})
        .get("sender", {})
        .get("sender_id", {})
        .get("open_id", "")
    )

    # 解析消息正文
    body_content = message.get("content", "{}")
    try:
        content_obj = json.loads(body_content)
        raw_text = content_obj.get("text", "")
        # mentions 在 content JSON 内部（飞书新格式），也可能在 message 层级
        inline_mentions = content_obj.get("mentions", [])
    except (json.JSONDecodeError, TypeError):
        raw_text = body_content
        inline_mentions = []

    # 合并两处 mentions（兼容不同飞书消息格式）
    msg_mentions = message.get("mentions", [])
    all_mentions = inline_mentions if inline_mentions else msg_mentions

    # 处理 @mention：bot mention 删除，用户 mention 替换为 @姓名
    processed_text = raw_text
    mentioned_users: list[dict] = []

    for mention in all_mentions:
        key = mention.get("key", "")
        name = mention.get("name", "")
        open_id = mention.get("id", {}).get("open_id", "")

        if not key:
            continue

        if bot_open_id and open_id == bot_open_id:
            # 删除 bot 自身的 @mention
            processed_text = processed_text.replace(key, "")
        else:
            # 将占位符替换为真实姓名，保留 @ 前缀供 LLM 识别
            processed_text = processed_text.replace(key, f"@{name}" if name else "")
            if open_id or name:
                mentioned_users.append({"name": name, "open_id": open_id})

    # 清理多余空白
    message_text = re.sub(r"\s+", " ", processed_text).strip()

    # 系统指令检测：消息以 "/" 开头时提取指令名（如 "/init"）
    system_command: str | None = None
    if message_text.startswith("/"):
        system_command = message_text.split()[0].lower()

    return {
        "group_id": group_id,
        "sender_open_id": sender_open_id,
        "message_id": message_id,
        "message_text": message_text,
        "mentioned_users": mentioned_users,
        "system_command": system_command,
        "_intent_result": {},
        "member_refresh_attempted": False,
        "target_todo": None,
        "update_result": None,
        "reply_text": "",
    }


async def send_reply(
    state: dict,
    feishu: Any,
) -> dict:
    """发送回复消息（引用原消息）。

    Args:
        state: 包含 group_id、message_id、reply_text。
        feishu: FeishuClient 实例。

    Returns:
        空字典（无状态更新）。
    """
    group_id: str = state.get("group_id", "")
    message_id: str = state.get("message_id", "")
    reply_text: str = state.get("reply_text", "")

    content = json.dumps({"text": reply_text}, ensure_ascii=False)
    try:
        await feishu.send_message(
            group_id,
            content,
            msg_type="text",
            reply_to_message_id=message_id,
        )
    except Exception as exc:
        logger.error(
            "send_reply failed for message %s: %s",
            message_id,
            exc,
        )
    return {}


# ── OnboardGraph 节点 ─────────────────────────────────────


async def parse_onboard_event(state: dict) -> dict:
    """解析飞书机器人入群事件。

    Args:
        state: 包含 event_raw 的 OnboardState。

    Returns:
        包含 group_id、group_name 的部分状态更新。
    """
    event = state.get("event_raw", {})
    chat_id = event.get("event", {}).get("chat_id", "")
    return {
        "group_id": chat_id,
        "group_name": "",  # 后续由 check_group_exists 填充
        "is_first_time": False,
        "bitable_exists": False,
        "member_list": [],
    }


async def send_introduction(
    state: dict,
    feishu: Any,
) -> dict:
    """发送机器人自我介绍消息。

    Args:
        state: 包含 group_id。
        feishu: FeishuClient 实例。

    Returns:
        空字典（无状态更新）。
    """
    from prompts.report import INTRODUCTION_TEXT

    group_id: str = state.get("group_id", "")
    content = json.dumps({"text": INTRODUCTION_TEXT}, ensure_ascii=False)
    try:
        await feishu.send_message(group_id, content)
        logger.info("Introduction sent to group %s", group_id)
    except Exception as exc:
        logger.error(
            "send_introduction failed for %s: %s",
            group_id,
            exc,
        )
    return {}
