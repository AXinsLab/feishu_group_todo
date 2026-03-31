"""OnboardGraph：机器人入群初始化工作流。

触发时机：im.chat.member.bot.added_v1 事件。
两条路径：
- 首次入群：检查/创建多维表格 → 写群配置 → 拉成员 → 发自我介绍
- 重复入群：仅刷新成员表
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from schemas.state import OnboardState

logger = logging.getLogger(__name__)


def build_onboard_graph(
    storage: Any,
    feishu: Any,
) -> Any:
    """编译并返回 OnboardGraph。

    Args:
        storage: StorageInterface 实现实例。
        feishu: FeishuClient 实例。

    Returns:
        已编译的 LangGraph CompiledGraph。
    """
    # ── 节点函数（通过 functools.partial 注入依赖）────────

    from nodes.bitable_nodes import (
        check_bitable_exists,
        check_group_exists,
        create_bitable,
        fetch_and_write_members,
        write_group_config,
    )
    from nodes.feishu_nodes import (
        parse_onboard_event,
        send_introduction,
    )

    async def _parse_onboard_event(
        state: OnboardState,
    ) -> dict:
        return await parse_onboard_event(state)

    async def _check_group_exists(
        state: OnboardState,
    ) -> dict:
        return await check_group_exists(state, storage, feishu)

    async def _check_bitable_exists(
        state: OnboardState,
    ) -> dict:
        return await check_bitable_exists(state, storage)

    async def _create_bitable(
        state: OnboardState,
    ) -> dict:
        return await create_bitable(state, feishu, storage)

    async def _write_group_config(
        state: OnboardState,
    ) -> dict:
        return await write_group_config(state, storage)

    async def _fetch_and_write_members(
        state: OnboardState,
    ) -> dict:
        return await fetch_and_write_members(state, feishu, storage)

    async def _send_introduction(
        state: OnboardState,
    ) -> dict:
        return await send_introduction(state, feishu)

    async def _refresh_members_only(
        state: OnboardState,
    ) -> dict:
        """重复入群时仅刷新成员表。"""
        from nodes.feishu_nodes import refresh_members

        return await refresh_members(state, feishu, storage)

    # ── 条件路由函数 ──────────────────────────────────────

    def route_first_time(state: OnboardState) -> str:
        """根据是否首次入群分流。"""
        if state.get("is_first_time"):
            return "check_bitable_exists"
        return "refresh_members_only"

    def route_bitable_exists(
        state: OnboardState,
    ) -> str:
        """根据多维表格是否存在分流。"""
        if state.get("bitable_exists"):
            return "write_group_config"
        return "create_bitable"

    # ── 构建 Graph ────────────────────────────────────────

    graph = StateGraph(OnboardState)

    graph.add_node("parse_onboard_event", _parse_onboard_event)
    graph.add_node("check_group_exists", _check_group_exists)
    graph.add_node("check_bitable_exists", _check_bitable_exists)
    graph.add_node("create_bitable", _create_bitable)
    graph.add_node("write_group_config", _write_group_config)
    graph.add_node("fetch_and_write_members", _fetch_and_write_members)
    graph.add_node("send_introduction", _send_introduction)
    graph.add_node("refresh_members_only", _refresh_members_only)

    graph.set_entry_point("parse_onboard_event")
    graph.add_edge("parse_onboard_event", "check_group_exists")
    graph.add_conditional_edges(
        "check_group_exists",
        route_first_time,
        {
            "check_bitable_exists": "check_bitable_exists",
            "refresh_members_only": "refresh_members_only",
        },
    )
    graph.add_conditional_edges(
        "check_bitable_exists",
        route_bitable_exists,
        {
            "write_group_config": "write_group_config",
            "create_bitable": "create_bitable",
        },
    )
    graph.add_edge("create_bitable", "write_group_config")
    graph.add_edge("write_group_config", "fetch_and_write_members")
    graph.add_edge("fetch_and_write_members", "send_introduction")
    graph.add_edge("send_introduction", END)
    graph.add_edge("refresh_members_only", END)

    return graph.compile()
