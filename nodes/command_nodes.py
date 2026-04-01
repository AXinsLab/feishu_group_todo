"""系统指令处理节点（Slash Commands）。

架构设计：
- COMMAND_REGISTRY：指令注册表，新增指令只需在此处添加条目，图结构无需改动。
- run_command()：统一入口，负责路由和错误捕获，异常时自动回复错误通知到群。
- 各 handle_* 函数：每条指令的具体实现，返回回复文本字符串。

支持的指令：
  /help   · 显示所有可用指令
  /init   · 重载成员 + 修复表格结构
  /tasks  · 查看当前全部任务状态报告
  /my     · 查看发送者本人的待完成任务
  /update · 分析近 24h 消息，更新任务表
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── 帮助文本生成 ───────────────────────────────────────────


def _help_text(prefix: str = "") -> str:
    """生成帮助文本，从 COMMAND_REGISTRY 动态构建。"""
    lines = ["🤖 可用指令列表", "─────────────────────────────────"]
    for cmd, meta in COMMAND_REGISTRY.items():
        lines.append(f"{cmd:<10}· {meta['description']}")
    lines.append("─────────────────────────────────")
    lines.append("使用方式：@机器人 /指令名")
    text = "\n".join(lines)
    if prefix:
        text = prefix + "\n\n" + text
    return text


# ── 各指令 Handler ────────────────────────────────────────


async def handle_help(state: dict, feishu: Any, storage: Any) -> str:
    """显示所有可用指令列表。"""
    return _help_text()


async def handle_init(state: dict, feishu: Any, storage: Any) -> str:
    """重新拉取群成员并写入表格，检查并修复表格结构。"""
    group_id: str = state.get("group_id", "")

    # 1. 拉取并同步群成员
    members = await feishu.get_group_members(group_id)
    await storage.upsert_members(group_id, members)
    member_count = len(members)

    # 2. 检查并修复表格结构
    report = await storage.ensure_schema()
    repaired = [
        f"{table}（{', '.join(actions)}）"
        for table, actions in report.items()
        if actions
    ]
    schema_status = "已修复：\n  · " + "\n  · ".join(repaired) if repaired else "正常（无需修复）"

    return (
        f"✅ 初始化完成\n"
        f"• 成员已同步：{member_count} 人\n"
        f"• 表格结构：{schema_status}"
    )


async def handle_tasks(state: dict, feishu: Any, storage: Any) -> str:
    """立即发送当前任务状态报告（同定时报告格式，含任务编号）。"""
    from nodes.report_nodes import _build_report_card

    group_id: str = state.get("group_id", "")

    # 查询进行中任务（active）
    active_todos = await storage.get_todos(group_id, status="进行中")

    if not active_todos:
        return "📋 当前没有进行中的任务。\n\n如需新增任务，请 @我 并说明操作。"

    from datetime import date as _date
    today = _date.today()
    card = _build_report_card(
        report_date=today,
        completed=[],
        active=active_todos,
        low_confidence_ids=set(),
        today=today,
    )
    import json as _json
    return _json.loads(card["content"])["text"]


async def handle_my(state: dict, feishu: Any, storage: Any) -> str:
    """仅显示发送者本人负责的待完成任务（编号与 /tasks 一致）。"""
    group_id: str = state.get("group_id", "")
    sender_open_id: str = state.get("sender_open_id", "")

    all_active = await storage.get_todos(group_id, status="进行中")

    # 按 open_id 精确匹配
    my_todos: list[tuple[int, dict]] = []
    for i, todo in enumerate(all_active, start=1):
        if sender_open_id and todo.get("负责人open_id") == sender_open_id:
            my_todos.append((i, todo))

    if not my_todos:
        return "🎉 你目前没有待完成的任务！"

    lines = [f"📌 你的待完成任务（{len(my_todos)} 项）", "─────────────────────────────────"]
    for idx, todo in my_todos:
        desc = todo.get("任务描述", "")
        lines.append(f"{idx}. {desc}")

    lines.append("─────────────────────────────────")
    return "\n".join(lines)

async def handle_update(state: dict, feishu: Any, storage: Any) -> str:
    """分析近 24 小时群消息，更新任务表，发送分析报告。

    重复消息去重：filter_messages 基于「来源消息ID」字段过滤已处理消息。
    重复任务去重：LLM analyze_messages 接收当前 active_todos，不重复创建已有任务。
    """
    from nodes.bitable_nodes import (
        build_operations,
        execute_updates,
        fetch_todos,
        filter_messages,
    )
    from nodes.feishu_nodes import (
        fetch_messages,
        refresh_members,
        send_report,
    )
    from nodes.llm_nodes import analyze_messages
    from nodes.report_nodes import generate_report

    group_id: str = state.get("group_id", "")

    # 时间窗口：当前时间往前 24h
    now = datetime.now(tz=timezone.utc)
    time_window_start = now - timedelta(hours=24)
    time_window_end = now

    group_state: dict = {
        "current_group_id": group_id,
        "time_window_start": time_window_start,
        "time_window_end": time_window_end,
        "raw_messages": [],
        "filtered_messages": [],
        "active_todos": [],
        "completed_yesterday": [],
        "member_map": {},
        "llm_analysis": {},
        "update_operations": [],
        "errors": [],
    }

    try:
        # 1. 刷新成员
        result = await refresh_members(group_state, feishu, storage)
        group_state.update(result)

        # 2. 拉取消息（近 24h）
        result = await fetch_messages(group_state, feishu)
        group_state.update(result)
        if result.get("errors"):
            errs = result["errors"]
            return f"❌ 消息拉取失败：{errs[0].get('message', '未知错误')}"

        # 3. 查询当前任务
        result = await fetch_todos(group_state, storage)
        group_state.update(result)

        # 4. 过滤已处理消息（基于来源消息ID去重）
        result = await filter_messages(group_state)
        group_state.update(result)

        filtered = group_state.get("filtered_messages", [])
        active = group_state.get("active_todos", [])

        if not filtered and not active:
            return "📭 近 24 小时内暂无新消息，且当前无进行中的任务。"

        if not filtered:
            return (
                f"📭 近 24 小时内无新消息（或均已处理过）。\n"
                f"当前共有 {len(active)} 项进行中的任务，发送 /tasks 查看详情。"
            )

        # 5. LLM 分析
        result = await analyze_messages(group_state)
        group_state.update(result)

        # 6. 构建操作
        result = await build_operations(group_state)
        group_state.update(result)

        # 7. 执行更新
        result = await execute_updates(group_state, storage)
        group_state.update(result)

        # 8. 生成报告文本
        result = await generate_report(group_state)
        group_state.update(result)

        # 9. 发送报告到群（主动发送，不引用原消息）
        await send_report(group_state, feishu, storage)

        analysis = group_state.get("llm_analysis", {})
        new_count = len(analysis.get("new_tasks", []))
        done_count = len(analysis.get("high_confidence_done", []))
        return (
            f"✅ 更新完成\n"
            f"• 分析消息：{len(filtered)} 条\n"
            f"• 新增任务：{new_count} 项\n"
            f"• 标记完成：{done_count} 项\n"
            f"详细报告已发送到群内。"
        )

    except Exception as exc:
        logger.error("handle_update failed for group %s: %s", group_id, exc, exc_info=True)
        raise  # 由 run_command 统一捕获并格式化错误消息




async def handle_delete(state: dict, feishu: Any, storage: Any) -> str:
    """按编号删除指定任务，支持批量删除。

    格式：/delete 1  或  /delete 1 2 3
    编号与 /tasks、每日报告中的编号一致（进行中任务按写入顺序排列）。
    """
    group_id: str = state.get("group_id", "")
    message_text: str = state.get("message_text", "")

    # 解析编号参数
    parts = message_text.strip().split()[1:]  # 跳过 "/delete"
    if not parts:
        return (
            "❌ 请指定要删除的任务编号。\n"
            "用法：/delete 1  或  /delete 1 2 3\n"
            "发送 /tasks 查看任务编号。"
        )

    # 验证所有参数为正整数
    invalid_parts = [p for p in parts if not p.isdigit() or int(p) < 1]
    if invalid_parts:
        return (
            f"❌ 无效参数：{' '.join(invalid_parts)}\n"
            "编号必须为正整数，多个编号用空格隔开。"
        )

    indices = sorted(set(int(p) for p in parts))

    # 获取当前进行中任务（顺序与显示保持一致）
    active_todos = await storage.get_todos(group_id, status="进行中")
    if not active_todos:
        return "📭 当前没有进行中的任务。"

    # 检查编号是否越界
    out_of_range = [i for i in indices if i > len(active_todos)]
    if out_of_range:
        return (
            f"❌ 编号 {' '.join(str(i) for i in out_of_range)} 超出范围。\n"
            f"当前共有 {len(active_todos)} 项进行中的任务，"
            "发送 /tasks 查看任务列表。"
        )

    # 执行删除
    deleted: list[str] = []
    failed: list[str] = []
    for i in indices:
        todo = active_todos[i - 1]
        desc = todo.get("任务描述", f"任务{i}")
        record_id = todo.get("record_id", "")
        try:
            await storage.delete_todo(record_id)
            deleted.append(f"{i}. {desc}")
        except Exception as exc:
            logger.error("handle_delete: failed to delete record %s: %s", record_id, exc)
            failed.append(f"{i}. {desc}（失败：{exc}）")

    lines: list[str] = []
    if deleted:
        lines.append(f"✅ 已删除 {len(deleted)} 项任务：")
        for d in deleted:
            lines.append(f"  · {d}")
    if failed:
        lines.append(f"❌ {len(failed)} 项删除失败：")
        for f_item in failed:
            lines.append(f"  · {f_item}")
    return "\n".join(lines)

# ── 指令注册表（在 handler 定义后注册，避免前向引用）─────────


COMMAND_REGISTRY: dict[str, dict] = {
    "/help":   {"description": "显示所有可用指令",               "handler": handle_help},
    "/init":   {"description": "重载成员 + 检查/修复表格结构",     "handler": handle_init},
    "/tasks":  {"description": "查看当前全部任务状态报告（含编号）", "handler": handle_tasks},
    "/my":     {"description": "查看我的待完成任务",              "handler": handle_my},
    "/update": {"description": "分析近 24h 消息，更新任务表",      "handler": handle_update},
    "/delete": {"description": "按编号删除任务，如 /delete 1 2 3", "handler": handle_delete},
}


# ── 统一指令入口（带错误处理）────────────────────────────────


async def run_command(
    cmd: str,
    state: dict,
    feishu: Any,
    storage: Any,
) -> str:
    """统一指令路由入口，自动捕获异常并返回错误文本。

    Args:
        cmd: 指令名称（如 "/init"），已 lower()。
        state: 当前 MessageState。
        feishu: FeishuClient 实例。
        storage: StorageInterface 实例。

    Returns:
        回复文本字符串（由 send_reply 节点负责发送）。
    """
    entry = COMMAND_REGISTRY.get(cmd)
    if entry is None:
        return _help_text(prefix=f"❓ 未知指令 `{cmd}`")

    try:
        result = await entry["handler"](state, feishu, storage)
        return result
    except Exception as exc:
        logger.error(
            "Command %s failed for group %s: %s",
            cmd,
            state.get("group_id", ""),
            exc,
            exc_info=True,
        )
        return (
            f"❌ 指令 `{cmd}` 执行失败\n"
            f"错误详情：{exc}\n\n"
            f"请检查服务日志或联系管理员。"
        )
