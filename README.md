# 飞书群聊 Todo 智能体

基于 LangGraph 构建的飞书群聊任务追踪机器人。自动提取群消息中的任务、管理进度，并每日发送工作报告。

## 功能特性

- **@机器人 交互**：通过自然语言新增、修改、删除、查询、恢复任务
- **定时分析**：每日 09:30 自动分析昨日群消息，提取新任务，更新已完成任务
- **每日报告**：生成包含完成情况、进行中任务（含逾期标注）的结构化日报
- **多群支持**：一个机器人管理多个群，数据相互隔离
- **双重去重**：消息 ID 硬过滤 + LLM 语义去重，避免重复创建任务
- **数据持久化**：任务、成员、群配置存储于飞书多维表格

## 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI |
| Agent 编排 | LangGraph |
| LLM | AzureChatOpenAI（DeepSeek-V3） |
| 飞书 SDK | lark-oapi |
| 存储 | 飞书多维表格（Bitable） |
| 断点续跑 | langgraph-checkpoint-sqlite |
| 运行时 | Python 3.13 + uv |

## 项目结构

```
├── main.py                  # FastAPI 入口，Webhook 路由
├── config.py                # 配置（pydantic-settings）
├── schemas/
│   ├── models.py            # Pydantic 数据模型
│   └── state.py             # LangGraph State 定义
├── graphs/
│   ├── onboard_graph.py     # 入群初始化 Graph
│   ├── message_graph.py     # @机器人消息处理 Graph
│   └── scheduler_graph.py  # 定时总结 Graph
├── nodes/
│   ├── feishu_nodes.py      # 飞书相关节点
│   ├── bitable_nodes.py     # 多维表格 CRUD 节点
│   ├── llm_nodes.py         # LLM 调用节点
│   └── report_nodes.py      # 报告生成节点
├── prompts/
│   ├── intent.py            # 意图分类 Prompt
│   ├── analyzer.py          # 消息分析 Prompt
│   └── report.py            # 回复文本常量
├── tools/
│   ├── feishu_client.py     # 飞书 API 客户端
│   ├── bitable_client.py    # 多维表格客户端
│   ├── storage_interface.py # 存储抽象接口
│   └── llm_client.py        # LLM 单例
├── tests/                   # pytest 测试（42 个用例）
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 快速开始

### 前置要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- 飞书开放平台应用（已开通消息、群成员、多维表格权限）
- Azure AI Foundry 部署的 DeepSeek-V3 模型

### 安装

```bash
git clone https://github.com/AXinsLab/feishu_group_todo.git
cd feishu_group_todo
uv sync
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入所有配置项
```

主要配置项（详见 `.env.example`）：

```env
AZURE_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_DEPLOYMENT=deepseek-v3
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxx
BITABLE_APP_TOKEN=xxxxxxxx
OPS_CHAT_ID=oc_xxxxxxxx
WEBHOOK_SECRET=your-secret
```

### 运行

```bash
# 开发模式
uv run uvicorn main:app --reload --port 8000

# 生产（Docker）
docker compose up -d
```

### 测试

```bash
uv run pytest tests/ -v
```

## 飞书应用配置

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. **事件订阅** → 请求 URL 填写 `https://your-domain/webhook/feishu`
3. 订阅以下事件：
   - `im.message.receive_v1`（接收消息）
   - `im.chat.member.bot.added_v1`（机器人入群）
4. 开通权限：消息读写、获取群成员、多维表格读写

## 定时任务

在服务器上配置 cron，每日 09:30 触发定时总结：

```bash
30 9 * * * curl -X POST https://your-domain/webhook/scheduler \
  -H "X-Webhook-Secret: your-secret"
```

## 机器人指令示例

| 指令 | 说明 |
|---|---|
| `新增任务 修复登录Bug 负责人 张三 截止 2024-04-01` | 新增任务 |
| `登录Bug已完成` | 标记任务完成 |
| `修改登录Bug 改为负责人李四` | 修改任务 |
| `删除登录Bug任务` | 删除任务 |
| `查询登录Bug的状态` | 查询任务 |
| `恢复任务 登录Bug` | 将已完成任务恢复为进行中 |

## License

MIT
