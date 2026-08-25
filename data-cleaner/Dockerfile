FROM python:3.11-slim

WORKDIR /app

# 切换为国内镜像源（默认 Debian 源在部分网络不可达，导致 apt-get update 失败）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g; s/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
 || sed -i 's/deb.debian.org/mirrors.aliyun.com/g; s/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# 基础构建工具（仅 pandas/numpy 轮子编译所需，无需 TA-Lib C 库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY . .

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
