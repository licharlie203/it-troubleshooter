# IT 运维故障知识库 —— 容器镜像
# 构建：docker build -t it-troubleshooter .
# 运行：docker run -p 8000:8000 -e DASHSCOPE_API_KEY=sk-xxx it-troubleshooter
#       （Windows CMD 里把 \ 换成一行写；PowerShell 也一样一行写即可）
#
# 注意：Key 通过 -e 环境变量传入，不要写进镜像（见 .dockerignore 已排除 config.py）

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 先装依赖（单独一层，改代码时不用重装依赖，构建更快）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

EXPOSE 8000

# HTTP 接口（给别人调用）
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

# 想跑网页界面就改成：
# CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
