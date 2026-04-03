"""系统指令处理节点（Slash Commands）。

架构设计：
- COMMAND_REGISTRY：指令注册表，新增指令只需在此处添加条目，图结构无需改动。
- run_command()：统一入口，负责路由和错误捕获，异常时自动回复错误通知到群。
- 各 handle_* 函数：每条指令的具体实现，返回回复文本字符串。

支持的指令：
  /help       · 显示所有可用指令
  /init       · 重载成员 + 修复表格结构
  /tasks      · 查看当前全部任务状态报告
  /my         · 查看发送者本人的待完成任务
  /update     · 分析近 24h 消息，更新任务表
  /about_you  · 发送机器人自我介绍
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── 帮助文本生成 ──────────────────────────────────────────


def _build_help_text(prefix: str = "") -> str:
    """生成帮助指令纯文本，从 COMMAND_REGISTRY 动态构建。

    prefix 不为空时在列表上方显示提示文字（如"未知指令"）。
    """
    lines: list[str] = []
    if prefix:
        lines.append(prefix)
        lines.append("")

    lines.append("🤖 可用指令列表")
    lines.append("")
    for cmd, meta in COMMAND_REGISTRY.items():
        lines.append(f"{cmd}  · {meta['description']}")

    lines.append("")
    lines.append("使用方式：@机器人 /指令名")
    return "\n".join(lines)


# ── 各指令 Handler ────────────────────────────────────────


async def handle_help(state: dict, feishu: Any, storage: Any) -> str:
    """显示所有可用指令列表。"""
    return _build_help_text()


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
    """立即发送当前任务状态报告（含任务编号）。"""
    from nodes.report_nodes import _build_tasks_text

    group_id: str = state.get("group_id", "")
    active_todos = await storage.get_todos(group_id, status="进行中")

    if not active_todos:
        return "📋 当前没有进行中的任务。\n\n如需新增任务，请 @我 并说明操作。"

    from datetime import date as _date
    return _build_tasks_text(active=active_todos, today=_date.today())


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
    )
    from nodes.llm_nodes import analyze_messages
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
        "bot_open_id": state.get("bot_open_id", ""),
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

        # 8. 用 in-memory 方式构建更新后的任务列表（避免 Bitable API 最终一致性问题）
        analysis = group_state.get("llm_analysis", {})
        done_ids = set(analysis.get("high_confidence_done", []))
        old_active = group_state.get("active_todos", [])

        # 移除已完成任务
        updated_active = [t for t in old_active if t.get("record_id") not in done_ids]
        # 追加本次新增的任务
        for task in analysis.get("new_tasks", []):
            updated_active.append({
                "任务描述": task.get("description", ""),
                "负责人姓名": task.get("assignee_name") or "",
                "负责人open_id": task.get("assignee_open_id") or "",
                "状态": "进行中",
            })

        new_count = len(analysis.get("new_tasks", []))
        done_count = len(done_ids)

        # 9. 生成并返回更新后的任务列表文本
        from datetime import date as _date
        from nodes.report_nodes import _build_tasks_text

        today = _date.today()
        header_parts = []
        if new_count:
            header_parts.append(f"已新增 {new_count} 项")
        if done_count:
            header_parts.append(f"标记完成 {done_count} 项")
        header = "✅ " + "，".join(header_parts) if header_parts else "📊 任务表已同步"

        return _build_tasks_text(
            active=updated_active,
            today=today,
            header=header,
            low_confidence_ids=set(analysis.get("low_confidence_done", [])),
        )

    except Exception as exc:
        logger.error("handle_update failed for group %s: %s", group_id, exc, exc_info=True)
        raise  # 由 run_command 统一捕获并格式化错误消息




async def handle_about_you(state: dict, feishu: Any, storage: Any) -> str:
    """发送机器人自我介绍（同入群时一致）。"""
    from nodes.report_nodes import _build_intro_text
    return _build_intro_text()


def _parse_delete_indices(args_str: str, total: int) -> list[int] | str:
    """将 /delete 参数解析为有序的 1-based 编号列表。

    支持格式（可混合使用）：
    - 空格分隔整数：  1 2 3
    - 括号切片：      [:]  [:4]  [2:]  [2:5]   （含首尾，1-based）
    - 括号列表/范围：  [1,3,5]  [1-3,5,8]

    返回 list[int] 或错误提示字符串。
    """
    _USAGE = (
        "用法示例：\n"
        "  /delete 1 2 3      · 删除第 1、2、3 项\n"
        "  /delete [1-3,5]    · 删除第 1~3 和第 5 项\n"
        "  /delete [:4]       · 删除前 4 项\n"
        "  /delete [2:]       · 从第 2 项删到末尾\n"
        "  /delete [:]        · 删除全部任务\n"
        "发送 /tasks 查看任务编号。"
    )

    args_str = args_str.strip()
    if not args_str:
        return f"❌ 请指定要删除的任务编号。\n{_USAGE}"

    indices: set[int] = set()

    if args_str.startswith("[") and args_str.endswith("]"):
        inner = args_str[1:-1].strip()

        if ":" in inner:
            # 切片语法：[start:end]，含首尾，1-based
            halves = inner.split(":", 1)
            start_s, end_s = halves[0].strip(), halves[1].strip()
            try:
                start = int(start_s) if start_s else 1
                end = int(end_s) if end_s else total
            except ValueError:
                return f"❌ 切片参数格式错误：{args_str}\n正确格式示例：[:4]、[2:]、[2:5]、[:]"
            if start < 1:
                return f"❌ 起始编号不能小于 1（当前：{start}）"
            if end > total:
                return f"❌ 结束编号 {end} 超出范围（共 {total} 项）"
            if start > end:
                return f"❌ 起始编号 {start} 不能大于结束编号 {end}"
            indices = set(range(start, end + 1))

        else:
            # 列表/范围语法：逗号分隔，支持 m-n 连续范围
            for item in inner.split(","):
                item = item.strip()
                if not item:
                    continue
                if "-" in item:
                    parts = item.split("-", 1)
                    try:
                        lo, hi = int(parts[0].strip()), int(parts[1].strip())
                    except (ValueError, IndexError):
                        return f"❌ 无效范围：{item}，正确格式如 1-3"
                    if lo < 1 or hi < 1:
                        return f"❌ 编号不能小于 1（当前：{item}）"
                    if lo > hi:
                        return f"❌ 范围起始 {lo} 不能大于结束 {hi}"
                    if hi > total:
                        return f"❌ 编号 {hi} 超出范围（共 {total} 项）"
                    indices.update(range(lo, hi + 1))
                else:
                    try:
                        n = int(item)
                    except ValueError:
                        return f"❌ 无效参数：{item}，编号必须为正整数"
                    if n < 1:
                        return f"❌ 编号不能小于 1（当前：{n}）"
                    if n > total:
                        return f"❌ 编号 {n} 超出范围（共 {total} 项）"
                    indices.add(n)
    else:
        # 兼容原有空格分隔格式
        for part in args_str.split():
            if not part.isdigit() or int(part) < 1:
                return f"❌ 无效参数：{part}，编号必须为正整数\n{_USAGE}"
            n = int(part)
            if n > total:
                return f"❌ 编号 {n} 超出范围（共 {total} 项）"
            indices.add(n)

    if not indices:
        return f"❌ 未解析到有效编号，请检查参数格式。\n{_USAGE}"

    return sorted(indices)


async def handle_delete(state: dict, feishu: Any, storage: Any) -> str:
    """按编号删除指定任务，支持多种参数格式。

    格式：
      /delete 1 2 3       空格分隔编号
      /delete [1-3,5,8]   括号内连续范围或离散编号
      /delete [:4]        前 4 项（切片语法）
      /delete [2:]        从第 2 项到末尾
      /delete [:]         全部删除
    编号与 /tasks、每日报告中的编号一致。
    """
    group_id: str = state.get("group_id", "")
    message_text: str = state.get("message_text", "")

    # 取 "/delete" 之后的全部内容（保留括号内空格）
    args_str = message_text.strip()[len("/delete"):].strip()

    active_todos = await storage.get_todos(group_id, status="进行中")
    if not active_todos and not args_str:
        return "📭 当前没有进行中的任务。"

    # 无参数时先提示用法（不依赖 active_todos 是否为空）
    if not args_str:
        return (
            "❌ 请指定要删除的任务编号。\n"
            "用法示例：\n"
            "  /delete 1 2 3      · 删除第 1、2、3 项\n"
            "  /delete [1-3,5]    · 删除第 1~3 和第 5 项\n"
            "  /delete [:4]       · 删除前 4 项\n"
            "  /delete [:]        · 删除全部任务\n"
            "发送 /tasks 查看任务编号。"
        )

    if not active_todos:
        return "📭 当前没有进行中的任务。"

    result = _parse_delete_indices(args_str, len(active_todos))
    if isinstance(result, str):
        return result  # 错误提示

    indices: list[int] = result

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
            logger.error(
                "handle_delete: failed to delete record %s: %s", record_id, exc
            )
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
    "/help":      {"description": "显示所有可用指令",               "handler": handle_help},
    "/about_you": {"description": "发送机器人自我介绍",              "handler": handle_about_you},
    "/init":      {"description": "重载成员 + 检查/修复表格结构",     "handler": handle_init},
    "/tasks":     {"description": "查看当前全部任务状态报告（含编号）", "handler": handle_tasks},
    "/my":        {"description": "查看我的待完成任务",              "handler": handle_my},
    "/update":    {"description": "分析近 24h 消息，更新任务表",      "handler": handle_update},
    "/delete":    {"description": "删除任务：/delete 1 2  /delete [1-3,5]  /delete [:]", "handler": handle_delete},
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
        return _build_help_text(prefix=f"❓ 未知指令 `{cmd}`")

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
