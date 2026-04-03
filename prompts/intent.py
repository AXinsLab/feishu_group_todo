"""意图分类 Prompt 及输出结构定义。

用于 MessageGraph：将用户 @机器人 的消息分类为
七种操作类型，并提取关键参数。支持单条消息含多个操作。
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, field_validator

from schemas.models import OperationType

INTENT_SYSTEM = """你是一个群任务追踪机器人的意图分析助手。
根据用户发来的自然语言消息，判断操作类型并提取关键信息。

支持的操作类型：
- 新增：用户想要添加新任务
- 修改：用户想要修改任务内容
- 删除：用户想要删除任务
- 标记完成：用户确认某任务已完成
- 查询状态：用户想查询某任务的当前状态
- 恢复任务：用户想把已完成任务改回进行中
- 无关：与任务管理无关的内容（纯闲聊、问候等）

当前群成员（供负责人识别参考）：
{member_list}

【负责人识别规则】
以下几种表达方式均可指定负责人，请灵活识别：
1. @提及：消息中出现 @姓名（如 @甘鑫）
2. "让...做/负责"句式：如"让甘鑫负责这个"、"让张三去开会"
3. "...负责人..."句式：如"负责人张三"、"指定李四负责"
4. 动作主语：如"甘鑫明天下午四点开会"（甘鑫是执行人）

请从上述规则中提取 assignee_name，与群成员列表对照后填入。

【任务描述提取规则】
提取用户意图的核心任务内容，去掉指令词（"新增任务"、"让谁谁"等前缀）：
- "让甘鑫明天下午四点到会议室开会" → task: "明天下午四点到会议室开会"
- "新增任务 修复登录bug 负责人张三" → task: "修复登录bug"
- "@甘鑫 明天下午四点开会" → task: "明天下午四点开会"

【当前激活任务（带编号，供任务匹配参考）】
{numbered_active_todos}

【多操作规则】
一条消息可能包含对多个任务的操作，请将所有操作均填入 operations 列表：
- 哪怕只有一个操作，也要填入 operations 列表
- 用户说"第N点/项/个"时，对照上方编号列表找到对应任务，将实际任务描述填入 task_description（不要填"第N点"，要填实际描述）
- 用户隐式引用（如"刚才那个MIC评估"、"那个手板的任务"）时，从编号列表中语义匹配最相关的任务，填写实际描述
- 用户说明结果/进展（如"不能共用，会新增模具费"）时，该内容填入 note 字段（系统将写入任务的进展备注）
- 若确实无法识别目标任务，task_description 置 null（系统会友好提示用户）
- operation_type 填第一个操作的类型（用于路由），operations 填所有操作

【多操作示例】
消息："第3点不能共用会新增模具费，第四点有个实心手板"
→ operation_type: "标记完成"
→ operations: [
    {{"operation_type": "标记完成", "task_description": "评估C桥新设计是否可共用...", "note": "不能共用，会新增模具费"}},
    {{"operation_type": "标记完成", "task_description": "提供当前产品外观手板供查看", "note": "有个实心的外观手板"}}
  ]

消息："任务1完成了，把任务2的负责人改成张三，新增一个评估D桥的任务"
→ operation_type: "标记完成"
→ operations: [
    {{"operation_type": "标记完成", "task_description": "<任务1的实际描述>"}},
    {{"operation_type": "修改", "task_description": "<任务2的实际描述>", "assignee_name": "张三"}},
    {{"operation_type": "新增", "task_description": "评估D桥"}}
  ]

【单操作示例】
- "让甘鑫明天下午四点到会议室开会" → 新增, assignee: 甘鑫, task: 明天下午四点到会议室开会
- "让张三负责登录Bug" → 新增, assignee: 张三, task: 修复登录Bug
- "登录Bug张三修完了" → 标记完成, task: 登录Bug
- "登录bug已经完成了" → 标记完成, task: 登录bug
- "把登录Bug的负责人改成李四" → 修改, task: 登录Bug
- "删掉登录Bug这个任务" → 删除, task: 登录Bug
- "登录Bug现在什么状态" → 查询状态, task: 登录Bug
- "把登录Bug重新激活" → 恢复任务, task: 登录Bug
- "今天天气怎么样" → 无关

当前任务列表（供任务匹配参考，包含所有状态）：
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


class OperationItem(BaseModel):
    """单个操作项，用于多操作消息中的每一个操作。"""

    operation_type: OperationType = Field(
        description="操作类型：新增/修改/删除/标记完成/查询状态/恢复任务/无关"
    )
    task_description: str | None = Field(
        default=None,
        description="目标任务的实际描述（从编号列表中解析得到，不填编号本身）",
    )
    note: str | None = Field(
        default=None,
        description="附加说明或进展结果，将写入任务的进展备注字段",
    )
    assignee_name: str | None = Field(
        default=None,
        description="负责人姓名（新增/修改时使用）",
    )
    new_content: str | None = Field(
        default=None,
        description="修改操作时的新任务描述内容",
    )

    @field_validator("operation_type", mode="before")
    @classmethod
    def fix_encoding(cls, v: object) -> object:
        """修复 Azure AI Foundry 返回中文时的 Latin-1/UTF-8 误解码。"""
        if isinstance(v, str):
            try:
                return v.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return v
        return v


class IntentResult(BaseModel):
    """意图分类结果。"""

    operation_type: OperationType = Field(
        description=(
            "主操作类型（多操作时取第一个非无关操作）："
            "新增/修改/删除/标记完成/查询状态/恢复任务/无关"
        )
    )
    task_description: str | None = Field(
        default=None,
        description="目标任务描述（去掉指令前缀后的核心任务内容）",
    )
    assignee_name: str | None = Field(
        default=None,
        description="负责人姓名（从@提及、让...做、负责人...等表达中提取）",
    )
    new_content: str | None = Field(
        default=None,
        description="修改操作时的新内容",
    )
    operations: list[OperationItem] = Field(
        default_factory=list,
        description="所有操作列表，单操作时也填入（下游统一按此列表处理）",
    )

    @field_validator("operation_type", mode="before")
    @classmethod
    def fix_encoding(cls, v: object) -> object:
        """修复 Azure AI Foundry 返回中文时的 Latin-1/UTF-8 误解码。"""
        if isinstance(v, str):
            try:
                return v.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return v
        return v
