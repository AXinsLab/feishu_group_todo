"""飞书多维表格客户端（StorageInterface 实现）。

通过飞书 Bitable v1 API 实现三张表的增删改查，
内置 table_id 缓存，避免重复查询表结构。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from tools.storage_interface import StorageInterface

if TYPE_CHECKING:
    from config import Settings
    from tools.feishu_client import FeishuClient

logger = logging.getLogger(__name__)

# 多维表格三张表名称常量
TODO_TABLE = "Todo主表"
MEMBER_TABLE = "群成员表"
GROUP_TABLE = "群配置表"

# 飞书 Bitable 字段类型常量
FIELD_TYPE_TEXT = 1
FIELD_TYPE_NUMBER = 2
FIELD_TYPE_SINGLE_SELECT = 3
FIELD_TYPE_DATE = 5
FIELD_TYPE_DATETIME = 5


def _bitable_base_url(app_token: str) -> str:
    """构造多维表格 API 基础 URL。"""
    return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"


class BitableClient(StorageInterface):
    """飞书多维表格存储实现。

    实现 StorageInterface 中定义的所有抽象方法，
    通过飞书 Bitable v1 API 操作三张数据表。
    """

    def __init__(
        self,
        settings: Settings,
        feishu_client: FeishuClient,
    ) -> None:
        self._settings = settings
        self._feishu = feishu_client
        self._app_token: str = settings.bitable_app_token
        # table_name -> table_id 缓存
        self._table_id_cache: dict[str, str] = {}

    def _update_app_token(self, app_token: str) -> None:
        """更新 app_token（首次建表后调用）。"""
        self._app_token = app_token
        self._table_id_cache.clear()

    async def _get_headers(self) -> dict[str, str]:
        """构造带 token 的请求头。"""
        token = await self._feishu._token_mgr.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _get_table_id(self, table_name: str) -> str:
        """获取数据表 ID，优先从缓存读取。

        Args:
            table_name: 数据表名称。

        Returns:
            数据表 table_id。

        Raises:
            ValueError: 数据表不存在时抛出。
        """
        if table_name in self._table_id_cache:
            return self._table_id_cache[table_name]

        headers = await self._get_headers()
        url = f"{_bitable_base_url(self._app_token)}/tables"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        for table in data.get("data", {}).get("items", []):
            self._table_id_cache[table["name"]] = table["table_id"]

        if table_name not in self._table_id_cache:
            raise ValueError(f"Table '{table_name}' not found in bitable")
        return self._table_id_cache[table_name]

    async def _list_records(
        self,
        table_name: str,
        filter_formula: str | None = None,
    ) -> list[dict]:
        """查询数据表记录（支持翻页）。

        Args:
            table_name: 数据表名称。
            filter_formula: Bitable 筛选公式，None 返回全部。

        Returns:
            记录列表，每条包含 record_id 和 fields 字典。
        """
        table_id = await self._get_table_id(table_name)
        headers = await self._get_headers()
        base = _bitable_base_url(self._app_token)
        url = f"{base}/tables/{table_id}/records"

        records: list[dict] = []
        page_token: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict[str, Any] = {"page_size": 100}
                if filter_formula:
                    params["filter"] = filter_formula
                if page_token:
                    params["page_token"] = page_token

                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 0:
                    logger.error(
                        "list_records failed: %s",
                        data.get("msg"),
                    )
                    break

                items = data.get("data", {}).get("items", [])
                for item in items:
                    record = {
                        "record_id": item.get("record_id"),
                    }
                    record.update(item.get("fields", {}))
                    records.append(record)

                has_more = data.get("data", {}).get("has_more", False)
                if not has_more:
                    break
                page_token = data.get("data", {}).get("page_token")

        return records

    async def _create_record(self, table_name: str, fields: dict) -> str:
        """新增一条记录。

        Returns:
            新记录的 record_id。
        """
        table_id = await self._get_table_id(table_name)
        headers = await self._get_headers()
        base = _bitable_base_url(self._app_token)
        url = f"{base}/tables/{table_id}/records"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"fields": fields},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            # 记录详细信息便于诊断（日期字段类型转换失败等）
            logger.error(
                "create_record failed: code=%s msg=%s | fields=%s",
                data.get("code"),
                data.get("msg"),
                {k: v for k, v in fields.items()},
            )
            raise RuntimeError(f"create_record failed: {data.get('msg')}")
        return data["data"]["record"]["record_id"]

    async def _update_record(
        self,
        table_name: str,
        record_id: str,
        fields: dict,
    ) -> bool:
        """更新一条记录。"""
        table_id = await self._get_table_id(table_name)
        headers = await self._get_headers()
        base = _bitable_base_url(self._app_token)
        url = f"{base}/tables/{table_id}/records/{record_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                url,
                headers=headers,
                json={"fields": fields},
            )
            resp.raise_for_status()
            data = resp.json()

        return data.get("code") == 0

    async def _delete_record(self, table_name: str, record_id: str) -> bool:
        """删除一条记录。"""
        table_id = await self._get_table_id(table_name)
        headers = await self._get_headers()
        base = _bitable_base_url(self._app_token)
        url = f"{base}/tables/{table_id}/records/{record_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return data.get("code") == 0

    # ── StorageInterface 实现 ─────────────────────────────

    async def get_todos(
        self,
        group_id: str,
        status: str | None = None,
    ) -> list[dict]:
        """查询 Todo 列表，支持按群ID和状态筛选。"""
        if status:
            formula = (
                f'AND(CurrentValue.[群ID]="{group_id}",'
                f'CurrentValue.[状态]="{status}")'
            )
        else:
            formula = f'CurrentValue.[群ID]="{group_id}"'

        return await self._list_records(TODO_TABLE, formula)

    async def create_todo(self, todo_data: dict) -> str:
        """新增 Todo 记录，返回 record_id。"""
        return await self._create_record(TODO_TABLE, todo_data)

    async def update_todo(self, todo_id: str, fields: dict) -> bool:
        """更新 Todo 记录指定字段。"""
        return await self._update_record(TODO_TABLE, todo_id, fields)

    async def delete_todo(self, todo_id: str) -> bool:
        """删除 Todo 记录。"""
        return await self._delete_record(TODO_TABLE, todo_id)

    async def get_members(self, group_id: str) -> list[dict]:
        """查询群成员列表。"""
        formula = f'CurrentValue.[群ID]="{group_id}"'
        return await self._list_records(MEMBER_TABLE, formula)

    async def upsert_members(
        self,
        group_id: str,
        members: list[dict],
    ) -> bool:
        """批量写入群成员（存在则更新，不存在则插入）。"""
        existing = await self.get_members(group_id)
        existing_by_open_id = {
            r.get("open_id", ""): r.get("record_id") for r in existing
        }

        for member in members:
            open_id = member.get("open_id", "")
            fields = {
                "群ID": group_id,
                "open_id": open_id,
                "真实姓名": member.get("name", ""),
                "英文名": member.get("en_name", ""),
                "群昵称": member.get("nickname", ""),
            }
            # 尝试拆分中文姓名（首字为姓）
            name = member.get("name", "")
            if name and len(name) >= 2:
                fields["姓"] = name[0]
                fields["名"] = name[1:]

            if open_id in existing_by_open_id:
                record_id = existing_by_open_id[open_id]
                if record_id:
                    await self._update_record(MEMBER_TABLE, record_id, fields)
            else:
                await self._create_record(MEMBER_TABLE, fields)

        return True

    async def get_group(self, group_id: str | None) -> dict | list | None:
        """查询群配置，group_id 为 None 时返回所有群配置。"""
        if group_id is None:
            return await self._list_records(GROUP_TABLE)

        formula = f'CurrentValue.[群ID]="{group_id}"'
        results = await self._list_records(GROUP_TABLE, formula)
        return results[0] if results else None

    async def upsert_group(self, group_data: dict) -> bool:
        """写入或更新群配置记录。"""
        group_id = group_data.get("群ID", "")
        existing = await self.get_group(group_id)

        if existing and isinstance(existing, dict):
            record_id = existing.get("record_id")
            if record_id:
                return await self._update_record(
                    GROUP_TABLE, record_id, group_data
                )

        await self._create_record(GROUP_TABLE, group_data)
        return True

    async def check_bitable_exists(self) -> bool:
        """检查多维表格是否可访问。"""
        if not self._app_token:
            return False

        try:
            headers = await self._get_headers()
            url = f"{_bitable_base_url(self._app_token)}/tables"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (404, 403):
                    return False
                resp.raise_for_status()
                data = resp.json()
                return data.get("code") == 0
        except Exception:
            logger.debug(
                "check_bitable_exists failed",
                exc_info=True,
            )
            return False

    async def initialize_tables(self, app_token: str) -> None:
        """初始化三张数据表（首次入群时调用）。

        Args:
            app_token: 已创建的多维表格 App Token。
        """
        self._update_app_token(app_token)

        # Todo 主表字段定义
        todo_fields = [
            {"field_name": "任务描述", "type": FIELD_TYPE_TEXT},
            {"field_name": "负责人姓名", "type": FIELD_TYPE_TEXT},
            {"field_name": "负责人open_id", "type": FIELD_TYPE_TEXT},
            {"field_name": "预期完成时间", "type": FIELD_TYPE_DATE},
            {
                "field_name": "状态",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {
                    "options": [
                        {"name": "进行中"},
                        {"name": "已完成"},
                    ]
                },
            },
            {"field_name": "完成日期", "type": FIELD_TYPE_DATE},
            {
                "field_name": "完成来源",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {
                    "options": [
                        {"name": "LLM判断"},
                        {"name": "成员确认"},
                    ]
                },
            },
            {
                "field_name": "来源类型",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {
                    "options": [
                        {"name": "定时提取"},
                        {"name": "成员手动添加"},
                    ]
                },
            },
            {"field_name": "来源消息ID", "type": FIELD_TYPE_TEXT},
            {"field_name": "进展备注", "type": FIELD_TYPE_TEXT},
            {"field_name": "来源摘要", "type": FIELD_TYPE_TEXT},
            {"field_name": "创建日期", "type": FIELD_TYPE_DATE},
            {"field_name": "最后更新", "type": FIELD_TYPE_DATETIME},
            {"field_name": "群ID", "type": FIELD_TYPE_TEXT},
        ]

        # 群成员表字段定义
        member_fields = [
            {"field_name": "群ID", "type": FIELD_TYPE_TEXT},
            {"field_name": "open_id", "type": FIELD_TYPE_TEXT},
            {"field_name": "真实姓名", "type": FIELD_TYPE_TEXT},
            {"field_name": "姓", "type": FIELD_TYPE_TEXT},
            {"field_name": "名", "type": FIELD_TYPE_TEXT},
            {"field_name": "英文名", "type": FIELD_TYPE_TEXT},
            {"field_name": "群昵称", "type": FIELD_TYPE_TEXT},
        ]

        # 群配置表字段定义
        group_fields = [
            {"field_name": "群ID", "type": FIELD_TYPE_TEXT},
            {"field_name": "群名称", "type": FIELD_TYPE_TEXT},
            {"field_name": "机器人加入时间", "type": FIELD_TYPE_DATETIME},
            {"field_name": "最后同步时间", "type": FIELD_TYPE_DATETIME},
            {"field_name": "多维表格ID", "type": FIELD_TYPE_TEXT},
            {"field_name": "错误日志", "type": FIELD_TYPE_TEXT},
        ]

        for name, fields in [
            (TODO_TABLE, todo_fields),
            (MEMBER_TABLE, member_fields),
            (GROUP_TABLE, group_fields),
        ]:
            table_id = await self._feishu.create_bitable_table(
                app_token, name, fields
            )
            self._table_id_cache[name] = table_id
            logger.info("Created table '%s': %s", name, table_id)
    # ── Schema 自动修复 ───────────────────────────────────

    # 三张必需数据表的完整字段规格（field_name → 字段定义）
    _REQUIRED_SCHEMA: dict[str, list[dict]] = {
        TODO_TABLE: [
            {"field_name": "任务描述",    "type": FIELD_TYPE_TEXT},
            {"field_name": "负责人姓名",  "type": FIELD_TYPE_TEXT},
            {"field_name": "负责人open_id","type": FIELD_TYPE_TEXT},
            {"field_name": "预期完成时间","type": FIELD_TYPE_DATE},
            {
                "field_name": "状态",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {"options": [{"name": "进行中"}, {"name": "已完成"}]},
            },
            {"field_name": "完成日期",   "type": FIELD_TYPE_DATE},
            {
                "field_name": "完成来源",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {"options": [{"name": "LLM判断"}, {"name": "成员确认"}]},
            },
            {
                "field_name": "来源类型",
                "type": FIELD_TYPE_SINGLE_SELECT,
                "property": {"options": [{"name": "定时提取"}, {"name": "成员手动添加"}]},
            },
            {"field_name": "来源消息ID", "type": FIELD_TYPE_TEXT},
            {"field_name": "进展备注",   "type": FIELD_TYPE_TEXT},
            {"field_name": "来源摘要",   "type": FIELD_TYPE_TEXT},
            {"field_name": "创建日期",   "type": FIELD_TYPE_DATE},
            {"field_name": "最后更新",   "type": FIELD_TYPE_DATETIME},
            {"field_name": "群ID",       "type": FIELD_TYPE_TEXT},
        ],
        MEMBER_TABLE: [
            {"field_name": "群ID",    "type": FIELD_TYPE_TEXT},
            {"field_name": "open_id", "type": FIELD_TYPE_TEXT},
            {"field_name": "真实姓名","type": FIELD_TYPE_TEXT},
            {"field_name": "姓",      "type": FIELD_TYPE_TEXT},
            {"field_name": "名",      "type": FIELD_TYPE_TEXT},
            {"field_name": "英文名",  "type": FIELD_TYPE_TEXT},
            {"field_name": "群昵称",  "type": FIELD_TYPE_TEXT},
        ],
        GROUP_TABLE: [
            {"field_name": "群ID",        "type": FIELD_TYPE_TEXT},
            {"field_name": "群名称",       "type": FIELD_TYPE_TEXT},
            {"field_name": "机器人加入时间","type": FIELD_TYPE_DATETIME},
            {"field_name": "最后同步时间", "type": FIELD_TYPE_DATETIME},
            {"field_name": "多维表格ID",   "type": FIELD_TYPE_TEXT},
            {"field_name": "错误日志",     "type": FIELD_TYPE_TEXT},
        ],
    }

    async def ensure_schema(self) -> dict[str, list[str]]:
        """检查并自动修复多维表格的子表和字段结构。

        对每张必需子表：
        - 若子表不存在 → 创建子表（含所有字段）
        - 若子表存在但字段缺失 → 逐一补充缺失字段

        Returns:
            修复报告字典，key 为表名，value 为已修复动作列表（空列表表示无需修复）。
        """
        if not self._app_token:
            logger.warning("ensure_schema: no app_token configured, skip")
            return {}

        report: dict[str, list[str]] = {}

        # 1. 获取当前所有子表
        headers = await self._get_headers()
        url = f"{_bitable_base_url(self._app_token)}/tables"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        existing_tables: dict[str, str] = {}  # name → table_id
        for t in data.get("data", {}).get("items", []):
            existing_tables[t["name"]] = t["table_id"]
        # 同步缓存
        self._table_id_cache.update(existing_tables)

        for table_name, required_fields in self._REQUIRED_SCHEMA.items():
            actions: list[str] = []

            if table_name not in existing_tables:
                # ── 整张子表缺失：直接创建 ───────────────────
                logger.info("ensure_schema: creating missing table '%s'", table_name)
                try:
                    table_id = await self._feishu.create_bitable_table(
                        self._app_token, table_name, required_fields
                    )
                    self._table_id_cache[table_name] = table_id
                    actions.append(f"created table '{table_name}'")
                except Exception as exc:
                    logger.error(
                        "ensure_schema: failed to create table '%s': %s",
                        table_name, exc,
                    )
                    actions.append(f"ERROR creating table '{table_name}': {exc}")
            else:
                # ── 子表已存在：检查并补充缺失字段 ──────────
                table_id = existing_tables[table_name]
                try:
                    existing_fields_raw = await self._feishu.list_bitable_fields(
                        self._app_token, table_id
                    )
                    existing_names = {
                        f.get("field_name", "") for f in existing_fields_raw
                    }
                    for fdef in required_fields:
                        fname = fdef["field_name"]
                        if fname not in existing_names:
                            logger.info(
                                "ensure_schema: adding missing field '%s' to '%s'",
                                fname, table_name,
                            )
                            try:
                                await self._feishu.add_bitable_field(
                                    self._app_token, table_id, fdef
                                )
                                actions.append(f"added field '{fname}'")
                            except Exception as exc:
                                logger.error(
                                    "ensure_schema: failed to add field '%s' to '%s': %s",
                                    fname, table_name, exc,
                                )
                                actions.append(f"ERROR adding field '{fname}': {exc}")
                except Exception as exc:
                    logger.error(
                        "ensure_schema: failed to list fields for '%s': %s",
                        table_name, exc,
                    )
                    actions.append(f"ERROR listing fields: {exc}")

            report[table_name] = actions
            if actions:
                logger.info("ensure_schema [%s]: %s", table_name, actions)
            else:
                logger.debug("ensure_schema [%s]: OK (no changes needed)", table_name)

        return report

