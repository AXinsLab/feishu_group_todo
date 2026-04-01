"""LangGraph 三个 Graph 的 State 定义。

每个 Graph 使用独立的 TypedDict 描述其状态结构。
SchedulerState.errors 使用 Annotated + operator.add 累加器，
支持跨群错误汇总。
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, TypedDict


class SchedulerState(TypedDict):
    """定时总结 Graph 状态。"""

    trigger_time: datetime
    time_window_start: datetime
    time_window_end: datetime
    group_list: list[dict]
    current_group_id: str
    raw_messages: list[dict]
    filtered_messages: list[dict]
    active_todos: list[dict]
    completed_yesterday: list[dict]
    member_map: dict[str, dict]
    llm_analysis: dict
    update_operations: list[dict]
    # 使用 operator.add 作为 reducer，跨节点累积错误列表
    errors: Annotated[list[dict], operator.add]


class MessageState(TypedDict):
    """@响应 Graph 状态。"""

    event_raw: dict
    group_id: str
    sender_open_id: str
    message_id: str
    message_text: str
    intent: str
    operation_type: str
    target_todo: dict | None
    all_todos: list[dict]
    member_map: dict[str, dict]
    update_result: dict | None
    reply_text: str
    # 防止成员刷新死循环：最多重试 1 次
    member_refresh_attempted: bool
    # LLM 意图分类完整结果（必须在 TypedDict 中声明，否则 LangGraph 不创建 channel 会丢弃该字段）
    _intent_result: dict
    # 消息中 @提及 的非 bot 用户列表，格式：[{"name": "甘鑫", "open_id": "ou_xxx"}]
    mentioned_users: list
    # 机器人自身 open_id，用于 parse_event 中过滤 bot 自身的 @mention
    bot_open_id: str
    # 检测到的系统 "/" 指令（如 "/init"），None 表示普通消息
    system_command: str | None


class OnboardState(TypedDict):
    """入群自我介绍 Graph 状态。"""

    event_raw: dict
    group_id: str
    group_name: str
    is_first_time: bool
    bitable_exists: bool
    schema_repair_report: dict  # ensure_schema() 修复报告
    member_list: list[dict]
