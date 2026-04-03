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

    card_dict = _build_report_card(
        report_date=report_date,
        completed=completed,
        active=active,
        low_confidence_ids=low_confidence_ids,
        today=today,
    )
    return {"reply_text": _card_json(card_dict)}


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
        due = fields.get("预期完成时间") or "待确认"
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


# ── 消息卡片构建辅助函数 ──────────────────────────────────


def _card_json(card: dict) -> str:
    """将 Interactive Card dict 序列化为 send_reply/send_report 可识别的 JSON 字符串。

    返回格式：{"msg_type": "interactive", "content": "<card json string>"}
    send_reply 和 send_report 均通过检测 msg_type 键自动路由到 interactive 模式。
    """
    return json.dumps(
        {
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        ensure_ascii=False,
    )


def _build_intro_card() -> dict:
    """构建机器人自我介绍 Interactive Card（返回原始 card dict）。

    供 send_introduction 和 handle_about_you 共用。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "👋 群任务追踪助手"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "· 每天 **09:30** 自动分析群消息，提取并跟踪任务\n"
                        "· @我 用自然语言**新增、完成、修改、查询**任务"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "发送 /help 查看所有可用指令"}
                ],
            },
        ],
    }


def _format_assignee(name: str, open_id: str | None = None) -> str:
    """用于 msg_type="text" 消息的负责人格式（<at user_id="..."> 语法）。"""
    if not name:
        return ""
    if open_id:
        return f'<at user_id="{open_id}">{name}</at>'
    return f"@{name}"


def _format_assignee_md(name: str, open_id: str | None = None) -> str:
    """用于 Interactive Card lark_md 内容的负责人格式。

    飞书卡片 lark_md @mention 语法（官方文档）：<at id=open_id></at>
    属性值不加引号，与文本消息的 <at user_id="..."> 写法不同。
    """
    if not name:
        return ""
    if open_id:
        return f"<at id={open_id}></at>"
    return f"@{name}"


def _build_report_card(
    report_date: date,
    completed: list[dict],
    active: list[dict],
    low_confidence_ids: set[str],
    today: date,
) -> dict:
    """构建飞书 Interactive Card 格式的任务报告（返回原始 card dict）。

    Args:
        report_date: 报告对应日期（昨日）。
        completed: 昨日完成的任务列表。
        active: 当前进行中的任务列表。
        low_confidence_ids: 低置信度完成的 record_id 集合。
        today: 今天的日期（用于逾期判断）。

    Returns:
        飞书 Interactive Card 原始字典（供 _card_json 包装后发送）。
    """
    date_str = report_date.strftime("%Y/%m/%d")

    low_conf_todos = [
        t for t in active if t.get("record_id") in low_confidence_ids
    ]
    normal_active = [
        t for t in active if t.get("record_id") not in low_confidence_ids
    ]

    elements: list[dict] = []

    # ── 昨日完成（始终显示，含 0 项） ────────────────────────
    comp_lines = [f"**✅ 昨日完成（{len(completed)} 项）**"]
    for todo in completed:
        desc = todo.get("任务描述", "")
        assignee = todo.get("负责人姓名", "")
        assignee_open_id = todo.get("负责人open_id", "")
        assignee_str = f"  {_format_assignee_md(assignee, assignee_open_id)}" if assignee else ""
        comp_lines.append(f"~~· {desc}{assignee_str}~~")
    elements.append(
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(comp_lines)}}
    )
    elements.append({"tag": "hr"})

    # ── 待完成（带全局编号，与 /delete 编号一致） ────────────
    active_lines = [f"**📌 待完成（{len(normal_active)} 项）**"]
    for idx, todo in enumerate(active, start=1):
        if todo not in normal_active:
            continue
        desc = todo.get("任务描述", "")
        assignee = todo.get("负责人姓名", "")
        assignee_open_id = todo.get("负责人open_id", "")
        due = todo.get("预期完成时间", "")
        overdue_str = ""
        if due:
            try:
                due_date = date.fromisoformat(str(due))
                if due_date < today:
                    overdue_str = " ⚠️逾期"
            except (ValueError, TypeError):
                pass
        assignee_str = f"  {_format_assignee_md(assignee, assignee_open_id)}" if assignee else ""
        active_lines.append(f"{idx}. {desc}{assignee_str}{overdue_str}")
    elements.append(
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(active_lines)}}
    )

    # ── 进展待确认 ────────────────────────────────────────────
    if low_conf_todos:
        elements.append({"tag": "hr"})
        low_lines = [f"**⚠️ 进展待确认（{len(low_conf_todos)} 项）**"]
        for todo in low_conf_todos:
            desc = todo.get("任务描述", "")
            low_lines.append(f"· {desc}（昨日消息提及但状态不明，请确认）")
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(low_lines)}}
        )

    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "如需更新任务，@我 并说明操作  ·  /tasks 查看实时状态",
                }
            ],
        }
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"📋 每日任务追踪报告 · {date_str}",
            },
            "template": "blue",
        },
        "elements": elements,
    }
