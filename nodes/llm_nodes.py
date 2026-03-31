"""LLM 分析相关的 LangGraph 节点函数。"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def analyze_messages(state: dict) -> dict:
    """调用 LLM 分析昨日消息，提取任务和状态更新。

    使用结构化输出（with_structured_output）保证 JSON 格式。

    Args:
        state: 包含 filtered_messages、active_todos、
               member_map、current_group_id 的 SchedulerState。

    Returns:
        包含 llm_analysis 的部分状态更新。
    """
    from prompts.analyzer import ANALYZER_PROMPT, AnalysisResult
    from tools.llm_client import get_llm

    llm = get_llm()
    chain = ANALYZER_PROMPT | llm.with_structured_output(AnalysisResult)

    filtered_messages: list[dict] = state.get("filtered_messages", [])
    active_todos: list[dict] = state.get("active_todos", [])
    member_map: dict = state.get("member_map", {})

    try:
        result: AnalysisResult = await chain.ainvoke(
            {
                "messages": json.dumps(
                    filtered_messages,
                    ensure_ascii=False,
                    indent=2,
                ),
                "active_todos": json.dumps(
                    active_todos,
                    ensure_ascii=False,
                    indent=2,
                ),
                "member_map": json.dumps(
                    member_map,
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )
        analysis = result.model_dump()
        logger.info(
            "analyze_messages: %d done, %d new tasks",
            len(result.high_confidence_done),
            len(result.new_tasks),
        )
        return {"llm_analysis": analysis}
    except Exception as exc:
        logger.error("analyze_messages LLM call failed: %s", exc)
        return {
            "llm_analysis": {
                "high_confidence_done": [],
                "low_confidence_done": [],
                "new_tasks": [],
            },
            "errors": [
                {
                    "group_id": state.get("current_group_id", ""),
                    "type": "system",
                    "message": f"LLM 分析失败：{exc}",
                }
            ],
        }


async def classify_intent(state: dict) -> dict:
    """调用 LLM 对 @机器人 消息进行意图分类。

    Args:
        state: 包含 message_text、all_todos 的 MessageState。

    Returns:
        包含 operation_type、target_todo、
        _intent_result 的部分状态更新。
    """
    from prompts.intent import INTENT_PROMPT, IntentResult
    from tools.llm_client import get_llm

    llm = get_llm()
    chain = INTENT_PROMPT | llm.with_structured_output(IntentResult)

    message_text: str = state.get("message_text", "")
    all_todos: list[dict] = state.get("all_todos", [])

    # 仅传递描述和状态，减少 token 消耗
    todos_summary = [
        {
            "record_id": t.get("record_id"),
            "任务描述": t.get("任务描述"),
            "状态": t.get("状态"),
            "负责人姓名": t.get("负责人姓名"),
        }
        for t in all_todos
    ]

    try:
        result: IntentResult = await chain.ainvoke(
            {
                "message_text": message_text,
                "all_todos": json.dumps(
                    todos_summary,
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )

        target_todo = _find_target_todo(result.task_description, all_todos)

        logger.info(
            "classify_intent: %s, task='%s'",
            result.operation_type,
            result.task_description,
        )
        return {
            "intent": result.operation_type,
            "operation_type": result.operation_type,
            "target_todo": target_todo,
            # 存储完整 intent 结果供后续节点使用
            "_intent_result": result.model_dump(),
        }
    except Exception as exc:
        logger.error("classify_intent LLM call failed: %s", exc)
        return {
            "intent": "无关",
            "operation_type": "无关",
            "target_todo": None,
            "_intent_result": {},
        }


def _find_target_todo(
    task_description: str | None,
    all_todos: list[dict],
) -> dict | None:
    """根据任务描述在 Todo 列表中找到最匹配的任务。

    使用简单的字符串包含匹配，优先返回描述最短的匹配项
    （越精确越好）。

    Args:
        task_description: 用户描述的任务文本。
        all_todos: 所有 Todo 记录列表。

    Returns:
        最匹配的 Todo 记录，未找到返回 None。
    """
    if not task_description:
        return None

    desc_lower = task_description.lower()
    matches = [
        t
        for t in all_todos
        if desc_lower in t.get("任务描述", "").lower()
        or t.get("任务描述", "").lower() in desc_lower
    ]

    if not matches:
        return None

    # 返回描述最短的匹配项（最精确）
    return min(
        matches,
        key=lambda t: len(t.get("任务描述", "")),
    )
