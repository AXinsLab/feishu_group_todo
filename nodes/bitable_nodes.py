"""飞书多维表格读写相关的 LangGraph 节点函数。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def fetch_todos(
    state: dict,
    storage: Any,
) -> dict:
    """查询当前群的进行中任务和昨日完成任务。

    Args:
        state: 包含 current_group_id 和 time_window_start。
        storage: StorageInterface 实例。

    Returns:
        包含 active_todos、completed_yesterday 的部分状态。
    """
    group_id: str = state["current_group_id"]
    window_start: datetime = state["time_window_start"]
    yesterday = window_start.date()

    try:
        active = await storage.get_todos(group_id, status="进行中")
        all_completed = await storage.get_todos(group_id, status="已完成")
    except Exception as exc:
        logger.error("fetch_todos failed for %s: %s", group_id, exc)
        return {
            "active_todos": [],
            "completed_yesterday": [],
            "errors": [
                {
                    "group_id": group_id,
                    "type": "system",
                    "message": f"查询任务失败：{exc}",
                }
            ],
        }

    # 筛选昨日完成（完成日期 == 昨天）
    completed_yesterday = [
        t for t in all_completed if t.get("完成日期") == str(yesterday)
    ]

    logger.info(
        "Group %s: %d active, %d completed yesterday",
        group_id,
        len(active),
        len(completed_yesterday),
    )
    return {
        "active_todos": active,
        "completed_yesterday": completed_yesterday,
    }


async def fetch_all_todos(
    state: dict,
    storage: Any,
) -> dict:
    """查询当前群所有状态的 Todo（MessageGraph 使用）。

    Args:
        state: 包含 group_id 的 MessageState。
        storage: StorageInterface 实例。

    Returns:
        包含 all_todos 的部分状态更新。
    """
    group_id: str = state["group_id"]
    try:
        todos = await storage.get_todos(group_id)
    except Exception as exc:
        logger.error(
            "fetch_all_todos failed for %s: %s",
            group_id,
            exc,
        )
        return {"all_todos": []}
    return {"all_todos": todos}


async def filter_messages(state: dict) -> dict:
    """硬过滤：剔除 message_id 已存在于 Todo 表的消息。

    不调用 LLM，纯字段比对。这是两层防重复机制的第一层。

    Args:
        state: 包含 raw_messages 和 active_todos。

    Returns:
        包含 filtered_messages 的部分状态更新。
    """
    raw_messages: list[dict] = state.get("raw_messages", [])
    active_todos: list[dict] = state.get("active_todos", [])

    # 收集所有已存在的来源消息 ID
    existing_source_ids: set[str] = {
        t.get("来源消息ID", "") for t in active_todos if t.get("来源消息ID")
    }

    filtered = [
        m
        for m in raw_messages
        if m.get("message_id") not in existing_source_ids
    ]

    logger.info(
        "Filtered messages: %d -> %d (removed %d)",
        len(raw_messages),
        len(filtered),
        len(raw_messages) - len(filtered),
    )
    return {"filtered_messages": filtered}


async def build_operations(state: dict) -> dict:
    """根据 LLM 分析结果构建增删改操作列表。

    Args:
        state: 包含 llm_analysis、active_todos。

    Returns:
        包含 update_operations 的部分状态更新。
    """
    analysis: dict = state.get("llm_analysis", {})
    operations: list[dict] = []
    today_str = str(date.today())

    # 高置信度完成
    for record_id in analysis.get("high_confidence_done", []):
        operations.append(
            {
                "type": "update",
                "record_id": record_id,
                "fields": {
                    "状态": "已完成",
                    "完成日期": today_str,
                    "完成来源": "LLM判断",
                },
            }
        )

    # 低置信度完成（只更新备注，不改状态）
    for record_id in analysis.get("low_confidence_done", []):
        operations.append(
            {
                "type": "update",
                "record_id": record_id,
                "fields": {
                    "进展备注": "昨日消息提及可能已完成，请相关成员确认",
                },
            }
        )

    # 新增任务
    for task in analysis.get("new_tasks", []):
        operations.append(
            {
                "type": "create",
                "fields": {
                    "任务描述": task.get("description", ""),
                    "负责人姓名": task.get("assignee_name") or "",
                    "负责人open_id": task.get("assignee_open_id") or "",
                    "预期完成时间": task.get("due_date") or "",
                    "状态": "进行中",
                    "来源类型": "定时提取",
                    "来源消息ID": task.get("source_message_id", ""),
                    "来源摘要": task.get("source_summary", ""),
                    "创建日期": today_str,
                    "最后更新": today_str,
                    "群ID": state.get("current_group_id", ""),
                },
            }
        )

    return {"update_operations": operations}


async def execute_updates(
    state: dict,
    storage: Any,
) -> dict:
    """执行 SchedulerGraph 的批量增改操作。

    Args:
        state: 包含 update_operations 的 SchedulerState。
        storage: StorageInterface 实例。

    Returns:
        空字典或含错误的部分状态更新。
    """
    group_id: str = state.get("current_group_id", "")
    errors: list[dict] = []

    for op in state.get("update_operations", []):
        try:
            if op["type"] == "update":
                await storage.update_todo(op["record_id"], op["fields"])
            elif op["type"] == "create":
                await storage.create_todo(op["fields"])
        except Exception as exc:
            logger.error("execute_updates op failed: %s", exc)
            errors.append(
                {
                    "group_id": group_id,
                    "type": "system",
                    "message": f"写入操作失败：{exc}",
                }
            )

    result: dict = {}
    if errors:
        result["errors"] = errors
    return result


async def execute_operation(
    state: dict,
    storage: Any,
) -> dict:
    """执行 MessageGraph 的单条 CRUD 操作。

    Args:
        state: 包含 operation_type、target_todo 的 MessageState。
        storage: StorageInterface 实例。

    Returns:
        包含 update_result 的部分状态更新。
    """
    operation_type: str = state.get("operation_type", "")
    target_todo: dict | None = state.get("target_todo")
    group_id: str = state.get("group_id", "")
    message_id: str = state.get("message_id", "")
    today_str = str(date.today())

    try:
        if operation_type == "新增":
            intent = state.get("_intent_result", {})
            fields = {
                "任务描述": intent.get("task_description", ""),
                "负责人姓名": _resolve_assignee_name(
                    intent, state.get("member_map", {})
                ),
                "负责人open_id": _resolve_assignee_open_id(
                    intent, state.get("member_map", {})
                ),
                "预期完成时间": intent.get("due_date") or "",
                "状态": "进行中",
                "来源类型": "成员手动添加",
                "来源消息ID": message_id,
                "创建日期": today_str,
                "最后更新": today_str,
                "群ID": group_id,
            }
            record_id = await storage.create_todo(fields)
            return {
                "update_result": {
                    "success": True,
                    "record_id": record_id,
                    "fields": fields,
                }
            }

        if target_todo is None:
            return {
                "update_result": {
                    "success": False,
                    "error": "未找到目标任务",
                }
            }

        record_id = target_todo.get("record_id", "")
        desc = target_todo.get("任务描述", "")

        if operation_type == "标记完成":
            fields = {
                "状态": "已完成",
                "完成日期": today_str,
                "完成来源": "成员确认",
                "最后更新": today_str,
            }
            await storage.update_todo(record_id, fields)
            return {
                "update_result": {
                    "success": True,
                    "action": "mark_done",
                    "task_description": desc,
                }
            }

        if operation_type == "修改":
            intent = state.get("_intent_result", {})
            update_fields: dict = {"最后更新": today_str}
            if intent.get("new_content"):
                update_fields["任务描述"] = intent["new_content"]
            if intent.get("assignee_name"):
                update_fields["负责人姓名"] = intent["assignee_name"]
                update_fields["负责人open_id"] = _resolve_assignee_open_id(
                    intent,
                    state.get("member_map", {}),
                )
            if intent.get("due_date"):
                update_fields["预期完成时间"] = intent["due_date"]
            await storage.update_todo(record_id, update_fields)
            return {
                "update_result": {
                    "success": True,
                    "action": "update",
                    "task_description": desc,
                    "changes": update_fields,
                }
            }

        if operation_type == "删除":
            await storage.delete_todo(record_id)
            return {
                "update_result": {
                    "success": True,
                    "action": "delete",
                    "task_description": desc,
                }
            }

        if operation_type == "恢复任务":
            fields = {
                "状态": "进行中",
                "完成日期": "",
                "最后更新": today_str,
            }
            await storage.update_todo(record_id, fields)
            return {
                "update_result": {
                    "success": True,
                    "action": "restore",
                    "task_description": desc,
                }
            }

        if operation_type == "查询状态":
            return {
                "update_result": {
                    "success": True,
                    "action": "query",
                    "task_description": desc,
                    "status": target_todo.get("状态", ""),
                    "assignee": target_todo.get("负责人姓名", ""),
                }
            }

    except Exception as exc:
        logger.error("execute_operation failed: %s", exc)
        return {
            "update_result": {
                "success": False,
                "error": str(exc),
            }
        }

    return {"update_result": {"success": False}}


# ── OnboardGraph 节点 ─────────────────────────────────────


async def check_group_exists(
    state: dict,
    storage: Any,
    feishu: Any,
) -> dict:
    """检查群是否已在配置表中存在（幂等入群判断）。

    Args:
        state: 包含 group_id 的 OnboardState。
        storage: StorageInterface 实例。
        feishu: FeishuClient 实例（获取群名称）。

    Returns:
        包含 is_first_time、group_name 的部分状态更新。
    """
    group_id: str = state["group_id"]

    try:
        existing = await storage.get_group(group_id)
        is_first_time = existing is None
    except Exception:
        is_first_time = True

    group_name = ""
    try:
        chat_info = await feishu.get_chat_info(group_id)
        group_name = chat_info.get("name", "")
    except Exception as exc:
        logger.warning(
            "get_chat_info failed for %s: %s",
            group_id,
            exc,
        )

    return {
        "is_first_time": is_first_time,
        "group_name": group_name,
    }


async def check_bitable_exists(
    state: dict,
    storage: Any,
) -> dict:
    """检查多维表格是否已创建。

    Args:
        state: OnboardState。
        storage: StorageInterface 实例。

    Returns:
        包含 bitable_exists 的部分状态更新。
    """
    exists = await storage.check_bitable_exists()
    return {"bitable_exists": exists}


async def create_bitable(
    state: dict,
    feishu: Any,
    storage: Any,
) -> dict:
    """创建多维表格和三张数据表。

    Args:
        state: 包含 group_name 的 OnboardState。
        feishu: FeishuClient 实例。
        storage: BitableClient 实例（需调用 initialize_tables）。

    Returns:
        包含 bitable_exists 的部分状态更新。
    """
    group_name: str = state.get("group_name", "群任务追踪")
    bitable_name = f"{group_name}·任务追踪"

    try:
        app_token = await feishu.create_bitable(bitable_name)
        await storage.initialize_tables(app_token)
        logger.info(
            "Created bitable %s for group %s",
            app_token,
            state.get("group_id"),
        )
        return {"bitable_exists": True}
    except Exception as exc:
        logger.error("create_bitable failed: %s", exc)
        return {"bitable_exists": False}


async def write_group_config(
    state: dict,
    storage: Any,
) -> dict:
    """将群配置写入存储层。

    Args:
        state: 包含 group_id、group_name 的 OnboardState。
        storage: StorageInterface 实例。

    Returns:
        空字典（无状态更新）。
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    group_data = {
        "群ID": state.get("group_id", ""),
        "群名称": state.get("group_name", ""),
        "机器人加入时间": now,
        "最后同步时间": now,
    }
    try:
        await storage.upsert_group(group_data)
    except Exception as exc:
        logger.error("write_group_config failed: %s", exc)
    return {}


async def fetch_and_write_members(
    state: dict,
    feishu: Any,
    storage: Any,
) -> dict:
    """拉取群成员并写入成员表。

    Args:
        state: 包含 group_id 的 OnboardState。
        feishu: FeishuClient 实例。
        storage: StorageInterface 实例。

    Returns:
        包含 member_list 的部分状态更新。
    """
    group_id: str = state.get("group_id", "")
    try:
        members = await feishu.get_group_members(group_id)
        await storage.upsert_members(group_id, members)
        return {"member_list": members}
    except Exception as exc:
        logger.error("fetch_and_write_members failed: %s", exc)
        return {"member_list": []}


# ── 辅助函数 ─────────────────────────────────────────────


def _resolve_assignee_name(intent: dict, member_map: dict) -> str:
    """从意图结果和成员表中解析负责人姓名。"""
    assignee_name = intent.get("assignee_name", "") or ""
    if not assignee_name:
        return ""

    # 模糊匹配（不区分大小写）
    name_lower = assignee_name.lower()
    for member in member_map.values():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(c.lower() == name_lower for c in candidates if c):
                return member.get("name", assignee_name)

    return assignee_name


def _resolve_assignee_open_id(intent: dict, member_map: dict) -> str:
    """从意图结果和成员表中解析负责人 open_id。"""
    assignee_name = intent.get("assignee_name", "") or ""
    if not assignee_name:
        return ""

    name_lower = assignee_name.lower()
    for open_id, member in member_map.items():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(c.lower() == name_lower for c in candidates if c):
                return open_id

    return ""
