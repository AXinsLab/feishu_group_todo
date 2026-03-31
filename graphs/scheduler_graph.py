"""SchedulerGraph：定时总结工作流。

触发时机：POST /webhook/scheduler（由 VPS Crontab 调用）。
每日 09:30 分析昨日群消息，更新任务状态，发送每日报告。
支持 SqliteSaver Checkpointer 实现断点续跑。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from schemas.state import SchedulerState

logger = logging.getLogger(__name__)


def build_scheduler_graph(
    storage: Any,
    feishu: Any,
) -> Any:
    """编译并返回 SchedulerGraph。

    Args:
        storage: StorageInterface 实现实例。
        feishu: FeishuClient 实例。

    Returns:
        已编译的 LangGraph CompiledGraph（不含 Checkpointer）。
        Checkpointer 在调用时通过 config 传入。
    """
    from nodes.bitable_nodes import (
        build_operations,
        execute_updates,
        fetch_todos,
        filter_messages,
    )
    from nodes.feishu_nodes import (
        fetch_groups,
        fetch_messages,
        refresh_members,
        send_empty_report,
        send_report,
    )
    from nodes.llm_nodes import analyze_messages
    from nodes.report_nodes import generate_report

    # ── 主节点：循环处理所有群 ────────────────────────────

    async def _fetch_groups(
        state: SchedulerState,
    ) -> dict:
        return await fetch_groups(state, storage)

    async def _process_all_groups(
        state: SchedulerState,
    ) -> dict:
        """核心节点：按顺序处理每个群的定时总结任务。

        使用 Python for 循环顺序处理，错误不中断其他群。
        """
        group_list: list[dict] = state.get("group_list", [])
        all_errors: list[dict] = []

        for group in group_list:
            group_id = group.get("群ID", "")
            if not group_id:
                continue

            logger.info(
                "Processing scheduler for group: %s",
                group_id,
            )

            # 为当前群构建子状态（复用父状态的时间窗口）
            group_state: dict = {
                **state,
                "current_group_id": group_id,
                "raw_messages": [],
                "filtered_messages": [],
                "active_todos": [],
                "completed_yesterday": [],
                "member_map": {},
                "llm_analysis": {},
                "update_operations": [],
            }

            try:
                group_state = await _process_single_group(group_state)
                all_errors.extend(group_state.get("errors", []))
            except Exception as exc:
                logger.error(
                    "Group %s processing failed: %s",
                    group_id,
                    exc,
                )
                all_errors.append(
                    {
                        "group_id": group_id,
                        "type": "system",
                        "message": (f"群 {group_id} 处理异常：{exc}"),
                    }
                )

        return {"errors": all_errors}

    async def _process_single_group(
        group_state: dict,
    ) -> dict:
        """处理单个群的完整定时总结流程。"""
        # 1. 刷新成员
        result = await refresh_members(group_state, feishu, storage)
        group_state.update(result)

        # 2. 拉取消息
        result = await fetch_messages(group_state, feishu)
        group_state.update(result)
        if result.get("errors"):
            return group_state

        # 3. 查询任务
        result = await fetch_todos(group_state, storage)
        group_state.update(result)
        if result.get("errors"):
            return group_state

        # 4. 消息硬过滤
        result = await filter_messages(group_state)
        group_state.update(result)

        # 5. 条件分流：空消息且无进行中任务
        has_messages = bool(group_state.get("filtered_messages"))
        has_active = bool(group_state.get("active_todos"))

        if not has_messages and not has_active:
            await send_empty_report(group_state, feishu)
            return group_state

        # 6. LLM 分析
        result = await analyze_messages(group_state)
        group_state.update(result)

        # 7. 构建操作
        result = await build_operations(group_state)
        group_state.update(result)

        # 8. 执行更新
        result = await execute_updates(group_state, storage)
        group_state.update(result)

        # 9. 生成报告
        result = await generate_report(group_state)
        group_state.update(result)

        # 10. 发送报告
        await send_report(group_state, feishu, storage)

        return group_state

    async def _handle_errors(
        state: SchedulerState,
    ) -> dict:
        """汇总处理所有群的错误。

        业务错误 → 发到对应群聊
        系统错误 → 发到 OPS_CHAT_ID
        """
        import json

        errors: list[dict] = state.get("errors", [])
        if not errors:
            return {}

        # 延迟加载配置，避免测试环境无 .env 文件时报错
        ops_chat_id = ""
        try:
            from config import get_settings

            ops_chat_id = get_settings().ops_chat_id
        except Exception:
            logger.warning("get_settings failed in handle_errors")

        for error in errors:
            error_type = error.get("type", "system")
            message = error.get("message", "未知错误")
            group_id = error.get("group_id", "")

            content = json.dumps(
                {"text": f"⚠️ {message}"},
                ensure_ascii=False,
            )

            try:
                if error_type == "business" and group_id:
                    await feishu.send_message(group_id, content)
                elif ops_chat_id:
                    await feishu.send_message(ops_chat_id, content)
            except Exception as exc:
                logger.error("handle_errors send failed: %s", exc)

        return {}

    # ── 构建 Graph ────────────────────────────────────────

    graph = StateGraph(SchedulerState)

    graph.add_node("fetch_groups", _fetch_groups)
    graph.add_node("process_all_groups", _process_all_groups)
    graph.add_node("handle_errors", _handle_errors)

    graph.set_entry_point("fetch_groups")
    graph.add_edge("fetch_groups", "process_all_groups")
    graph.add_edge("process_all_groups", "handle_errors")
    graph.add_edge("handle_errors", END)

    return graph.compile()
