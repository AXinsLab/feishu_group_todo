"""MessageGraph 端到端测试。

验证意图分类、操作执行和回复生成的完整流程。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphs.message_graph import build_message_graph
from prompts.intent import IntentResult
from schemas.models import OperationType


@pytest.fixture
def message_graph(
    mock_storage: AsyncMock,
    mock_feishu_client: AsyncMock,
) -> object:
    """构建 MessageGraph（注入 mock 依赖）。"""
    return build_message_graph(mock_storage, mock_feishu_client)


@pytest.fixture
def message_event(mock_events: dict) -> dict:
    """@机器人 消息事件。"""
    return mock_events["message_receive"]


def _make_intent_result(
    op_type: OperationType,
    task_description: str | None = None,
    assignee_name: str | None = None,
    new_content: str | None = None,
    due_date: str | None = None,
) -> IntentResult:
    """构造 IntentResult 测试对象。"""
    return IntentResult(
        operation_type=op_type,
        task_description=task_description,
        assignee_name=assignee_name,
        new_content=new_content,
        due_date=due_date,
    )


class TestIntentClassification:
    """意图分类测试（mock LLM 输出）。"""

    @pytest.mark.parametrize(
        "user_message,expected_op",
        [
            (
                "新增任务 修复登录bug 负责人 张三",
                OperationType.CREATE,
            ),
            (
                "登录bug已完成",
                OperationType.MARK_DONE,
            ),
            (
                "修改登录bug任务 改为负责人李四",
                OperationType.UPDATE,
            ),
            (
                "删除登录bug任务",
                OperationType.DELETE,
            ),
            (
                "查询登录bug的状态",
                OperationType.QUERY,
            ),
            (
                "恢复任务 登录bug",
                OperationType.RESTORE,
            ),
            (
                "今天天气怎么样",
                OperationType.UNRELATED,
            ),
        ],
    )
    async def test_intent_classification(
        self,
        user_message: str,
        expected_op: OperationType,
        message_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        message_event: dict,
        mock_llm: MagicMock,
    ) -> None:
        """验证各操作类型的意图分类结果。"""
        # mock LLM 返回对应的意图结果
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=_make_intent_result(
                expected_op,
                task_description="登录bug",
            )
        )

        mock_storage.get_todos.return_value = []
        mock_storage.get_members.return_value = []

        # 修改事件中的消息文本
        event = dict(message_event)
        event["event"]["message"]["content"] = f'{{"text":"{user_message}"}}'

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            result = await message_graph.ainvoke({"event_raw": event})

        assert result["operation_type"] == expected_op


class TestUnrelatedMessage:
    """无关内容处理测试。"""

    async def test_unrelated_sends_rejection(
        self,
        message_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        message_event: dict,
        mock_llm: MagicMock,
    ) -> None:
        """无关内容应发送拒绝回复，不写入任何数据。"""
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=_make_intent_result(OperationType.UNRELATED)
        )
        mock_storage.get_todos.return_value = []
        mock_storage.get_members.return_value = []

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            result = await message_graph.ainvoke({"event_raw": message_event})

        # 不应有写入操作
        mock_storage.create_todo.assert_not_called()
        mock_storage.update_todo.assert_not_called()
        mock_storage.delete_todo.assert_not_called()
        # 应发送拒绝回复
        mock_feishu_client.send_message.assert_called()
        reply = result.get("reply_text", "")
        assert "只能处理" in reply or "任务追踪" in reply


class TestMarkDoneOperation:
    """标记完成操作测试。"""

    async def test_mark_done_sets_correct_fields(
        self,
        message_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        message_event: dict,
        mock_llm: MagicMock,
        active_todos_sample: list[dict],
    ) -> None:
        """标记完成应设置状态、完成日期、完成来源。"""
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=_make_intent_result(
                OperationType.MARK_DONE,
                task_description="修复登录bug",
            )
        )
        mock_storage.get_todos.return_value = active_todos_sample
        mock_storage.get_members.return_value = []

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await message_graph.ainvoke({"event_raw": message_event})

        # 应调用 update_todo
        mock_storage.update_todo.assert_called()
        call_args = mock_storage.update_todo.call_args
        fields = call_args[0][1]
        assert fields.get("状态") == "已完成"
        assert fields.get("完成来源") == "成员确认"
        assert "完成日期" in fields


class TestCreateOperation:
    """新增任务操作测试。"""

    async def test_create_sets_source_type(
        self,
        message_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        message_event: dict,
        mock_llm: MagicMock,
    ) -> None:
        """手动新增任务应设置来源类型=成员手动添加。"""
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=_make_intent_result(
                OperationType.CREATE,
                task_description="新的测试任务",
                assignee_name="张三",
                due_date="2024-04-01",
            )
        )
        mock_storage.get_todos.return_value = []
        mock_storage.get_members.return_value = [
            {
                "open_id": "ou_001",
                "name": "张三",
                "en_name": "zhangsan",
                "nickname": "",
            }
        ]

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await message_graph.ainvoke({"event_raw": message_event})

        mock_storage.create_todo.assert_called()
        fields = mock_storage.create_todo.call_args[0][0]
        assert fields.get("来源类型") == "成员手动添加"
        assert "来源消息ID" in fields


class TestRestoreOperation:
    """恢复任务操作测试。"""

    async def test_restore_clears_completion_date(
        self,
        message_graph: object,
        mock_storage: AsyncMock,
        mock_feishu_client: AsyncMock,
        message_event: dict,
        mock_llm: MagicMock,
    ) -> None:
        """恢复任务应将状态改为进行中，并清空完成日期。"""
        completed_todo = {
            "record_id": "rec_003",
            "任务描述": "已完成的任务",
            "状态": "已完成",
            "完成日期": "2024-03-27",
            "群ID": "oc_test_group_001",
        }
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=_make_intent_result(
                OperationType.RESTORE,
                task_description="已完成的任务",
            )
        )
        mock_storage.get_todos.return_value = [completed_todo]
        mock_storage.get_members.return_value = []

        with patch(
            "tools.llm_client.get_llm",
            return_value=mock_llm,
        ):
            await message_graph.ainvoke({"event_raw": message_event})

        mock_storage.update_todo.assert_called()
        fields = mock_storage.update_todo.call_args[0][1]
        assert fields.get("状态") == "进行中"
