"""报告和回复文本生成的 LangGraph 节点函数。

消息卡片 JSON 由程序逻辑构建（不经 LLM），
保证输出格式稳定可测试。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


async def generate_report(state: dict) -> dict:
    """构建每日任务追踪报告消息卡片 JSON。

    包含：昨日完成（含删除线）、待完成、进展待确认。
    逾期判断：预期完成时间 < 今天 AND 状态=进行中。

    Args:
        state: 包含 completed_yesterday、active_todos、
               llm_analysis、time_window_start 的 SchedulerState。

    Returns:
        包含 reply_text（卡片 JSON 字符串）的部分状态更新。
    """
    completed: list[dict] = state.get("completed_yesterday", [])
    active: list[dict] = state.get("active_todos", [])
    analysis: dict = state.get("llm_analysis", {})
    window_start: Any = state.get("time_window_start")

    report_date = (
        window_start.date() if hasattr(window_start, "date") else date.today()
    )
    today = date.today()

    low_confidence_ids: set[str] = set(analysis.get("low_confidence_done", []))

    card = _build_report_card(
        report_date=report_date,
        completed=completed,
        active=active,
        low_confidence_ids=low_confidence_ids,
        today=today,
    )
    return {"reply_text": json.dumps(card, ensure_ascii=False)}


async def build_reject_reply(state: dict) -> dict:
    """生成拒绝回复文本（无关内容）。

    Args:
        state: MessageState（不使用任何字段）。

    Returns:
        包含 reply_text 的部分状态更新。
    """
    from prompts.report import REJECTION_TEXT

    return {"reply_text": REJECTION_TEXT}


async def build_confirm_reply(state: dict) -> dict:
    """根据操作结果构建操作确认回复文本。

    Args:
        state: 包含 operation_type、update_result 的 MessageState。

    Returns:
        包含 reply_text 的部分状态更新。
    """
    from prompts.report import (
        CONFIRM_CREATE,
        CONFIRM_DELETE,
        CONFIRM_DONE,
        CONFIRM_QUERY,
        CONFIRM_RESTORE,
        CONFIRM_UPDATE,
    )

    operation_type: str = state.get("operation_type", "")
    result: dict = state.get("update_result") or {}

    if not result.get("success"):
        error = result.get("error", "操作失败，请重试")
        return {"reply_text": f"❌ {error}"}

    desc: str = result.get("task_description", "")

    if operation_type == "新增":
        fields: dict = result.get("fields", {})
        assignee = fields.get("负责人姓名") or "待定"
        due = fields.get("预期完成时间") or "待确认"
        text = CONFIRM_CREATE.format(
            desc=fields.get("任务描述", ""),
            assignee=assignee,
            due=due,
        )
    elif operation_type == "标记完成":
        text = CONFIRM_DONE.format(desc=desc)
    elif operation_type == "修改":
        changes: dict = result.get("changes", {})
        change_desc = "、".join(
            f"{k}→{v}" for k, v in changes.items() if k != "最后更新"
        )
        text = CONFIRM_UPDATE.format(desc=desc, change=change_desc)
    elif operation_type == "删除":
        text = CONFIRM_DELETE.format(desc=desc)
    elif operation_type == "恢复任务":
        text = CONFIRM_RESTORE.format(desc=desc)
    elif operation_type == "查询状态":
        text = CONFIRM_QUERY.format(
            desc=desc,
            status=result.get("status", ""),
            assignee=result.get("assignee", "未分配"),
        )
    else:
        text = "✅ 操作完成"

    return {"reply_text": text}


# ── 消息卡片构建辅助函数 ──────────────────────────────────


def _build_report_card(
    report_date: date,
    completed: list[dict],
    active: list[dict],
    low_confidence_ids: set[str],
    today: date,
) -> dict:
    """构建飞书消息卡片 JSON（富文本格式）。

    Args:
        report_date: 报告对应日期（昨日）。
        completed: 昨日完成的任务列表。
        active: 当前进行中的任务列表。
        low_confidence_ids: 低置信度完成的 record_id 集合。
        today: 今天的日期（用于逾期判断）。

    Returns:
        符合飞书消息卡片规范的字典。
    """
    date_str = report_date.strftime("%Y/%m/%d")

    # 分离低置信度任务（进展待确认）
    low_conf_todos = [
        t for t in active if t.get("record_id") in low_confidence_ids
    ]
    normal_active = [
        t for t in active if t.get("record_id") not in low_confidence_ids
    ]

    lines: list[str] = [
        f"📋 每日任务追踪报告 · {date_str}",
        "─────────────────────────────────",
    ]

    # 昨日完成
    lines.append(f"✅ 昨日完成（{len(completed)} 项）")
    for todo in completed:
        assignee = todo.get("负责人姓名", "")
        desc = todo.get("任务描述", "")
        assignee_str = f" · @{assignee}" if assignee else ""
        lines.append(f"~~· {desc}{assignee_str}~~")

    lines.append("")

    # 待完成
    lines.append(f"📌 待完成（{len(normal_active)} 项）")
    for todo in normal_active:
        desc = todo.get("任务描述", "")
        assignee = todo.get("负责人姓名", "")
        due_str = todo.get("预期完成时间", "")
        overdue_flag = ""

        if due_str:
            try:
                due_date = date.fromisoformat(str(due_str))
                if due_date < today:
                    overdue_flag = " ⚠️ 已逾期"
                due_display = due_date.strftime("%m/%d")
            except (ValueError, TypeError):
                due_display = "待确认"
        else:
            due_display = "待确认"

        assignee_str = f" · @{assignee}" if assignee else ""
        lines.append(
            f"· {desc}{assignee_str} · 预期：{due_display}{overdue_flag}"
        )

    # 进展待确认
    if low_conf_todos:
        lines.append("")
        lines.append(f"⚠️ 进展待确认（{len(low_conf_todos)} 项）")
        for todo in low_conf_todos:
            desc = todo.get("任务描述", "")
            lines.append(f"· {desc} · 昨日消息提及但状态不明，请相关成员确认")

    lines.append("─────────────────────────────────")
    lines.append("如需更新任务，请 @我 并说明操作")

    # 使用飞书富文本（post）格式
    full_text = "\n".join(lines)
    return {
        "msg_type": "text",
        "content": json.dumps({"text": full_text}, ensure_ascii=False),
    }
