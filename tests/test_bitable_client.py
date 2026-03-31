"""bitable_client 模块单元测试。

使用 mock_storage fixture 验证 StorageInterface 契约，
使用 httpx mock 验证 BitableClient 实现细节。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.bitable_client import BitableClient


@pytest.fixture
def bitable_settings() -> MagicMock:
    """创建 Settings 的 MagicMock。"""
    settings = MagicMock()
    settings.bitable_app_token = "bitable_test_token"
    settings.rate_limit_interval_ms = 0
    settings.token_refresh_buffer_seconds = 300
    settings.feishu_app_id = "test_app_id"
    settings.feishu_app_secret.get_secret_value.return_value = "test_secret"
    return settings


@pytest.fixture
def bitable_client(
    bitable_settings: MagicMock,
    mock_feishu_client: AsyncMock,
) -> BitableClient:
    """创建 BitableClient 实例（注入 mock）。"""
    client = BitableClient(bitable_settings, mock_feishu_client)
    # 预置 table_id 缓存，避免真实 API 调用
    client._table_id_cache = {
        "Todo主表": "tbl_todo",
        "群成员表": "tbl_member",
        "群配置表": "tbl_group",
    }
    return client


class TestGetTodos:
    """get_todos 方法测试。"""

    async def test_get_todos_with_status_filter(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """按状态筛选时应构造正确的 filter 公式。"""
        with patch.object(
            bitable_client,
            "_list_records",
            new_callable=AsyncMock,
            return_value=[
                {
                    "record_id": "rec_001",
                    "任务描述": "测试任务",
                    "状态": "进行中",
                    "群ID": "oc_001",
                }
            ],
        ) as mock_list:
            result = await bitable_client.get_todos("oc_001", status="进行中")

        assert len(result) == 1
        # 验证 filter 包含状态条件
        call_args = mock_list.call_args
        assert "进行中" in call_args[0][1]

    async def test_get_todos_without_status(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """不传 status 时应返回全部记录。"""
        with patch.object(
            bitable_client,
            "_list_records",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_list:
            await bitable_client.get_todos("oc_001")

        call_args = mock_list.call_args
        filter_formula = call_args[0][1]
        # filter 只包含群ID，不包含状态
        assert "状态" not in filter_formula


class TestCreateTodo:
    """create_todo 方法测试。"""

    async def test_create_todo_returns_record_id(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """新增成功应返回 record_id。"""
        with patch.object(
            bitable_client,
            "_create_record",
            new_callable=AsyncMock,
            return_value="rec_new_001",
        ):
            record_id = await bitable_client.create_todo(
                {"任务描述": "新任务", "群ID": "oc_001"}
            )

        assert record_id == "rec_new_001"


class TestUpdateTodo:
    """update_todo 方法测试。"""

    async def test_update_todo_success(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """更新成功应返回 True。"""
        with patch.object(
            bitable_client,
            "_update_record",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await bitable_client.update_todo(
                "rec_001", {"状态": "已完成"}
            )

        assert result is True


class TestDeleteTodo:
    """delete_todo 方法测试。"""

    async def test_delete_todo_success(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """删除成功应返回 True。"""
        with patch.object(
            bitable_client,
            "_delete_record",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await bitable_client.delete_todo("rec_001")

        assert result is True


class TestUpsertMembers:
    """upsert_members 方法测试。"""

    async def test_upsert_new_member_creates_record(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """不存在的成员应调用 create_record。"""
        with (
            patch.object(
                bitable_client,
                "get_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                bitable_client,
                "_create_record",
                new_callable=AsyncMock,
                return_value="rec_new",
            ) as mock_create,
        ):
            await bitable_client.upsert_members(
                "oc_001",
                [
                    {
                        "open_id": "ou_new",
                        "name": "新成员",
                        "en_name": "",
                        "nickname": "",
                    }
                ],
            )

        mock_create.assert_called_once()

    async def test_upsert_existing_member_updates_record(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """已存在的成员应调用 update_record。"""
        with (
            patch.object(
                bitable_client,
                "get_members",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "record_id": "rec_001",
                        "open_id": "ou_existing",
                    }
                ],
            ),
            patch.object(
                bitable_client,
                "_update_record",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update,
        ):
            await bitable_client.upsert_members(
                "oc_001",
                [
                    {
                        "open_id": "ou_existing",
                        "name": "老成员",
                        "en_name": "",
                        "nickname": "",
                    }
                ],
            )

        mock_update.assert_called_once()


class TestCheckBitableExists:
    """check_bitable_exists 方法测试。"""

    async def test_returns_false_when_no_token(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """app_token 为空时应返回 False。"""
        bitable_client._app_token = ""
        result = await bitable_client.check_bitable_exists()
        assert result is False

    async def test_returns_false_on_404(
        self,
        bitable_client: BitableClient,
    ) -> None:
        """API 返回 404 时应返回 False。"""

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await bitable_client.check_bitable_exists()

        assert result is False
