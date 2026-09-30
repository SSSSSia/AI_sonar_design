# T9.5（PHASE-009）py-engine 镜像：uvicorn(8090) / celery worker / 按需子进程
# （convert_worker / geometry_inspect 由代码 subprocess 拉起，同一镜像即可）
# 构建：docker build -f deploy/docker/py.Dockerfile .（上下文 = 仓库根）
# APT_MIRROR：deb.debian.org/aliyun 在本网络不稳（500 EOF/404），默认 USTC（实测 2MB/s）
ARG APT_MIRROR=mirrors.ustc.edu.cn
FROM python:3.12-slim
# ARG 在 FROM 前声明不进入构建阶段，须在阶段内重复声明
ARG APT_MIRROR

RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null; \
    true
# OCP（cadquery 内核）在 slim 上的运行库；libllvm19 较大，失败重试一次
RUN (apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libxrender1 libxext6 libglib2.0-0) \
    || (apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libxrender1 libxext6 libglib2.0-0) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# PIP_INDEX：pypi.org 直连不稳（实测中断），默认清华源，build-arg 可覆盖
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_NO_CACHE_DIR=1 PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONUNBUFFERED=1

# 依赖层（cadquery/OCP manylinux wheel 纯 pip 路线——T9.5 决策：先试 pip，失败退 micromamba）
COPY py-engine/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY py-engine/ ./

EXPOSE 8090
# 0.0.0.0：容器内须对外（宿主开发模式是 127.0.0.1，互不影响——由启动方指定）
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8090", "--timeout-keep-alive", "30"]
