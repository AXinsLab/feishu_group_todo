FROM python:3.13-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装生产依赖（--frozen 确保 lock 文件一致，--no-dev 排除开发依赖）
RUN uv sync --frozen --no-dev

# 复制应用代码
COPY . .

# 创建数据目录（用于 SQLite Checkpointer 持久化）
RUN mkdir -p data

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
