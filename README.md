<div align="center">

# 🤖 Feishu Group Todo Agent

**An intelligent task tracking bot for Feishu (Lark) group chats, powered by LangGraph + DeepSeek-V3.**

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple?style=flat-square)](https://docs.astral.sh/uv/)

[English](README.md) · [中文](README.zh-CN.md)

</div>

---

## ✨ Features

- 🗣️ **Natural Language Interaction** — Mention the bot to create, update, delete, query, or restore tasks using plain language
- 📝 **Multi-Task in One Message** — Operate on multiple tasks in a single reply; each operation executes independently and partial failures are reported clearly
- 🔢 **Number & Semantic Reference** — Reference tasks by index ("item 3", "the 4th one") or by implicit description ("that MIC evaluation") — the bot resolves both
- 📌 **Progress Notes** — When marking tasks complete, any attached explanation is automatically saved to the progress note field
- ⏰ **Scheduled Daily Analysis** — Every morning at 09:30, automatically analyzes the past 24 hours of group messages, extracts new tasks, and marks completed ones
- 📊 **Numbered Task Reports** — Daily reports with indexed tasks, completed items with strikethrough, and per-assignee @mention
- 👥 **Multi-Group Support** — One bot instance manages multiple groups; all data is fully isolated per group
- 🔁 **Two-Layer Deduplication** — Hard message-ID filter + LLM semantic comparison to prevent duplicate task creation
- 💾 **Feishu Bitable Storage** — Tasks, members, and group configs are persisted in Feishu Bitable (no extra database needed)
- 🔄 **Schema Auto-Repair** — On bot join or `/init`, missing Bitable tables and fields are created automatically
- 🤖 **Slash Command System** — Extensible command registry; add a new command by registering one entry — no graph changes required
- 🔐 **AES-CBC Webhook Encryption** — Supports Feishu encrypted webhook payloads out of the box
- ♻️ **Crash Recovery** — SQLite checkpointer lets the scheduler resume from the last checkpoint after a failure

---

## 💬 Bot Usage

### Natural Language Task Management (@ the bot)

| Example message | Action |
|---|---|
| `Ask Alice to prepare the meeting notes by Friday` | ➕ Create task, assignee: Alice |
| `Bob finished the login bug fix` | ✅ Mark task as completed |
| `Change the assignee for login bug to Carol` | ✏️ Update task |
| `Remove the login bug task` | 🗑️ Delete task |
| `What is the status of the login bug?` | 🔍 Query task status |
| `Reopen the login bug task` | 🔄 Restore to in-progress |
| `Item 3: can't share parts, will add tooling cost` | ✅ Mark task #3 complete + save progress note |
| `Item 4 has a solid mockup; item 2 is done` | ✅ Mark tasks #4 and #2 complete in one message |

> When no assignee is specified, the **message sender** is automatically set as the assignee.
>
> You can reference tasks by their **numbered index** (item 3, the 4th one) or by **natural language description** — the bot resolves both using the active task list.

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
    │                                 └─ [natural lang] → classify intent (LLM)
    │                                       ├─ build pending_operations list
    │                                       ├─ execute each op independently
    │                                       └─ reply with per-op results
    │
    └─── ⏱️ Cron Trigger        ──►  SchedulerGraph
                                      ├─ Refresh members
                                      ├─ Fetch & deduplicate messages (filter bot msgs)
                                      ├─ LLM analysis (new tasks + completion detection)
                                      ├─ Resolve assignee names → open_id
                                      ├─ Write to Bitable
                                      └─ Send daily report card
```

### MessageGraph Flow Detail

```
parse_event
    │
    ├─ /command ──► execute_command ──► send_reply
    │
    └─ natural lang
          │
          ├─ fetch_all_todos
          ├─ fetch_members          (pre-load before LLM so assignee names resolve)
          ├─ classify_intent (LLM)  (builds pending_operations list)
          │     ├─ UNRELATED ──► build_reject_reply ──► send_reply
          │     └─ task op   ──► resolve_operation
          │                         └─ execute_operation  (loop over pending_operations)
          │                               └─ build_confirm_reply ──► send_reply
```

### Slash Command Extension

Commands are registered in `COMMAND_REGISTRY` in `nodes/command_nodes.py`. To add a new command:

```python
COMMAND_REGISTRY["/mycommand"] = {
    "description": "What this command does",
    "handler": my_handler_fn,   # async (state, feishu, storage) -> str
}
```

No graph changes required. All handlers share a unified error-catch wrapper.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | [FastAPI](https://fastapi.tiangolo.com/) |
| Agent Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| LLM | AzureChatOpenAI (DeepSeek-V3 via Azure AI Foundry) |
| Feishu SDK | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) |
| Storage | Feishu Bitable (3 tables: Todo, Members, Group Config) |
| Checkpointing | langgraph-checkpoint-sqlite |
| Runtime | Python 3.13 + [uv](https://docs.astral.sh/uv/) |
| Webhook Security | AES-CBC decryption + HMAC secret verification |

---

## 📁 Project Structure

```
feishu_group_todo/
├── main.py                      # FastAPI entry point, webhook routes, AES decryption
├── config.py                    # Settings (pydantic-settings + lru_cache)
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
│   ├── bitable_nodes.py         # Bitable CRUD + multi-op execution loop
│   ├── llm_nodes.py             # LLM inference + intent → pending_operations
│   └── report_nodes.py          # Report card builder + reply text generation
│
├── prompts/
│   ├── intent.py                # Intent classification prompt + OperationItem schema
│   ├── analyzer.py              # Scheduler message analysis prompt + schema
│   └── report.py                # Reply text templates
│
├── tools/
│   ├── feishu_client.py         # Feishu API client (token mgr, rate limiter, retry)
│   ├── bitable_client.py        # StorageInterface implementation + ensure_schema()
│   ├── storage_interface.py     # Abstract storage interface
│   └── llm_client.py            # AzureChatOpenAI singleton
│
├── tests/                       # pytest test suite
│   ├── conftest.py              # Shared fixtures (mock_storage, mock_feishu, mock_llm)
│   ├── fixtures/                # JSON test data
│   └── test_*.py
│
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.13+ with [uv](https://docs.astral.sh/uv/)
- A Feishu Open Platform internal app (permissions listed below)
- DeepSeek-V3 (or compatible) model deployed on Azure AI Foundry

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
docker compose up -d --build
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

## 🧪 Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_message_graph.py -v

# Lint and format
uv run ruff check .
uv run ruff format .
```

---

## 📄 License

MIT © [AXinsLab](https://github.com/AXinsLab)
