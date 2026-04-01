<div align="center">

# 🤖 Feishu Group Todo Agent

**An intelligent task tracking bot for Feishu group chats, powered by LangGraph + Azure AI (DeepSeek-V3).**

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple?style=flat-square)](https://docs.astral.sh/uv/)

</div>

---

## ✨ Features

- 🗣️ **Natural Language Interaction** — Mention the bot to create, update, delete, query, or restore tasks using plain language (Chinese or English)
- ⏰ **Scheduled Daily Analysis** — Every morning at 09:30, automatically analyzes the past 24 hours of messages, extracts new tasks, and marks completed ones
- 📊 **Numbered Task Reports** — Daily reports with indexed tasks (1. 2. 3. …), completed items, and per-user @mention for assignees
- 👥 **Auto Member Sync** — Group member list is refreshed every day at 09:30 and on every `/init` or `/update` call
- 🔁 **Two-Layer Deduplication** — Hard message-ID filter + LLM semantic comparison to prevent duplicate tasks
- 💾 **Feishu Bitable Storage** — Tasks, members, and group configs are persisted in Feishu Bitable
- 🔄 **Schema Auto-Repair** — On startup or `/init`, automatically creates missing Bitable sub-tables and fills in missing fields
- 🤖 **Slash Command System** — Extensible slash command registry; new commands can be added by registering one entry — no graph changes required
- 🔐 **AES-CBC Webhook Encryption** — Supports Feishu encrypted webhook payloads out of the box

---

## 💬 Bot Usage

### Natural Language Task Management (@ the bot)

| Example | Action |
|---|---|
| `让甘鑫明天到会议室开会` | ➕ Create task, assignee: 甘鑫 |
| `登录Bug张三修完了` | ✅ Mark task as completed |
| `把登录Bug的负责人改成李四` | ✏️ Update task |
| `删掉登录Bug这个任务` | 🗑️ Delete task |
| `登录Bug现在什么状态` | 🔍 Query task status |
| `把登录Bug重新激活` | 🔄 Restore to in-progress |

> When no assignee is specified, the **message sender** is automatically set as the assignee.

### Slash Commands (@ the bot)

| Command | Description |
|---|---|
| `/help` | Show all available commands |
| `/init` | Sync group members + auto-repair Bitable schema |
| `/tasks` | Show current task list with numbered index |
| `/my` | Show only tasks assigned to you |
| `/update` | Analyze past 24h messages, update task table, send report |
| `/delete 2` | Delete task #2 |
| `/delete 1 3 5` | Batch delete tasks #1, #3, #5 |

---

## 🏗️ Architecture

Three independent LangGraph workflows handle all scenarios:

```
Feishu Event
    │
    ├─── 🤖 Bot Added to Group  ──►  OnboardGraph
    │                                 ├─ Auto-create / repair Bitable tables
    │                                 ├─ Sync group members
    │                                 └─ Send introduction message
    │
    ├─── 💬 @Bot Message        ──►  MessageGraph
    │                                 ├─ [/command] → Slash command handler (no LLM)
    │                                 └─ [natural lang] → LLM intent classify → CRUD → reply
    │
    └─── ⏱️ Cron Trigger        ──►  SchedulerGraph
                                      ├─ Refresh members
                                      ├─ Fetch & deduplicate messages
                                      ├─ LLM analysis (new tasks + completed detection)
                                      ├─ Write to Bitable
                                      └─ Send daily report to group
```

### Slash Command Architecture

Commands are registered in a single `COMMAND_REGISTRY` dict in `nodes/command_nodes.py`. To add a new command:

```python
COMMAND_REGISTRY["/mycommand"] = {
    "description": "What this command does",
    "handler": my_handler_fn,   # async (state, feishu, storage) -> str
}
```

No changes to the graph are required. All handlers share a unified error-catch wrapper that sends failure messages back to the group.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| 🌐 Web Framework | [FastAPI](https://fastapi.tiangolo.com/) |
| 🧠 Agent Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| 🤖 LLM | AzureChatOpenAI (DeepSeek-V3 via Azure AI Foundry) |
| 🗄️ Storage | Feishu Bitable (3 tables: Todo, Members, Group Config) |
| 💾 Checkpointing | langgraph-checkpoint-sqlite |
| ⚡ Runtime | Python 3.13 + [uv](https://docs.astral.sh/uv/) |
| 🔐 Webhook Security | AES-CBC decryption + HMAC secret verification |

---

## 📁 Project Structure

```
feishu_group_todo/
├── main.py                      # FastAPI entry point, webhook routes, AES decryption
├── config.py                    # Settings (pydantic-settings)
│
├── schemas/
│   ├── models.py                # OperationType enum, Pydantic models
│   └── state.py                 # LangGraph TypedDict states (3 graphs)
│
├── graphs/
│   ├── onboard_graph.py         # Bot-join initialization workflow
│   ├── message_graph.py         # @mention message handling workflow
│   └── scheduler_graph.py       # Scheduled analysis workflow
│
├── nodes/
│   ├── command_nodes.py         # Slash command registry + all handlers
│   ├── feishu_nodes.py          # Feishu API interaction nodes
│   ├── bitable_nodes.py         # Bitable CRUD nodes
│   ├── llm_nodes.py             # LLM inference nodes
│   └── report_nodes.py          # Report & reply text generation
│
├── prompts/
│   ├── intent.py                # Intent classification prompt + Pydantic schema
│   ├── analyzer.py              # Scheduler message analysis prompt + schema
│   └── report.py                # Reply text templates
│
├── tools/
│   ├── feishu_client.py         # Feishu API client (token mgr, rate limiter, retry)
│   ├── bitable_client.py        # StorageInterface implementation + ensure_schema()
│   ├── storage_interface.py     # Abstract storage interface
│   └── llm_client.py            # AzureChatOpenAI singleton
│
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.13+ with [uv](https://docs.astral.sh/uv/)
- A Feishu Open Platform app (permissions listed below)
- DeepSeek-V3 (or compatible) model via Azure AI Foundry

### Installation

```bash
git clone https://github.com/AXinsLab/feishu_group_todo.git
cd feishu_group_todo
uv sync
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your credentials
```

```env
# Azure AI Foundry
AZURE_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_DEPLOYMENT=deepseek-v3
AZURE_API_VERSION=2024-05-01-preview
AZURE_API_KEY=your-api-key

# Feishu App
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_ENCRYPT_KEY=xxxxxxxx          # If webhook encryption is enabled
FEISHU_VERIFICATION_TOKEN=xxxxxxxx

# Bitable (can also be written to data/bitable_token.txt at runtime)
BITABLE_APP_TOKEN=xxxxxxxx

# Operations
OPS_CHAT_ID=oc_xxxxxxxx             # Group to receive system error alerts
WEBHOOK_SECRET=your-secret           # Used to authenticate /webhook/scheduler calls
```

### Run

```bash
# Development
uv run uvicorn main:app --reload --port 8000

# Production (Docker)
docker compose up -d
```

### Bitable Token

The Feishu Bitable app token can be set via `.env` or written directly to `data/bitable_token.txt` (takes precedence). The bot will automatically create any missing sub-tables and fields on startup — no manual table setup required.

---

## ⚙️ Feishu App Setup

1. Create an internal app at [open.feishu.cn](https://open.feishu.cn)
2. **Event Subscriptions** → Set request URL to `https://your-domain/webhook/feishu`
3. Subscribe to events:
   - `im.message.receive_v1`
   - `im.chat.member.bot.added_v1`
4. Grant permissions:
   - `im:message` — Send & receive messages
   - `im:chat.member:readonly` — Read group members
   - `bitable:app` — Read/write Bitable

---

## ⏱️ Scheduled Trigger

Add to crontab on your server to fire daily reports at 09:30:

```bash
# Create a trigger script
cat > /usr/local/bin/feishu_scheduler_trigger.sh << 'EOF'
#!/bin/bash
curl -s -X POST http://localhost:8000/webhook/scheduler \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret" \
  -d "{\"trigger_time\":\"$(date -u +%Y-%m-%dT%H:%M:%S)\"}"
EOF
chmod +x /usr/local/bin/feishu_scheduler_trigger.sh

# Add to crontab
echo "30 9 * * * /usr/local/bin/feishu_scheduler_trigger.sh >> /var/log/feishu_scheduler.log 2>&1" | crontab -
```

---

## 📄 License

MIT © [AXinsLab](https://github.com/AXinsLab)
