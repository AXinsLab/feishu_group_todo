"""飞书多维表格读写相关的 LangGraph 节点函数。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 飞书多维表格日期字段要求 UTC 00:00:00 的毫秒时间戳
# 注意：不使用 CST（UTC+8）本地午夜，否则会导致 DatetimeFieldConvFail
def _date_to_ms(d: date) -> int:
    """将 date 对象转为飞书多维表格所需的毫秒时间戳（UTC 00:00:00）。

    飞书 Bitable 日期字段要求传入 UTC 午夜毫秒时间戳（可被 86400000 整除），
    使用 CST 午夜会导致 DatetimeFieldConvFail。
    """
    from datetime import timezone
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _due_date_ms(s: str | None) -> int | None:
    """将 LLM 返回的 due_date 字符串（YYYY-MM-DD）转为毫秒时间戳；空则返回 None。

    返回 None 而非空串，调用方应跳过 None 值字段，
    避免向飞书日期字段传入空字符串导致 DatetimeFieldConvFail。
    """
    if not s:
        return None
    try:
        return _date_to_ms(date.fromisoformat(s))
    except (ValueError, TypeError):
        return None


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
    today_ms = _date_to_ms(date.today())

    # 高置信度完成
    for record_id in analysis.get("high_confidence_done", []):
        operations.append(
            {
                "type": "update",
                "record_id": record_id,
                "fields": {
                    "状态": "已完成",
                    "完成日期": today_ms,
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
        task_fields: dict = {
            "任务描述": task.get("description", ""),
            "负责人姓名": task.get("assignee_name") or "",
            "负责人open_id": task.get("assignee_open_id") or "",
            "状态": "进行中",
            "来源类型": "定时提取",
            "来源消息ID": task.get("source_message_id", ""),
            "来源摘要": task.get("source_summary", ""),
            "创建日期": today_ms,
            "最后更新": today_ms,
            "群ID": state.get("current_group_id", ""),
        }
        # due_date 字段已移除，不再写入预期完成时间
        operations.append({"type": "create", "fields": task_fields})

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
    today_ms = _date_to_ms(date.today())

    try:
        if operation_type == "新增":
            intent = state.get("_intent_result", {})
            member_map = state.get("member_map", {})
            assignee_name = _resolve_assignee_name(intent, member_map)
            assignee_open_id = _resolve_assignee_open_id(intent, member_map)
            # fallback 1：LLM 未提取到负责人时，直接使用消息中 @提及 的第一个非 bot 用户
            if not assignee_name:
                for _u in state.get("mentioned_users", []):
                    assignee_name = _u.get("name", "")
                    assignee_open_id = _u.get("open_id", "")
                    break
            # fallback 2：仍无负责人则默认为消息发送者
            if not assignee_name:
                sender_open_id = state.get("sender_open_id", "")
                if sender_open_id:
                    sender_info = member_map.get(sender_open_id, {})
                    assignee_name = (
                        sender_info.get("真实姓名")
                        or sender_info.get("name", "")
                    )
                    assignee_open_id = sender_open_id
            fields = {
                "任务描述": intent.get("task_description", ""),
                "负责人姓名": assignee_name,
                "负责人open_id": assignee_open_id,
                "状态": "进行中",
                "来源类型": "成员手动添加",
                "来源消息ID": message_id,
                "创建日期": today_ms,
                "最后更新": today_ms,
                "群ID": group_id,
            }
            # due_date 字段已移除，不再写入预期完成时间
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
                "完成日期": today_ms,
                "完成来源": "成员确认",
                "最后更新": today_ms,
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
            update_fields: dict = {"最后更新": today_ms}
            if intent.get("new_content"):
                update_fields["任务描述"] = intent["new_content"]
            if intent.get("assignee_name"):
                update_fields["负责人姓名"] = intent["assignee_name"]
                update_fields["负责人open_id"] = _resolve_assignee_open_id(
                    intent,
                    state.get("member_map", {}),
                )
            # due_date 字段已移除
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
                # 注意：不清空完成日期，避免向日期字段传入空值导致 DatetimeFieldConvFail
                "最后更新": today_ms,
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
    """检查多维表格是否已创建，并自动修复缺失的子表和字段。

    若 storage 已配置 app_token（来自 token.txt 或环境变量），则：
    1. 验证 bitable 可访问性；
    2. 若可访问，调用 ensure_schema() 自动补全缺失子表和字段；
    3. 不论修复结果如何，都返回 bitable_exists=True，避免覆盖用户配置的 token。

    若未配置 token，则返回 bitable_exists=False，走正常创建流程。

    Args:
        state: OnboardState。
        storage: StorageInterface 实例。

    Returns:
        包含 bitable_exists 和 schema_repair_report 的部分状态更新。
    """
    if not storage._app_token:
        # 无 token：走创建流程
        return {"bitable_exists": False}

    # 验证可访问性
    accessible = await storage.check_bitable_exists()
    if not accessible:
        logger.warning(
            "Bitable token %s is set but currently inaccessible "
            "(bot may lack permission or token is invalid). "
            "Will skip auto-creation to preserve the configured token.",
            storage._app_token,
        )
        return {"bitable_exists": True, "schema_repair_report": {}}

    # 可访问：自动修复缺失子表和字段
    logger.info("Bitable accessible, running schema repair check...")
    try:
        report = await storage.ensure_schema()
        any_fixed = any(actions for actions in report.values())
        if any_fixed:
            logger.info("Schema repair completed: %s", report)
        else:
            logger.info("Schema check passed, no repairs needed.")
        return {"bitable_exists": True, "schema_repair_report": report}
    except Exception as exc:
        logger.error("ensure_schema failed: %s", exc)
        return {"bitable_exists": True, "schema_repair_report": {"error": str(exc)}}


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
        # 将 app_token 写入 data/ 挂载目录，跨容器重建持久化
        import os as _os
        _token_path = _os.path.join("data", "bitable_token.txt")
        try:
            _os.makedirs("data", exist_ok=True)
            with open(_token_path, "w", encoding="utf-8") as _f:
                _f.write(app_token)
            from config import get_settings
            get_settings().bitable_app_token = app_token
            logger.info("Saved BITABLE_APP_TOKEN=%s to %s", app_token, _token_path)
        except Exception as _exc:
            logger.warning("Failed to save BITABLE_APP_TOKEN to data/: %s", _exc)
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
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    group_data = {
        "群ID": state.get("group_id", ""),
        "群名称": state.get("group_name", ""),
        "机器人加入时间": now_ms,
        "最后同步时间": now_ms,
        "多维表格ID": storage._app_token,
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
    """从意图结果和成员表中解析负责人姓名。

    先精确匹配，再子串模糊匹配（支持"甘鑫"匹配"甘鑫 Grant"等）。
    """
    assignee_name = intent.get("assignee_name", "") or ""
    if not assignee_name:
        return ""

    name_lower = assignee_name.lower()
    # 第一轮：精确匹配
    for member in member_map.values():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(c.lower() == name_lower for c in candidates if c):
                return member.get("name", assignee_name)

    # 第二轮：子串模糊匹配
    for member in member_map.values():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(
                (name_lower in c.lower() or c.lower() in name_lower)
                for c in candidates if c
            ):
                return member.get("name", assignee_name)

    return assignee_name


def _resolve_assignee_open_id(intent: dict, member_map: dict) -> str:
    """从意图结果和成员表中解析负责人 open_id。

    先精确匹配，再子串模糊匹配（与 _resolve_assignee_name 逻辑对称）。
    """
    assignee_name = intent.get("assignee_name", "") or ""
    if not assignee_name:
        return ""

    name_lower = assignee_name.lower()
    # 第一轮：精确匹配
    for open_id, member in member_map.items():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(c.lower() == name_lower for c in candidates if c):
                return open_id

    # 第二轮：子串模糊匹配
    for open_id, member in member_map.items():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(
                (name_lower in c.lower() or c.lower() in name_lower)
                for c in candidates if c
            ):
                return open_id

    return ""
