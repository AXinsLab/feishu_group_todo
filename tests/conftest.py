"""pytest 共享 Fixture 定义。

提供 mock_storage、mock_feishu_client、mock_llm
等测试辅助对象，所有 Graph 测试模块复用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Fixture 数据路径 ──────────────────────────────────────
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_messages() -> list[dict]:
    """加载模拟飞书消息数据。"""
    with open(FIXTURES_DIR / "mock_messages.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_todos() -> list[dict]:
    """加载模拟 Todo 数据。"""
    with open(FIXTURES_DIR / "mock_todos.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_events() -> dict:
    """加载模拟飞书事件数据。"""
    with open(FIXTURES_DIR / "mock_events.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_storage() -> AsyncMock:
    """创建 StorageInterface 的 AsyncMock。

    预配置常用方法的默认返回值，测试中可按需覆盖。
    """
    from tools.storage_interface import StorageInterface

    storage = AsyncMock(spec=StorageInterface)
    storage.get_todos.return_value = []
    storage.get_members.return_value = []
    storage.get_group.return_value = None
    storage.create_todo.return_value = "rec_test_001"
    storage.update_todo.return_value = True
    storage.delete_todo.return_value = True
    storage.upsert_members.return_value = True
    storage.upsert_group.return_value = True
    storage.check_bitable_exists.return_value = False
    return storage


@pytest.fixture
def mock_feishu_client() -> AsyncMock:
    """创建 FeishuClient 的 AsyncMock。

    预配置常用方法的默认返回值，测试中可按需覆盖。
    """
    from tools.feishu_client import FeishuClient

    client = AsyncMock(spec=FeishuClient)
    client.send_message.return_value = True
    client.get_group_messages.return_value = []
    client.get_group_members.return_value = []
    client.create_bitable.return_value = "bitable_test_token"
    client.create_bitable_table.return_value = "tbl_test_id"
    client.get_chat_info.return_value = {
        "name": "测试群",
        "description": "",
    }
    return client


@pytest.fixture
def mock_llm() -> MagicMock:
    """创建 AzureChatOpenAI 的 MagicMock。

    with_structured_output() 返回一个真正的 LangChain
    RunnableLambda，其 ainvoke 可在测试中被替换。
    这样 PROMPT | llm.with_structured_output() 能正常工作。
    """
    from langchain_core.runnables import RunnableLambda

    async def _noop(input_dict: dict, **kwargs: Any) -> None:
        return None

    mock_chain = RunnableLambda(_noop)
    # 预置 ainvoke 为 AsyncMock，测试中可覆盖
    mock_chain.ainvoke = AsyncMock(return_value=None)

    llm = MagicMock()
    llm.with_structured_output.return_value = mock_chain
    return llm


@pytest.fixture
def sample_member_map() -> dict[str, dict]:
    """样本群成员 map（open_id -> 成员信息）。"""
    return {
        "ou_test_user_1": {
            "open_id": "ou_test_user_1",
            "name": "张三",
            "en_name": "zhangsan",
            "nickname": "小张",
        },
        "ou_test_user_2": {
            "open_id": "ou_test_user_2",
            "name": "李四",
            "en_name": "lisi",
            "nickname": "小李",
        },
        "ou_test_user_3": {
            "open_id": "ou_test_user_3",
            "name": "王五",
            "en_name": "wangwu",
            "nickname": "小王",
        },
    }


@pytest.fixture
def active_todos_sample() -> list[dict]:
    """进行中的任务样本数据。"""
    return [
        {
            "record_id": "rec_001",
            "任务描述": "修复登录bug",
            "负责人姓名": "张三",
            "负责人open_id": "ou_test_user_1",
            "预期完成时间": "2024-03-28",
            "状态": "进行中",
            "来源消息ID": "om_test_001",
            "群ID": "oc_test_group_001",
        },
        {
            "record_id": "rec_002",
            "任务描述": "首页性能优化",
            "负责人姓名": "李四",
            "负责人open_id": "ou_test_user_2",
            "预期完成时间": "2024-03-22",
            "状态": "进行中",
            "来源消息ID": "om_test_003",
            "群ID": "oc_test_group_001",
        },
    ]
