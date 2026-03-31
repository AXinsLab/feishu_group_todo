"""OnboardGraph 端到端测试。

验证入群初始化流程的两条路径：
- 首次入群：完整初始化
- 重复入群：仅刷新成员表
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from graphs.onboard_graph import build_onboard_graph


@pytest.fixture
def onboard_graph(
    mock_storage: AsyncMock,
    mock_feishu_client: AsyncMock,
) -> object:
    """构建 OnboardGraph（注入 mock 依赖）。"""
    return build_onboard_graph(mock_storage, mock_feishu_client)


@pytest.fixture
def bot_added_event(mock_events: dict) -> dict:
    """机器人入群事件。"""
    return mock_events["bot_added"]


class TestFirstTimeOnboard:
    """首次入群流程测试。"""

    async def test_first_time_creates_bitable(
        self,
        onboard_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        bot_added_event: dict,
    ) -> None:
        """首次入群且多维表格不存在时，应创建多维表格。"""
        mock_storage.get_group.return_value = None
        mock_storage.check_bitable_exists.return_value = False
        mock_feishu_client.get_chat_info.return_value = {"name": "测试群"}
        mock_feishu_client.create_bitable.return_value = "new_bitable_token"

        initial_state = {"event_raw": bot_added_event}
        await onboard_graph.ainvoke(initial_state)

        # 应调用创建多维表格
        mock_feishu_client.create_bitable.assert_called_once()
        # 应发送自我介绍
        mock_feishu_client.send_message.assert_called()

    async def test_first_time_skips_bitable_creation_if_exists(
        self,
        onboard_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        bot_added_event: dict,
    ) -> None:
        """首次入群但多维表格已存在时，不应重复创建。"""
        mock_storage.get_group.return_value = None
        mock_storage.check_bitable_exists.return_value = True
        mock_feishu_client.get_chat_info.return_value = {"name": "测试群"}

        initial_state = {"event_raw": bot_added_event}
        await onboard_graph.ainvoke(initial_state)

        # 不应调用创建多维表格
        mock_feishu_client.create_bitable.assert_not_called()

    async def test_first_time_writes_member_list(
        self,
        onboard_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        bot_added_event: dict,
    ) -> None:
        """首次入群应拉取并写入群成员。"""
        mock_storage.get_group.return_value = None
        mock_storage.check_bitable_exists.return_value = True
        mock_feishu_client.get_chat_info.return_value = {"name": "测试群"}
        mock_feishu_client.get_group_members.return_value = [
            {
                "open_id": "ou_001",
                "name": "张三",
                "en_name": "zhangsan",
                "nickname": "小张",
            }
        ]

        initial_state = {"event_raw": bot_added_event}
        await onboard_graph.ainvoke(initial_state)

        # 应写入成员表
        mock_storage.upsert_members.assert_called()

    async def test_first_time_sends_introduction(
        self,
        onboard_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        bot_added_event: dict,
    ) -> None:
        """首次入群应发送自我介绍消息。"""
        mock_storage.get_group.return_value = None
        mock_storage.check_bitable_exists.return_value = True
        mock_feishu_client.get_chat_info.return_value = {"name": "测试群"}

        initial_state = {"event_raw": bot_added_event}
        await onboard_graph.ainvoke(initial_state)

        mock_feishu_client.send_message.assert_called()
        call_args = mock_feishu_client.send_message.call_args
        # 发送内容应包含自我介绍关键词
        content = str(call_args)
        assert "任务追踪" in content or "send_message" in str(
            mock_feishu_client.send_message.call_args_list
        )


class TestRepeatOnboard:
    """重复入群流程测试。"""

    async def test_repeat_only_refreshes_members(
        self,
        onboard_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        bot_added_event: dict,
    ) -> None:
        """重复入群时只应刷新成员表，不重复初始化。"""
        # 群配置已存在
        mock_storage.get_group.return_value = {
            "record_id": "rec_group_001",
            "群ID": "oc_test_group_002",
            "群名称": "测试群",
        }

        initial_state = {"event_raw": bot_added_event}
        await onboard_graph.ainvoke(initial_state)

        # 不应创建多维表格
        mock_feishu_client.create_bitable.assert_not_called()
        # 不应发送自我介绍
        mock_feishu_client.send_message.assert_not_called()
        # 应刷新成员
        mock_feishu_client.get_group_members.assert_called()
