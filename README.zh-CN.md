<div align="center">

# 🤖 飞书群聊 Todo 智能体

**基于 LangGraph 与 DeepSeek-V3 构建的飞书群聊智能任务追踪机器人。**

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-42%20passed-brightgreen?style=flat-square&logo=pytest)](tests/)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple?style=flat-square)](https://docs.astral.sh/uv/)

[English](README.md) · [中文](#-功能特性)

</div>

---

## ✨ 功能特性

- 🗣️ **自然语言交互** — @机器人，用自然语言新增、修改、删除、查询、恢复任务
- ⏰ **定时分析** — 每日 09:30 自动分析昨日群消息，提取新任务，更新已完成状态
- 📊 **每日报告** — 发送结构化日报，含完成情况、进行中任务及逾期告警
- 👥 **多群支持** — 同一机器人管理多个群，数据完全隔离
- 🔁 **双重去重** — 消息 ID 硬过滤 + LLM 语义比对，避免重复创建任务
- 💾 **多维表格存储** — 任务、成员、群配置持久化至飞书多维表格
- 🔄 **断点续跑** — 基于 SQLite Checkpointer，定时任务崩溃后可从断点恢复

---

## 🏗️ 系统架构

三个独立的 LangGraph 工作流覆盖所有场景：

```
飞书事件
    │
    ├─── 🤖 机器人入群  ──►  OnboardGraph（入群初始化）
    │                         └─ 创建多维表格
    │                         └─ 同步群成员
    │                         └─ 发送自我介绍
    │
    ├─── 💬 @机器人消息  ──►  MessageGraph（消息处理）
    │                         └─ LLM 意图分类
    │                         └─ 执行 CRUD 操作
    │                         └─ 回复用户
    │
    └─── ⏱️ 定时触发    ──►  SchedulerGraph（定时总结）
                              └─ 拉取并过滤消息
                              └─ LLM 分析
                              └─ 更新任务状态
                              └─ 发送每日报告
```

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| 🌐 Web 框架 | [FastAPI](https://fastapi.tiangolo.com/) |
| 🧠 Agent 编排 | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| 🤖 LLM | AzureChatOpenAI（DeepSeek-V3，Azure AI Foundry） |
| 📡 飞书 SDK | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) |
| 🗄️ 存储 | 飞书多维表格（Todo主表、群成员表、群配置表） |
| 💾 断点续跑 | langgraph-checkpoint-sqlite |
| ⚡ 运行时 | Python 3.13 + [uv](https://docs.astral.sh/uv/) |

---

## 📁 项目结构

```
feishu_group_todo/
├── 📄 main.py                   # FastAPI 入口，Webhook 路由
├── ⚙️  config.py                 # 配置管理（pydantic-settings + lru_cache）
│
├── 📐 schemas/
│   ├── models.py                # Pydantic v2 数据模型与 StrEnum 枚举
│   └── state.py                 # LangGraph TypedDict 状态定义（三个 Graph）
│
├── 🔀 graphs/
│   ├── onboard_graph.py         # 入群初始化工作流
│   ├── message_graph.py         # @消息处理工作流
│   └── scheduler_graph.py       # 定时总结工作流
│
├── 🧩 nodes/
│   ├── feishu_nodes.py          # 飞书 API 交互节点
│   ├── bitable_nodes.py         # 多维表格 CRUD 节点
│   ├── llm_nodes.py             # LLM 推理节点
│   └── report_nodes.py          # 报告与回复生成节点
│
├── 💬 prompts/
│   ├── intent.py                # 意图分类 Prompt 与结构化输出
│   ├── analyzer.py              # 消息分析 Prompt 与结构化输出
│   └── report.py                # 回复文本模板常量
│
├── 🔧 tools/
│   ├── feishu_client.py         # 飞书客户端（TokenManager、限速、重试）
│   ├── bitable_client.py        # StorageInterface 多维表格实现
│   ├── storage_interface.py     # 存储抽象接口（支持后端切换）
│   └── llm_client.py            # AzureChatOpenAI 单例
│
├── 🧪 tests/                    # pytest 测试套件（42 个用例）
│   ├── conftest.py              # 共享 Fixture（mock_storage、mock_feishu、mock_llm）
│   ├── fixtures/                # JSON 测试数据
│   ├── test_feishu_client.py
│   ├── test_bitable_client.py
│   ├── test_onboard_graph.py
│   ├── test_message_graph.py
│   └── test_scheduler_graph.py
│
├── 🐳 Dockerfile
├── 🐳 docker-compose.yml
└── 📋 .env.example
```

---

## 🚀 快速开始

### 前置要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 包管理器
- 飞书开放平台企业自建应用（已开通消息、成员、多维表格权限）
- Azure AI Foundry 上部署的 DeepSeek-V3 模型

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

主要配置项（完整说明见 `.env.example`）：

```env
# Azure AI Foundry
AZURE_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_DEPLOYMENT=deepseek-v3
AZURE_API_VERSION=2024-05-01-preview
AZURE_API_KEY=your-api-key

# 飞书应用
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxx

# 多维表格
BITABLE_APP_TOKEN=xxxxxxxx

# 运维
OPS_CHAT_ID=oc_xxxxxxxx
WEBHOOK_SECRET=your-secret
```

### 启动

```bash
# 开发模式
uv run uvicorn main:app --reload --port 8000

# 生产环境（Docker）
docker compose up -d
```

### 运行测试

```bash
uv run pytest tests/ -v
```

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
30 9 * * * curl -X POST https://your-domain/webhook/scheduler \
  -H "X-Webhook-Secret: your-secret"
```

---

## 💬 机器人指令示例

| 指令 | 说明 |
|---|---|
| `新增任务 修复登录Bug 负责人 张三 截止 2024-04-01` | ➕ 新增任务 |
| `登录Bug已完成` | ✅ 标记完成 |
| `修改登录Bug 改为负责人李四` | ✏️ 修改任务 |
| `删除登录Bug任务` | 🗑️ 删除任务 |
| `查询登录Bug的状态` | 🔍 查询任务 |
| `恢复任务 登录Bug` | 🔄 恢复为进行中 |

> 机器人基于 LLM 意图分类，指令可以自然语言表达，无需严格格式。

---

## 📄 许可证

MIT © [AXinsLab](https://github.com/AXinsLab)
