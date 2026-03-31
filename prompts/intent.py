"""意图分类 Prompt 及输出结构定义。

用于 MessageGraph：将用户 @机器人 的消息分类为
七种操作类型，并提取关键参数。
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from schemas.models import OperationType

INTENT_SYSTEM = """\
你是一个群任务追踪机器人的意图分析助手。
根据用户消息，判断操作类型并提取关键信息。

支持的操作类型：
- 新增：用户想要添加新任务
- 修改：用户想要修改任务内容
- 删除：用户想要删除任务
- 标记完成：用户确认某任务已完成
- 查询状态：用户想查询某任务的当前状态
- 恢复任务：用户想把已完成任务改回进行中
- 无关：与任务管理无关的内容

操作示例：
- "新增任务 修复登录bug 负责人 张三" → 新增
- "登录bug已完成" → 标记完成
- "修改登录bug任务 改为负责人李四" → 修改
- "删除登录bug任务" → 删除
- "查询登录bug的状态" → 查询状态
- "恢复任务 登录bug" → 恢复任务
- "今天天气怎么样" → 无关

当前任务列表（供任务匹配参考）：
{all_todos}

请根据消息内容，以 JSON 格式输出分析结果。
"""

INTENT_HUMAN = "{message_text}"

INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", INTENT_SYSTEM),
        ("human", INTENT_HUMAN),
    ]
)


class IntentResult(BaseModel):
    """意图分类结果。"""

    operation_type: OperationType = Field(
        description=(
            "操作类型：新增/修改/删除/标记完成/查询状态/恢复任务/无关"
        )
    )
    task_description: str | None = Field(
        default=None,
        description="目标任务描述（用于匹配现有任务）",
    )
    assignee_name: str | None = Field(
        default=None,
        description="新增或修改时指定的负责人姓名",
    )
    new_content: str | None = Field(
        default=None,
        description="修改操作时的新内容",
    )
    due_date: str | None = Field(
        default=None,
        description="截止日期，格式 YYYY-MM-DD",
    )
