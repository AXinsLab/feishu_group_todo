<div align="center">

# 🤖 Feishu Group Todo Agent

**An intelligent task tracking bot for Feishu group chats, powered by LangGraph & DeepSeek-V3.**

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-42%20passed-brightgreen?style=flat-square&logo=pytest)](tests/)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple?style=flat-square)](https://docs.astral.sh/uv/)

[English](#-features) · [中文](README.zh-CN.md)

</div>

---

## ✨ Features

- 🗣️ **Natural Language Interaction** — Mention the bot to create, update, delete, query, or restore tasks using plain language
- ⏰ **Scheduled Analysis** — Every morning at 09:30, automatically analyzes yesterday's messages, extracts new tasks, and marks completed ones
- 📊 **Daily Reports** — Sends structured daily reports with completed tasks, in-progress items, and overdue warnings
- 👥 **Multi-Group Support** — Manages multiple Feishu groups with fully isolated data per group
- 🔁 **Deduplication** — Two-layer dedup: hard message-ID filter + LLM semantic comparison to prevent duplicate tasks
- 💾 **Feishu Bitable Storage** — Tasks, members, and group configs are persisted in Feishu Bitable (spreadsheet-like database)
- 🔄 **Resumable Workflows** — SQLite-based checkpoint support for crash recovery in scheduled tasks

---

## 🏗️ Architecture

Three independent LangGraph workflows handle all scenarios:

```
Feishu Event
    │
    ├─── 🤖 Bot Added to Group  ──►  OnboardGraph
    │                                 └─ Create Bitable tables
    │                                 └─ Sync members
    │                                 └─ Send introduction
    │
    ├─── 💬 @Bot Message        ──►  MessageGraph
    │                                 └─ Classify intent (LLM)
    │                                 └─ Execute CRUD operation
    │                                 └─ Reply to user
    │
    └─── ⏱️ Cron Trigger        ──►  SchedulerGraph
                                      └─ Fetch & filter messages
                                      └─ LLM analysis
                                      └─ Update task statuses
                                      └─ Send daily report
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| 🌐 Web Framework | [FastAPI](https://fastapi.tiangolo.com/) |
| 🧠 Agent Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| 🤖 LLM | AzureChatOpenAI (DeepSeek-V3 via Azure AI Foundry) |
| 📡 Feishu SDK | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) |
| 🗄️ Storage | Feishu Bitable (Todo, Members, Group Config tables) |
| 💾 Checkpointing | langgraph-checkpoint-sqlite |
| ⚡ Runtime | Python 3.13 + [uv](https://docs.astral.sh/uv/) |

---

## 📁 Project Structure

```
feishu_group_todo/
├── 📄 main.py                   # FastAPI entry point & webhook routes
├── ⚙️  config.py                 # Settings (pydantic-settings + lru_cache)
│
├── 📐 schemas/
│   ├── models.py                # Pydantic v2 data models & StrEnum types
│   └── state.py                 # LangGraph TypedDict states (3 graphs)
│
├── 🔀 graphs/
│   ├── onboard_graph.py         # Bot join initialization workflow
│   ├── message_graph.py         # @mention message handling workflow
│   └── scheduler_graph.py       # Scheduled summarization workflow
│
├── 🧩 nodes/
│   ├── feishu_nodes.py          # Feishu API interaction nodes
│   ├── bitable_nodes.py         # Bitable CRUD nodes
│   ├── llm_nodes.py             # LLM inference nodes
│   └── report_nodes.py          # Report & reply generation nodes
│
├── 💬 prompts/
│   ├── intent.py                # Intent classification prompt + schema
│   ├── analyzer.py              # Message analysis prompt + schema
│   └── report.py                # Reply text templates
│
├── 🔧 tools/
│   ├── feishu_client.py         # Feishu client (TokenManager, RateLimiter, retry)
│   ├── bitable_client.py        # StorageInterface implementation
│   ├── storage_interface.py     # Abstract storage interface (swappable backend)
│   └── llm_client.py            # AzureChatOpenAI singleton
│
├── 🧪 tests/                    # pytest test suite (42 test cases)
│   ├── conftest.py              # Shared fixtures (mock_storage, mock_feishu, mock_llm)
│   ├── fixtures/                # JSON test data
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

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- A Feishu Open Platform app (with message, member, and Bitable permissions)
- DeepSeek-V3 model deployed on Azure AI Foundry

### Installation

```bash
git clone https://github.com/AXinsLab/feishu_group_todo.git
cd feishu_group_todo
uv sync
```

### Configuration

```bash
cp .env.example .env
# Fill in your credentials
```

Key environment variables (see `.env.example` for full list):

```env
# Azure AI Foundry
AZURE_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_DEPLOYMENT=deepseek-v3
AZURE_API_VERSION=2024-05-01-preview
AZURE_API_KEY=your-api-key

# Feishu App
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxx

# Storage
BITABLE_APP_TOKEN=xxxxxxxx

# Ops
OPS_CHAT_ID=oc_xxxxxxxx
WEBHOOK_SECRET=your-secret
```

### Run

```bash
# Development
uv run uvicorn main:app --reload --port 8000

# Production (Docker)
docker compose up -d
```

### Test

```bash
uv run pytest tests/ -v
```

---

## ⚙️ Feishu App Setup

1. Create an internal app at [open.feishu.cn](https://open.feishu.cn)
2. **Event Subscriptions** → Set request URL to `https://your-domain/webhook/feishu`
3. Subscribe to events:
   - `im.message.receive_v1` — Receive messages
   - `im.chat.member.bot.added_v1` — Bot added to group
4. Grant permissions: read/write messages, get group members, read/write Bitable

---

## ⏱️ Scheduled Trigger

Configure a cron job on your server to trigger daily reports at 09:30:

```bash
30 9 * * * curl -X POST https://your-domain/webhook/scheduler \
  -H "X-Webhook-Secret: your-secret"
```

---

## 💬 Bot Command Examples

| Command | Action |
|---|---|
| `Add task Fix login bug assignee Alice due 2024-04-01` | ➕ Create task |
| `Login bug is done` | ✅ Mark as completed |
| `Update login bug change assignee to Bob` | ✏️ Update task |
| `Delete login bug task` | 🗑️ Delete task |
| `Query status of login bug` | 🔍 Query task |
| `Restore task login bug` | 🔄 Restore to in-progress |

> The bot uses LLM-based intent classification, so commands can be expressed naturally in Chinese or English.

---

## 📄 License

MIT © [AXinsLab](https://github.com/AXinsLab)
