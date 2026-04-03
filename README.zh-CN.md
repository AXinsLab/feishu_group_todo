<div align="center">

# 🤖 飞书群聊 Todo 智能体

**基于 LangGraph 与 DeepSeek-V3 构建的飞书群聊智能任务追踪机器人。**

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple?style=flat-square)](https://docs.astral.sh/uv/)

[English](README.md) · [中文](README.zh-CN.md)

</div>

---

## ✨ 功能特性

- 🗣️ **自然语言交互** — @机器人，用自然语言新增、修改、删除、查询、恢复任务
- 📝 **一条消息多任务** — 单条消息可操作多个任务，各操作独立执行，部分失败不影响其余
- 🔢 **编号与语义引用** — 支持按编号引用（"第3点"、"第四个"）或语义描述引用（"那个MIC评估"），机器人自动解析
- 📌 **进展备注** — 标记完成时附带的说明或结果，自动写入多维表格的进展备注字段
- ⏰ **定时分析** — 每日 09:30 自动分析昨日群消息，提取新任务，更新已完成状态
- 📊 **每日报告** — 发送结构化消息卡片，含带编号的进行中任务、已完成项（带删除线）及逾期告警
- 👥 **多群隔离** — 同一机器人管理多个群，所有数据按群完全隔离
- 🔁 **双重去重** — 消息 ID 硬过滤 + LLM 语义比对，杜绝重复创建任务
- 💾 **多维表格存储** — 任务、成员、群配置持久化至飞书多维表格，无需额外数据库
- 🔄 **表结构自动修复** — 机器人入群或执行 `/init` 时，自动创建缺失的子表与字段
- 🤖 **Slash 指令系统** — 可扩展的指令注册表，新增指令只需注册一条记录，无需改动工作流
- 🔐 **AES-CBC 加密** — 内置飞书 Webhook 加密消息解密支持
- ♻️ **断点续跑** — SQLite Checkpointer，定时任务崩溃后从断点自动恢复

---

## 💬 机器人使用

### 自然语言任务管理（@机器人）

| 示例消息 | 操作 |
|---|---|
| `让张三明天下午四点到会议室开会` | ➕ 新增任务，负责人：张三 |
| `登录Bug李四修完了` | ✅ 标记任务完成 |
| `把登录Bug的负责人改成王五` | ✏️ 修改任务 |
| `删掉登录Bug这个任务` | 🗑️ 删除任务 |
| `登录Bug现在什么状态` | 🔍 查询任务状态 |
| `把登录Bug重新激活` | 🔄 恢复为进行中 |
| `第3点不能共用，会新增模具费` | ✅ 标记第3项完成 + 写入进展备注 |
| `第四点有个实心手板，第2个完成了` | ✅ 一条消息同时完成第4项和第2项 |

> 未指定负责人时，**消息发送者**自动设为负责人。
>
> 可按**编号**（第3点、第四个）或**自然语言描述**引用任务，机器人对照当前激活任务列表自动解析。

### Slash 指令（@机器人）

| 指令 | 说明 |
|---|---|
| `/help` | 显示所有可用指令 |
| `/init` | 同步群成员 + 自动修复多维表格结构 |
| `/tasks` | 显示当前任务列表（带编号） |
| `/my` | 只显示分配给你的任务 |
| `/update` | 分析过去 24 小时消息，更新任务表，发送报告 |
| `/delete 2` | 删除第 2 项任务 |
| `/delete 1 3 5` | 批量删除第 1、3、5 项任务 |

---

## 🏗️ 系统架构

三个独立的 LangGraph 工作流覆盖所有场景：

```
飞书事件
    │
    ├─── 🤖 机器人入群  ──►  OnboardGraph（入群初始化）
    │                         ├─ 自动创建 / 修复多维表格
    │                         ├─ 同步群成员
    │                         └─ 发送自我介绍消息卡片
    │
    ├─── 💬 @机器人消息  ──►  MessageGraph（消息处理）
    │                         ├─ [/指令] → Slash 指令处理器（无 LLM）
    │                         └─ [自然语言] → LLM 意图分类
    │                               ├─ 构建 pending_operations 列表
    │                               ├─ 各操作独立执行
    │                               └─ 逐操作回复结果
    │
    └─── ⏱️ 定时触发    ──►  SchedulerGraph（定时总结）
                              ├─ 刷新群成员
                              ├─ 拉取并去重消息（过滤机器人自身消息）
                              ├─ LLM 分析（提取任务 + 检测完成状态）
                              ├─ 服务端解析负责人 open_id
                              ├─ 写入多维表格
                              └─ 发送每日报告消息卡片
```

### MessageGraph 流程详解

```
parse_event
    │
    ├─ /指令 ──► execute_command ──► send_reply
    │
    └─ 自然语言
          │
          ├─ fetch_all_todos
          ├─ fetch_members          （LLM 前预加载，供负责人姓名解析使用）
          ├─ classify_intent（LLM）  （构建 pending_operations 列表）
          │     ├─ 无关 ──► build_reject_reply ──► send_reply
          │     └─ 任务操作 ──► resolve_operation
          │                       └─ execute_operation（循环执行各操作）
          │                             └─ build_confirm_reply ──► send_reply
```

### Slash 指令扩展

指令注册于 `nodes/command_nodes.py` 的 `COMMAND_REGISTRY`。新增指令只需：

```python
COMMAND_REGISTRY["/mycommand"] = {
    "description": "指令说明",
    "handler": my_handler_fn,   # async (state, feishu, storage) -> str
}
```

无需改动工作流图，所有处理器共享统一的异常捕获包装。

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | [FastAPI](https://fastapi.tiangolo.com/) |
| Agent 编排 | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| LLM | AzureChatOpenAI（DeepSeek-V3，Azure AI Foundry） |
| 飞书 SDK | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) |
| 存储 | 飞书多维表格（Todo 主表、群成员表、群配置表） |
| 断点续跑 | langgraph-checkpoint-sqlite |
| 运行时 | Python 3.13 + [uv](https://docs.astral.sh/uv/) |
| Webhook 安全 | AES-CBC 解密 + HMAC 密钥校验 |

---

## 📁 项目结构

```
feishu_group_todo/
├── main.py                      # FastAPI 入口，Webhook 路由，AES 解密
├── config.py                    # 配置管理（pydantic-settings + lru_cache）
│
├── schemas/
│   ├── models.py                # OperationType 枚举，Pydantic 模型
│   └── state.py                 # LangGraph TypedDict 状态（三个 Graph）
│
├── graphs/
│   ├── onboard_graph.py         # 入群初始化工作流
│   ├── message_graph.py         # @消息处理工作流
│   └── scheduler_graph.py       # 定时总结工作流
│
├── nodes/
│   ├── command_nodes.py         # Slash 指令注册表与所有处理器
│   ├── feishu_nodes.py          # 飞书 API 交互节点
│   ├── bitable_nodes.py         # 多维表格 CRUD + 多操作执行循环
│   ├── llm_nodes.py             # LLM 推理 + 意图 → pending_operations
│   └── report_nodes.py          # 报告卡片构建 + 回复文本生成
│
├── prompts/
│   ├── intent.py                # 意图分类 Prompt + OperationItem 结构
│   ├── analyzer.py              # 消息分析 Prompt + 结构化输出
│   └── report.py                # 回复文本模板常量
│
├── tools/
│   ├── feishu_client.py         # 飞书客户端（Token 管理、限速、重试）
│   ├── bitable_client.py        # StorageInterface 实现 + ensure_schema()
│   ├── storage_interface.py     # 存储抽象接口
│   └── llm_client.py            # AzureChatOpenAI 单例
│
├── tests/                       # pytest 测试套件
│   ├── conftest.py              # 共享 Fixture（mock_storage、mock_feishu、mock_llm）
│   ├── fixtures/                # JSON 测试数据
│   └── test_*.py
│
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 🚀 快速开始

### 前置要求

- Python 3.13+，[uv](https://docs.astral.sh/uv/) 包管理器
- 飞书开放平台企业自建应用（已开通消息、成员、多维表格权限）
- Azure AI Foundry 上部署的 DeepSeek-V3（或兼容模型）

### 安装

```bash
git clone https://github.com/AXinsLab/feishu_group_todo.git
cd feishu_group_todo
uv sync
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入真实凭证
```

```env
# Azure AI Foundry
AZURE_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_DEPLOYMENT=deepseek-v3
AZURE_API_VERSION=2024-05-01-preview
AZURE_API_KEY=your-api-key

# 飞书应用
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_ENCRYPT_KEY=xxxxxxxx          # 若开启 Webhook 加密
FEISHU_VERIFICATION_TOKEN=xxxxxxxx

# 多维表格（也可在运行时写入 data/bitable_token.txt）
BITABLE_APP_TOKEN=xxxxxxxx

# 运维
OPS_CHAT_ID=oc_xxxxxxxx             # 接收系统错误告警的群 ID
WEBHOOK_SECRET=your-secret           # 用于鉴权 /webhook/scheduler 请求
```

### 启动

```bash
# 开发模式
uv run uvicorn main:app --reload --port 8000

# 生产环境（Docker）
docker compose up -d --build
```

### 多维表格 Token

Bitable App Token 可通过 `.env` 设置，也可直接写入 `data/bitable_token.txt`（优先级更高）。机器人启动时自动创建缺失的子表与字段，无需手动建表。

---

## ⚙️ 飞书应用配置

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. **事件订阅** → 请求 URL 填写 `https://your-domain/webhook/feishu`
3. 订阅以下事件：
   - `im.message.receive_v1` — 接收消息
   - `im.chat.member.bot.added_v1` — 机器人入群
4. 开通权限：消息读写、获取群成员、多维表格读写

---

## ⏱️ 定时任务配置

在服务器上配置 cron，每日 09:30 触发定时总结：

```bash
# 创建触发脚本
cat > /usr/local/bin/feishu_scheduler_trigger.sh << 'EOF'
#!/bin/bash
curl -s -X POST http://localhost:8000/webhook/scheduler \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret" \
  -d "{\"trigger_time\":\"$(date -u +%Y-%m-%dT%H:%M:%S)\"}"
EOF
chmod +x /usr/local/bin/feishu_scheduler_trigger.sh

# 加入 crontab
echo "30 9 * * * /usr/local/bin/feishu_scheduler_trigger.sh >> /var/log/feishu_scheduler.log 2>&1" | crontab -
```

---

## 🧪 测试

```bash
# 运行全部测试
uv run pytest tests/ -v

# 运行单个测试文件
uv run pytest tests/test_message_graph.py -v

# 代码检查与格式化
uv run ruff check .
uv run ruff format .
```

---

## 📄 许可证

MIT © [AXinsLab](https://github.com/AXinsLab)
