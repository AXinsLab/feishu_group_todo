"""MessageGraph：@机器人 消息响应工作流。

触发时机：im.message.receive_v1 事件（@机器人 消息）。
流程：解析事件 → 查询 Todo → 预加载群成员 → 意图分类
  → 无关：拒绝回复
  → 相关：执行操作 → 确认回复
成员信息在 classify_intent 之前加载，使 LLM 能感知群成员姓名，
支持自然语言负责人识别（如"让甘鑫..."）。
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
    """编译并返回 MessageGraph。"""
    from nodes.bitable_nodes import (
        execute_operation,
        fetch_all_todos,
    )
    from nodes.command_nodes import run_command
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

    async def _fetch_all_todos(state: MessageState) -> dict:
        return await fetch_all_todos(state, storage)

    async def _fetch_members(state: MessageState) -> dict:
        """从存储层查询成员表，构建 member_map（在 classify_intent 前执行）。"""
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

    async def _classify_intent(state: MessageState) -> dict:
        return await classify_intent(state)

    async def _build_reject_reply(state: MessageState) -> dict:
        return await build_reject_reply(state)

    async def _resolve_operation(state: MessageState) -> dict:
        """根据 intent 最终确定 target_todo。"""
        from nodes.llm_nodes import _find_target_todo

        intent_result = state.get("_intent_result", {})
        task_desc = intent_result.get("task_description")
        all_todos: list[dict] = state.get("all_todos", [])

        if task_desc and not state.get("target_todo"):
            target = _find_target_todo(task_desc, all_todos)
            if target:
                return {"target_todo": target}
        return {}

    async def _execute_operation(state: MessageState) -> dict:
        return await execute_operation(state, storage)

    async def _build_confirm_reply(state: MessageState) -> dict:
        return await build_confirm_reply(state)

    async def _send_reply(state: MessageState) -> dict:
        return await send_reply(state, feishu)

    async def _execute_command(state: MessageState) -> dict:
        """执行系统 /指令，返回回复文本。"""
        cmd: str = state.get("system_command", "")
        reply_text = await run_command(cmd, state, feishu, storage)
        return {"reply_text": reply_text}

    # ── 条件路由函数 ──────────────────────────────────────

    def route_after_parse(state: MessageState) -> str:
        """parse_event 后分流：系统指令 vs 普通消息。"""
        if state.get("system_command"):
            return "execute_command"
        return "fetch_all_todos"

    def route_intent(state: MessageState) -> str:
        """意图分类后路由：无关 vs 相关操作。"""
        op = state.get("operation_type", "")
        if op == OperationType.UNRELATED:
            return "build_reject_reply"
        return "resolve_operation"

    # ── 构建 Graph ────────────────────────────────────────

    graph = StateGraph(MessageState)

    graph.add_node("parse_event", _parse_event)
    graph.add_node("execute_command", _execute_command)
    graph.add_node("fetch_all_todos", _fetch_all_todos)
    graph.add_node("fetch_members", _fetch_members)
    graph.add_node("classify_intent", _classify_intent)
    graph.add_node("build_reject_reply", _build_reject_reply)
    graph.add_node("resolve_operation", _resolve_operation)
    graph.add_node("execute_operation", _execute_operation)
    graph.add_node("build_confirm_reply", _build_confirm_reply)
    graph.add_node("send_reply", _send_reply)

    graph.set_entry_point("parse_event")
    # parse_event 后分流：系统指令直接执行，普通消息走 LLM 流程
    graph.add_conditional_edges(
        "parse_event",
        route_after_parse,
        {
            "execute_command": "execute_command",
            "fetch_all_todos": "fetch_all_todos",
        },
    )
    graph.add_edge("execute_command", "send_reply")
    graph.add_edge("fetch_all_todos", "fetch_members")
    graph.add_edge("fetch_members", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "build_reject_reply": "build_reject_reply",
            "resolve_operation": "resolve_operation",
        },
    )
    graph.add_edge("build_reject_reply", "send_reply")
    graph.add_edge("resolve_operation", "execute_operation")
    graph.add_edge("execute_operation", "build_confirm_reply")
    graph.add_edge("build_confirm_reply", "send_reply")
    graph.add_edge("send_reply", END)

    return graph.compile()
