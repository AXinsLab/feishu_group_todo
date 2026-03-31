"""MessageGraph：@机器人 消息响应工作流。

触发时机：im.message.receive_v1 事件（@机器人 消息）。
流程：解析事件 → 查询 Todo → 意图分类
  → 无关：拒绝回复
  → 相关：查成员 → [未命中] 刷新重试 → 执行操作 → 确认回复
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from schemas.models import OperationType
from schemas.state import MessageState

logger = logging.getLogger(__name__)


def build_message_graph(
    storage: Any,
    feishu: Any,
) -> Any:
    """编译并返回 MessageGraph。

    Args:
        storage: StorageInterface 实现实例。
        feishu: FeishuClient 实例。

    Returns:
        已编译的 LangGraph CompiledGraph。
    """
    from nodes.bitable_nodes import (
        execute_operation,
        fetch_all_todos,
    )
    from nodes.feishu_nodes import (
        parse_event,
        send_reply,
    )
    from nodes.llm_nodes import classify_intent
    from nodes.report_nodes import (
        build_confirm_reply,
        build_reject_reply,
    )

    # ── 节点包装函数 ──────────────────────────────────────

    async def _parse_event(state: MessageState) -> dict:
        return await parse_event(state)

    async def _fetch_all_todos(
        state: MessageState,
    ) -> dict:
        return await fetch_all_todos(state, storage)

    async def _classify_intent(
        state: MessageState,
    ) -> dict:
        return await classify_intent(state)

    async def _build_reject_reply(
        state: MessageState,
    ) -> dict:
        return await build_reject_reply(state)

    async def _fetch_members(
        state: MessageState,
    ) -> dict:
        """从存储层查询成员表，构建 member_map。"""
        group_id: str = state.get("group_id", "")
        try:
            members = await storage.get_members(group_id)
            member_map: dict[str, dict] = {
                m.get("open_id", ""): m for m in members if m.get("open_id")
            }
            return {"member_map": member_map}
        except Exception as exc:
            logger.error("_fetch_members failed: %s", exc)
            return {"member_map": {}}

    async def _refresh_members(
        state: MessageState,
    ) -> dict:
        """刷新成员表（最多触发一次）。"""
        from nodes.feishu_nodes import refresh_members

        result = await refresh_members(state, feishu, storage)
        result["member_refresh_attempted"] = True
        return result

    async def _resolve_operation(
        state: MessageState,
    ) -> dict:
        """根据 intent 和 member_map 最终确定 target_todo。"""
        from nodes.llm_nodes import _find_target_todo

        intent_result = state.get("_intent_result", {})
        task_desc = intent_result.get("task_description")
        all_todos: list[dict] = state.get("all_todos", [])

        if task_desc and not state.get("target_todo"):
            target = _find_target_todo(task_desc, all_todos)
            if target:
                return {"target_todo": target}
        return {}

    async def _execute_operation(
        state: MessageState,
    ) -> dict:
        return await execute_operation(state, storage)

    async def _build_confirm_reply(
        state: MessageState,
    ) -> dict:
        return await build_confirm_reply(state)

    async def _send_reply(
        state: MessageState,
    ) -> dict:
        return await send_reply(state, feishu)

    # ── 条件路由函数 ──────────────────────────────────────

    def route_intent(state: MessageState) -> str:
        """意图分类后路由：无关 vs 相关操作。"""
        op = state.get("operation_type", "")
        if op == OperationType.UNRELATED:
            return "build_reject_reply"
        return "fetch_members"

    def route_member_found(state: MessageState) -> str:
        """成员查询后路由：命中则执行操作，否则刷新。"""
        op = state.get("operation_type", "")
        member_map: dict = state.get("member_map", {})
        intent_result = state.get("_intent_result", {})
        assignee_name = intent_result.get("assignee_name")

        # 新增/修改 且指定了负责人时检查是否能匹配到
        needs_assignee = op in (OperationType.CREATE, OperationType.UPDATE)
        if needs_assignee and assignee_name:
            matched = _assignee_in_member_map(assignee_name, member_map)
            if not matched:
                already_retried = state.get("member_refresh_attempted", False)
                if not already_retried:
                    return "refresh_members"
        return "resolve_operation"

    def route_after_refresh(
        state: MessageState,
    ) -> str:
        """刷新成员后固定进入 resolve_operation。"""
        return "resolve_operation"

    # ── 构建 Graph ────────────────────────────────────────

    graph = StateGraph(MessageState)

    graph.add_node("parse_event", _parse_event)
    graph.add_node("fetch_all_todos", _fetch_all_todos)
    graph.add_node("classify_intent", _classify_intent)
    graph.add_node("build_reject_reply", _build_reject_reply)
    graph.add_node("fetch_members", _fetch_members)
    graph.add_node("refresh_members", _refresh_members)
    graph.add_node("resolve_operation", _resolve_operation)
    graph.add_node("execute_operation", _execute_operation)
    graph.add_node("build_confirm_reply", _build_confirm_reply)
    graph.add_node("send_reply", _send_reply)

    graph.set_entry_point("parse_event")
    graph.add_edge("parse_event", "fetch_all_todos")
    graph.add_edge("fetch_all_todos", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "build_reject_reply": "build_reject_reply",
            "fetch_members": "fetch_members",
        },
    )
    graph.add_edge("build_reject_reply", "send_reply")
    graph.add_conditional_edges(
        "fetch_members",
        route_member_found,
        {
            "refresh_members": "refresh_members",
            "resolve_operation": "resolve_operation",
        },
    )
    graph.add_edge("refresh_members", "resolve_operation")
    graph.add_edge("resolve_operation", "execute_operation")
    graph.add_edge("execute_operation", "build_confirm_reply")
    graph.add_edge("build_confirm_reply", "send_reply")
    graph.add_edge("send_reply", END)

    return graph.compile()


def _assignee_in_member_map(assignee_name: str, member_map: dict) -> bool:
    """检查负责人姓名是否能在成员表中找到匹配。"""
    name_lower = assignee_name.lower()
    for member in member_map.values():
        if isinstance(member, dict):
            candidates = [
                member.get("name", ""),
                member.get("en_name", ""),
                member.get("nickname", ""),
            ]
            if any(c.lower() == name_lower for c in candidates if c):
                return True
    return False
