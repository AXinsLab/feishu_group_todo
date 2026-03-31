"""消息分析 Prompt 及输出结构定义。

用于 SchedulerGraph：分析昨日群消息，提取新任务、
判断已有任务完成状态，并进行语义去重。
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

ANALYZER_SYSTEM = """\
你是一个群任务追踪智能体，负责分析群聊消息，提取任务和更新状态。

分析规则：

1. 任务完成判断：
   - 高置信度完成：消息中明确表示任务已完成（如"xxx已完成"、"xxx搞定了"）
   - 低置信度完成：消息中提及任务可能完成但不够明确

2. 新任务提取：
   - 从消息中识别新的待办任务
   - 语义去重：若新任务与现有任务语义相同或高度相似，\
不得重复新增，只更新状态

3. 负责人识别优先级：
   a. 消息中有飞书 @ 提及（取 mention_open_id）
   b. 群成员表模糊匹配（匹配英文名、名字的"名"部分、群昵称，不区分大小写）
   c. LLM 上下文推断
   d. 兜底：归属发言者，进展备注标注"负责人待确认"

4. 时间解析：
   - 识别消息中明确提及的截止日期
   - 无明确日期时 due_date 填 null

现有进行中任务列表（用于去重和完成状态更新）：
{active_todos}

群成员信息（open_id -> 姓名信息）：
{member_map}

请分析以下昨日群消息，输出 JSON 结果：
- high_confidence_done：确认完成的任务 record_id 列表
- low_confidence_done：疑似完成的任务 record_id 列表
- new_tasks：新提取的任务列表（已与现有任务语义去重）
"""

ANALYZER_HUMAN = """\
昨日群消息列表：
{messages}
"""

ANALYZER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", ANALYZER_SYSTEM),
        ("human", ANALYZER_HUMAN),
    ]
)


class NewTaskItem(BaseModel):
    """新提取的任务项。"""

    description: str = Field(description="任务描述")
    assignee_open_id: str | None = Field(
        default=None, description="负责人 open_id"
    )
    assignee_name: str | None = Field(default=None, description="负责人姓名")
    due_date: str | None = Field(
        default=None,
        description="截止日期，格式 YYYY-MM-DD，不明确时为 null",
    )
    source_message_id: str = Field(description="来源消息 ID（message_id）")
    source_summary: str = Field(
        description="原始消息片段（用于溯源，不超过100字）"
    )


class AnalysisResult(BaseModel):
    """LLM 消息分析结果。"""

    high_confidence_done: list[str] = Field(
        default_factory=list,
        description="高置信度已完成任务的 record_id 列表",
    )
    low_confidence_done: list[str] = Field(
        default_factory=list,
        description="低置信度（疑似完成）任务的 record_id 列表",
    )
    new_tasks: list[NewTaskItem] = Field(
        default_factory=list,
        description="新提取的任务列表（已语义去重）",
    )
