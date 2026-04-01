"""意图分类 Prompt 及输出结构定义。

用于 MessageGraph：将用户 @机器人 的消息分类为
七种操作类型，并提取关键参数。
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

【自然语言示例】
- "让甘鑫明天下午四点到会议室开会" → 新增, assignee: 甘鑫, task: 明天下午四点到会议室开会
- "让张三负责登录Bug" → 新增, assignee: 张三, task: 修复登录Bug
- "@甘鑫 明天下午四点开会" → 新增, assignee: 甘鑫, task: 明天下午四点开会
- "修复登录bug 负责人李四" → 新增, assignee: 李四, task: 修复登录bug
- "登录Bug张三修完了" → 标记完成, task: 登录Bug
- "登录bug已经完成了" → 标记完成, task: 登录bug
- "把登录Bug的负责人改成李四" → 修改, task: 登录Bug
- "删掉登录Bug这个任务" → 删除, task: 登录Bug
- "登录Bug现在什么状态" → 查询状态, task: 登录Bug
- "把登录Bug重新激活" → 恢复任务, task: 登录Bug
- "今天天气怎么样" → 无关
- "谢谢" → 无关

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
    @field_validator("operation_type", mode="before")
    @classmethod
    def fix_encoding(cls, v: object) -> object:
        """修复 Azure AI Foundry 返回中文时的 Latin-1/UTF-8 误解码。

        症状：模型输出 UTF-8 字节的中文，但被按 Latin-1 解码，
        导致 '修改' 变成 'ä¿®æ\x94¹'。
        通过 encode('latin-1').decode('utf-8') 还原正确字符串。
        """
        if isinstance(v, str):
            try:
                return v.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return v
        return v
