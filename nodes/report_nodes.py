"""报告和回复文本生成的 LangGraph 节点函数。

所有对外消息均使用 msg_type=text，<at user_id="..."> 语法支持跨租户 @mention。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


async def generate_report(state: dict) -> dict:
    """构建每日任务追踪报告文本消息。

    包含：昨日完成、待完成（带编号）、进展待确认，逾期标注。

    Args:
        state: 包含 completed_yesterday、active_todos、
               llm_analysis、time_window_start 的 SchedulerState。

    Returns:
        包含 reply_text（纯文本字符串）的部分状态更新。
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

    return {
        "reply_text": _build_report_text(
            report_date=report_date,
            completed=completed,
            active=active,
            low_confidence_ids=low_confidence_ids,
            today=today,
        )
    }


async def build_reject_reply(state: dict) -> dict:
    """生成拒绝回复文本（无关内容）。"""
    from prompts.report import REJECTION_TEXT

    return {"reply_text": REJECTION_TEXT}


async def build_confirm_reply(state: dict) -> dict:
    """根据操作结果构建操作确认回复文本。"""
    from prompts.report import (
        CONFIRM_CREATE,
        CONFIRM_DELETE,
        CONFIRM_DONE,
        CONFIRM_QUERY,
        CONFIRM_RESTORE,
        CONFIRM_UPDATE,
    )

    operation_type: str = state.get("operation_type", "")

    # 多操作模式：有多个结果时汇总回复
    all_results: list[dict] = state.get("update_results") or []
    if len(all_results) > 1:
        lines: list[str] = []
        for r in all_results:
            desc = r.get("task_description", "")
            if r.get("success"):
                action = r.get("action", "")
                if action == "mark_done":
                    lines.append(f"✅ 已完成：{desc}")
                elif action == "create":
                    lines.append(f"✅ 已新增：{desc}")
                elif action == "update":
                    lines.append(f"✅ 已更新：{desc}")
                elif action == "delete":
                    lines.append(f"✅ 已删除：{desc}")
                elif action == "restore":
                    lines.append(f"✅ 已恢复：{desc}")
                else:
                    lines.append(f"✅ {desc}")
            else:
                lines.append(f"❌ {r.get('error', '操作失败')}")
        return {"reply_text": "\n".join(lines)}

    result: dict = state.get("update_result") or {}

    if not result.get("success"):
        error = result.get("error", "操作失败，请重试")
        return {"reply_text": f"❌ {error}"}

    desc: str = result.get("task_description", "")

    if operation_type == "新增":
        fields: dict = result.get("fields", {})
        assignee_name = fields.get("负责人姓名") or ""
        assignee_open_id = fields.get("负责人open_id") or ""
        assignee = _format_assignee(assignee_name, assignee_open_id) if assignee_name else "待定"
        text = CONFIRM_CREATE.format(
            desc=fields.get("任务描述", ""),
            assignee=assignee,
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
        assignee_name = result.get("assignee", "")
        assignee_open_id = result.get("assignee_open_id", "")
        assignee_display = _format_assignee(assignee_name, assignee_open_id) if assignee_name else "未分配"
        text = CONFIRM_QUERY.format(
            desc=desc,
            status=result.get("status", ""),
            assignee=assignee_display,
        )
    else:
        text = "✅ 操作完成"

    return {"reply_text": text}


# ── 文本格式化辅助函数 ────────────────────────────────────


def _format_assignee(name: str, open_id: str | None = None) -> str:
    """构造 @mention 字符串，兼容跨租户外部成员。

    文本消息的 <at user_id="...">Name</at> 语法对同租户和外部成员均有效。
    """
    if not name:
        return ""
    if open_id:
        return f'<at user_id="{open_id}">{name}</at>'
    return f"@{name}"


def _build_intro_text() -> str:
    """构建机器人自我介绍纯文本。"""
    return (
        "👋 群任务追踪助手\n\n"
        "· 每天 09:30 自动分析群消息，提取并跟踪任务\n"
        "· @我 用自然语言新增、完成、修改、查询任务\n\n"
        "发送 /help 查看所有可用指令"
    )


def _build_report_text(
    report_date: date,
    completed: list[dict],
    active: list[dict],
    low_confidence_ids: set[str],
    today: date,
) -> str:
    """构建每日任务报告纯文本（定时触发）。

    使用 <at user_id="..."> 语法，支持跨租户 @mention。
    """
    date_str = report_date.strftime("%Y/%m/%d")
    low_conf_todos = [t for t in active if t.get("record_id") in low_confidence_ids]
    normal_active = [t for t in active if t.get("record_id") not in low_confidence_ids]

    lines: list[str] = [f"📋 任务日报 · {date_str}", ""]

    # ── 昨日完成 ────────────────────────────────────────────
    lines.append(f"✅ 昨日完成（{len(completed)} 项）")
    if completed:
        for todo in completed:
            desc = todo.get("任务描述", "")
            name = todo.get("负责人姓名", "")
            oid = todo.get("负责人open_id", "")
            assignee_str = f"  {_format_assignee(name, oid)}" if name else ""
            lines.append(f"· {desc}{assignee_str}")
    else:
        lines.append("· 暂无")

    lines.append("")

    # ── 进行中（带编号，与 /delete 一致） ───────────────────
    lines.append(f"📌 进行中（{len(normal_active)} 项）")
    if normal_active:
        for idx, todo in enumerate(active, start=1):
            if todo not in normal_active:
                continue
            desc = todo.get("任务描述", "")
            name = todo.get("负责人姓名", "")
            oid = todo.get("负责人open_id", "")
            due = todo.get("预期完成时间", "")
            overdue_str = ""
            if due:
                try:
                    if date.fromisoformat(str(due)) < today:
                        overdue_str = "  ⚠️逾期"
                except (ValueError, TypeError):
                    pass
            assignee_str = f"  {_format_assignee(name, oid)}" if name else ""
            lines.append(f"{idx}. {desc}{assignee_str}{overdue_str}")
    else:
        lines.append("· 暂无待处理任务 🎉")

    # ── 进展待确认 ────────────────────────────────────────────
    if low_conf_todos:
        lines.append("")
        lines.append(f"⚠️ 进展待确认（{len(low_conf_todos)} 项）")
        for todo in low_conf_todos:
            desc = todo.get("任务描述", "")
            lines.append(f"· {desc}（昨日消息提及，请确认状态）")

    lines.append("")
    lines.append("/tasks 查看实时状态 · @我 可更新任务")

    return "\n".join(lines)


def _build_tasks_text(
    active: list[dict],
    today: date,
    header: str = "",
    low_confidence_ids: set[str] | None = None,
) -> str:
    """构建实时任务列表纯文本（/tasks、/update 命令使用）。

    Args:
        active: 进行中的任务列表。
        today: 用于逾期判断的当前日期。
        header: 可选前置摘要行（如"已新增 2 项，完成 1 项"）。
        low_confidence_ids: 进展待确认的 record_id 集合。
    """
    low_conf_ids = low_confidence_ids or set()
    low_conf_todos = [t for t in active if t.get("record_id") in low_conf_ids]
    normal_active = [t for t in active if t.get("record_id") not in low_conf_ids]

    lines: list[str] = []
    if header:
        lines.append(header)
        lines.append("")

    lines.append(f"📌 进行中（{len(normal_active)} 项）")
    if normal_active:
        for idx, todo in enumerate(active, start=1):
            if todo not in normal_active:
                continue
            desc = todo.get("任务描述", "")
            name = todo.get("负责人姓名", "")
            oid = todo.get("负责人open_id", "")
            due = todo.get("预期完成时间", "")
            overdue_str = ""
            if due:
                try:
                    if date.fromisoformat(str(due)) < today:
                        overdue_str = "  ⚠️逾期"
                except (ValueError, TypeError):
                    pass
            assignee_str = f"  {_format_assignee(name, oid)}" if name else ""
            lines.append(f"{idx}. {desc}{assignee_str}{overdue_str}")
    else:
        lines.append("· 暂无待处理任务 🎉")

    if low_conf_todos:
        lines.append("")
        lines.append(f"⚠️ 进展待确认（{len(low_conf_todos)} 项）")
        for todo in low_conf_todos:
            desc = todo.get("任务描述", "")
            lines.append(f"· {desc}（请确认状态）")

    lines.append("")
    lines.append("@我 可更新任务 · /my 查看我的任务")

    return "\n".join(lines)
