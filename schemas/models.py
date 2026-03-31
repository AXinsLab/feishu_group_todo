"""数据模型定义。

包含飞书多维表格三张表的 Pydantic v2 模型，
以及所有枚举类型。字段 alias 与多维表格中文字段名一致。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TodoStatus(StrEnum):
    """Todo 任务状态。"""

    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"


class CompletionSource(StrEnum):
    """任务完成来源。"""

    LLM = "LLM判断"
    MEMBER = "成员确认"


class SourceType(StrEnum):
    """任务来源类型。"""

    SCHEDULED = "定时提取"
    MANUAL = "成员手动添加"


class OperationType(StrEnum):
    """@机器人 消息的操作类型。"""

    CREATE = "新增"
    UPDATE = "修改"
    DELETE = "删除"
    MARK_DONE = "标记完成"
    QUERY = "查询状态"
    RESTORE = "恢复任务"
    UNRELATED = "无关"


class TodoModel(BaseModel):
    """Todo 主表数据模型。

    record_id 对应多维表格行 ID，None 表示尚未写入。
    """

    model_config = ConfigDict(populate_by_name=True)

    record_id: str | None = None
    task_description: str = Field(alias="任务描述")
    assignee_name: str | None = Field(default=None, alias="负责人姓名")
    assignee_open_id: str | None = Field(default=None, alias="负责人open_id")
    due_date: date | None = Field(default=None, alias="预期完成时间")
    status: TodoStatus = Field(default=TodoStatus.IN_PROGRESS, alias="状态")
    completion_date: date | None = Field(default=None, alias="完成日期")
    completion_source: CompletionSource | None = Field(
        default=None, alias="完成来源"
    )
    source_type: SourceType | None = Field(default=None, alias="来源类型")
    source_message_id: str | None = Field(default=None, alias="来源消息ID")
    progress_notes: str | None = Field(default=None, alias="进展备注")
    source_summary: str | None = Field(default=None, alias="来源摘要")
    created_date: date | None = Field(default=None, alias="创建日期")
    last_updated: datetime | None = Field(default=None, alias="最后更新")
    group_id: str = Field(alias="群ID")


class MemberModel(BaseModel):
    """群成员表数据模型。"""

    model_config = ConfigDict(populate_by_name=True)

    record_id: str | None = None
    group_id: str = Field(alias="群ID")
    open_id: str
    full_name: str = Field(alias="真实姓名")
    last_name: str | None = Field(default=None, alias="姓")
    first_name: str | None = Field(default=None, alias="名")
    english_name: str | None = Field(default=None, alias="英文名")
    group_nickname: str | None = Field(default=None, alias="群昵称")


class GroupConfigModel(BaseModel):
    """群配置表数据模型。"""

    model_config = ConfigDict(populate_by_name=True)

    record_id: str | None = None
    group_id: str = Field(alias="群ID")
    group_name: str = Field(alias="群名称")
    bot_join_time: datetime | None = Field(
        default=None, alias="机器人加入时间"
    )
    last_sync_time: datetime | None = Field(default=None, alias="最后同步时间")
    bitable_id: str | None = Field(default=None, alias="多维表格ID")
    error_log: str | None = Field(default=None, alias="错误日志")
