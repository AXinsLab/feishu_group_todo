"""抽象存储接口。

所有 Graph 节点通过此接口访问数据，不直接依赖飞书多维表格 API。
当前实现：BitableClient（飞书多维表格）。
预留实现：DatabaseStorage（MySQL / PostgreSQL）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageInterface(ABC):
    """存储层抽象基类。

    所有方法均为异步，实现类需完整覆盖所有抽象方法。
    """

    @abstractmethod
    async def get_todos(
        self,
        group_id: str,
        status: str | None = None,
    ) -> list[dict]:
        """查询 Todo 列表。

        Args:
            group_id: 群 ID，用于多群数据隔离。
            status: 状态筛选，None 表示返回全部状态。

        Returns:
            Todo 记录列表，每条记录包含 record_id 及所有字段。
        """
        ...

    @abstractmethod
    async def create_todo(self, todo_data: dict) -> str:
        """新增 Todo 记录。

        Args:
            todo_data: 字段字典，键为中文字段名。

        Returns:
            新创建记录的 record_id。
        """
        ...

    @abstractmethod
    async def update_todo(self, todo_id: str, fields: dict) -> bool:
        """更新 Todo 记录。

        Args:
            todo_id: 目标记录的 record_id。
            fields: 待更新的字段字典，键为中文字段名。

        Returns:
            更新成功返回 True。
        """
        ...

    @abstractmethod
    async def delete_todo(self, todo_id: str) -> bool:
        """删除 Todo 记录。

        Args:
            todo_id: 目标记录的 record_id。

        Returns:
            删除成功返回 True。
        """
        ...

    @abstractmethod
    async def get_members(self, group_id: str) -> list[dict]:
        """查询群成员列表。

        Args:
            group_id: 群 ID。

        Returns:
            成员记录列表，每条包含 open_id、姓名等字段。
        """
        ...

    @abstractmethod
    async def upsert_members(self, group_id: str, members: list[dict]) -> bool:
        """批量写入群成员（存在则更新，不存在则插入）。

        Args:
            group_id: 群 ID。
            members: 成员数据列表，每条需包含 open_id。

        Returns:
            操作成功返回 True。
        """
        ...

    @abstractmethod
    async def get_group(self, group_id: str | None) -> dict | list | None:
        """查询群配置。

        Args:
            group_id: 群 ID；传 None 时返回所有群配置列表。

        Returns:
            单群配置 dict、全部配置 list，或不存在时返回 None。
        """
        ...

    @abstractmethod
    async def upsert_group(self, group_data: dict) -> bool:
        """写入或更新群配置。

        Args:
            group_data: 群配置字典，键为中文字段名，必须包含群ID。

        Returns:
            操作成功返回 True。
        """
        ...

    @abstractmethod
    async def check_bitable_exists(self) -> bool:
        """检查多维表格是否已创建。

        Returns:
            多维表格可访问返回 True，否则返回 False。
        """
        ...
