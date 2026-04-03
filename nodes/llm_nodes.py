"""LLM 分析相关的 LangGraph 节点函数。"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# 中文数字到阿拉伯数字的映射（支持"第三点"、"第四个"等）
_CHINESE_NUM: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _find_todo_by_number(desc: str, active_todos: list[dict]) -> dict | None:
    """检测描述是否为'第N点/项/个/条'格式，返回对应的激活任务。

    支持阿拉伯数字（"第3点"）和中文数字（"第三点"、"第四个"）。
    """
    if not desc or not active_todos:
        return None
    m = re.search(r"第([0-9一二三四五六七八九十]+)[点项个条]?", desc)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _CHINESE_NUM.get(raw, 0)
    if 1 <= n <= len(active_todos):
        return active_todos[n - 1]
    return None


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
    member_map: dict = state.get("member_map", {})

    # 构建群成员姓名列表，供 LLM 识别自然语言中的负责人
    member_list_str = "、".join(
        m.get("name", "") for m in member_map.values() if m.get("name")
    ) or "（暂无成员信息）"

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

    # 构建带编号的激活任务列表（供 LLM 解析"第N点"引用）
    active_todos = [t for t in all_todos if t.get("状态") == "进行中"]
    numbered_active = "\n".join(
        f"{i + 1}. {t.get('任务描述', '')}"
        for i, t in enumerate(active_todos)
    ) or "（当前无进行中任务）"

    try:
        result: IntentResult = await chain.ainvoke(
            {
                "message_text": message_text,
                "all_todos": json.dumps(todos_summary, ensure_ascii=False, indent=2),
                "member_list": member_list_str,
                "numbered_active_todos": numbered_active,
            }
        )

        # ── 构建 pending_operations 列表 ──────────────────────
        ops_raw = result.operations or []

        # LLM 未填 operations 时，从顶层字段构造单元素列表（后向兼容）
        if not ops_raw:
            from prompts.intent import OperationItem
            ops_raw = [
                OperationItem(
                    operation_type=result.operation_type,
                    task_description=result.task_description,
                    assignee_name=result.assignee_name,
                    new_content=result.new_content,
                )
            ]

        pending_operations: list[dict] = []
        for op in ops_raw:
            target = None
            if op.task_description:
                # 优先：编号解析（第N点/项）
                target = _find_todo_by_number(op.task_description, active_todos)
                # 其次：子串语义匹配
                if not target:
                    target = _find_target_todo(op.task_description, all_todos)
            pending_operations.append({
                "operation_type": op.operation_type,
                "target_todo": target,
                "note": op.note,
                "assignee_name": op.assignee_name,
                "new_content": op.new_content,
                "_intent_desc": op.task_description or "",
            })

        # 路由用：取第一个非无关操作类型；全无关则"无关"
        primary_op = next(
            (p["operation_type"] for p in pending_operations if p["operation_type"] != "无关"),
            "无关",
        )

        logger.info(
            "classify_intent: primary=%s, ops=%d %s",
            primary_op,
            len(pending_operations),
            [(p["operation_type"], p.get("_intent_desc", "")[:20]) for p in pending_operations],
        )

        return {
            "intent": primary_op,
            "operation_type": primary_op,
            "target_todo": pending_operations[0].get("target_todo") if pending_operations else None,
            "pending_operations": pending_operations,
            "_intent_result": result.model_dump(),
        }
    except Exception as exc:
        logger.error("classify_intent LLM call failed: %s", exc)
        return {
            "intent": "无关",
            "operation_type": "无关",
            "target_todo": None,
            "pending_operations": [],
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
