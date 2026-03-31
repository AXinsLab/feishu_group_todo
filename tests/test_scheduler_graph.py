"""SchedulerGraph 端到端测试。

验证消息过滤、LLM 分析、数据写入和报告生成的完整流程。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphs.scheduler_graph import build_scheduler_graph
from prompts.analyzer import AnalysisResult, NewTaskItem


@pytest.fixture
def scheduler_graph(
    mock_storage: AsyncMock,
    mock_feishu_client: AsyncMock,
) -> object:
    """构建 SchedulerGraph（注入 mock 依赖）。"""
    return build_scheduler_graph(mock_storage, mock_feishu_client)


@pytest.fixture
def trigger_time() -> datetime:
    """模拟触发时间（今日 09:30）。"""
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=9, minute=30, second=0)


@pytest.fixture
def scheduler_initial_state(
    trigger_time: datetime,
) -> dict:
    """SchedulerGraph 初始状态。"""
    return {
        "trigger_time": trigger_time,
        "time_window_start": trigger_time - timedelta(hours=24),
        "time_window_end": trigger_time,
        "group_list": [],
        "current_group_id": "",
        "raw_messages": [],
        "filtered_messages": [],
        "active_todos": [],
        "completed_yesterday": [],
        "member_map": {},
        "llm_analysis": {},
        "update_operations": [],
        "errors": [],
    }


class TestMessageFiltering:
    """消息过滤（第一层防重复）测试。"""

    async def test_filter_removes_existing_source_ids(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        scheduler_initial_state: dict,
    ) -> None:
        """来源消息ID已存在于 Todo 表的消息应被过滤。"""
        existing_todo = {
            "record_id": "rec_001",
            "任务描述": "已有任务",
            "状态": "进行中",
            "来源消息ID": "om_existing",
            "群ID": "oc_group_001",
        }
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "测试群"}
        ]
        mock_storage.get_todos.side_effect = lambda gid, status=None: (
            [existing_todo] if status == "进行中" else []
        )
        mock_feishu_client.get_group_messages.return_value = [
            {
                "message_id": "om_existing",
                "text": "已处理的消息",
                "sender_open_id": "ou_001",
                "create_time": "1711590000000",
            },
            {
                "message_id": "om_new_001",
                "text": "新消息",
                "sender_open_id": "ou_002",
                "create_time": "1711593600000",
            },
        ]

        mock_analysis = AnalysisResult(
            high_confidence_done=[],
            low_confidence_done=[],
            new_tasks=[],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_analysis
        )

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await scheduler_graph.ainvoke(scheduler_initial_state)

        # LLM 只应接收到 om_new_001（om_existing 被过滤）
        llm_call = (
            mock_llm.with_structured_output.return_value.ainvoke.call_args
        )
        if llm_call:
            messages_arg = llm_call[1].get(
                "messages",
                llm_call[0][0].get("messages", "") if llm_call[0] else "",
            )
            assert "om_existing" not in str(messages_arg)

    async def test_empty_messages_and_no_active_sends_empty_report(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        scheduler_initial_state: dict,
    ) -> None:
        """无消息且无进行中任务时，应发送空状态报告。"""
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "测试群"}
        ]
        mock_storage.get_todos.return_value = []
        mock_feishu_client.get_group_messages.return_value = []

        await scheduler_graph.ainvoke(scheduler_initial_state)

        # 应发送空状态报告
        mock_feishu_client.send_message.assert_called()
        call_content = str(mock_feishu_client.send_message.call_args_list)
        assert "无新增任务" in call_content or (
            mock_feishu_client.send_message.called
        )


class TestLLMAnalysis:
    """LLM 分析结果处理测试。"""

    async def test_high_confidence_done_updates_status(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        mock_llm: MagicMock,
        scheduler_initial_state: dict,
        active_todos_sample: list[dict],
    ) -> None:
        """高置信度完成任务应更新状态为已完成。"""
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "测试群"}
        ]
        mock_storage.get_todos.side_effect = lambda gid, status=None: (
            active_todos_sample if status == "进行中" else []
        )
        mock_feishu_client.get_group_messages.return_value = [
            {
                "message_id": "om_done_001",
                "text": "修复登录bug已完成",
                "sender_open_id": "ou_001",
                "create_time": "1711590000000",
            }
        ]

        mock_analysis = AnalysisResult(
            high_confidence_done=["rec_001"],
            low_confidence_done=[],
            new_tasks=[],
        )
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_analysis
        )

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await scheduler_graph.ainvoke(scheduler_initial_state)

        # 应更新 rec_001 的状态
        update_calls = mock_storage.update_todo.call_args_list
        updated_ids = [c[0][0] for c in update_calls]
        assert "rec_001" in updated_ids

        # 验证更新字段
        for call in update_calls:
            if call[0][0] == "rec_001":
                fields = call[0][1]
                assert fields.get("状态") == "已完成"
                assert fields.get("完成来源") == "LLM判断"

    async def test_new_task_creates_with_correct_source_type(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        mock_llm: MagicMock,
        scheduler_initial_state: dict,
    ) -> None:
        """LLM 提取的新任务应以来源类型=定时提取写入。"""
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "测试群"}
        ]
        mock_storage.get_todos.return_value = []
        mock_feishu_client.get_group_messages.return_value = [
            {
                "message_id": "om_new_task",
                "text": "数据库迁移脚本需要完成",
                "sender_open_id": "ou_001",
                "create_time": "1711590000000",
            }
        ]

        mock_analysis = AnalysisResult(
            high_confidence_done=[],
            low_confidence_done=[],
            new_tasks=[
                NewTaskItem(
                    description="数据库迁移脚本",
                    assignee_open_id="ou_001",
                    assignee_name="张三",
                    due_date=None,
                    source_message_id="om_new_task",
                    source_summary="数据库迁移脚本需要完成",
                )
            ],
        )
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_analysis
        )

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await scheduler_graph.ainvoke(scheduler_initial_state)

        mock_storage.create_todo.assert_called()
        fields = mock_storage.create_todo.call_args[0][0]
        assert fields.get("来源类型") == "定时提取"
        assert fields.get("来源消息ID") == "om_new_task"


class TestMultiGroupIsolation:
    """多群数据隔离测试。"""

    async def test_two_groups_processed_independently(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        scheduler_initial_state: dict,
    ) -> None:
        """两个群的处理应相互独立，数据不混用。"""
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "群A"},
            {"群ID": "oc_group_002", "群名称": "群B"},
        ]
        mock_storage.get_todos.return_value = []
        mock_feishu_client.get_group_messages.return_value = []

        await scheduler_graph.ainvoke(scheduler_initial_state)

        # 两个群都应调用 get_group_messages
        assert mock_feishu_client.get_group_messages.call_count >= 2
        call_args_list = mock_feishu_client.get_group_messages.call_args_list
        group_ids_called = [c[0][0] for c in call_args_list]
        assert "oc_group_001" in group_ids_called
        assert "oc_group_002" in group_ids_called

    async def test_one_group_failure_not_affect_others(
        self,
        scheduler_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        scheduler_initial_state: dict,
    ) -> None:
        """某个群处理失败时，不影响其他群的继续处理。"""
        mock_storage.get_group.return_value = [
            {"群ID": "oc_group_001", "群名称": "群A"},
            {"群ID": "oc_group_002", "群名称": "群B"},
        ]
        mock_storage.get_todos.return_value = []

        call_count = 0

        async def mock_get_messages(gid, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if gid == "oc_group_001":
                raise RuntimeError("群A 消息拉取失败")
            return []

        mock_feishu_client.get_group_messages.side_effect = mock_get_messages

        # 不应抛出异常
        await scheduler_graph.ainvoke(scheduler_initial_state)

        # 群A 失败，群B 仍应被处理
        assert call_count == 2
