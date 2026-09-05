# Edu Hierarchical Classifier API 镜像
# 推理镜像使用 CPU 版 torch（训练在本机 GPU 环境进行，不入镜像）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.huggingface_cache

WORKDIR /app

# 先装 CPU 版 torch（2.11.0+cpu 满足 requirements.txt 的 ==2.11.0 约束，
# 避免 PyPI Linux 默认拉取 CUDA 大轮子）
RUN pip install --no-cache-dir torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 源码与配置（模型权重 / 数据集 / 报告目录通过卷挂载，不打入镜像）
COPY app.py .
COPY edu_core ./edu_core
COPY scripts ./scripts
COPY static ./static
COPY tests ./tests

# 运行时目录
RUN mkdir -p logs reports eval_sets models/versions data/processed

EXPOSE 7860

# preflight 在 lifespan 中执行：模型权重/标签/数据库任一缺失即启动失败（无降级路径）
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
