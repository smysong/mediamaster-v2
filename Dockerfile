# 使用 Alpine 最新版本作为基础镜像
FROM alpine:latest

# 设置工作目录
WORKDIR /app

# 安装基础工具和依赖
RUN apk add --no-cache \
    bash \
    curl \
    unzip \
    python3 \
    python3-dev \
    py3-pip \
    chromium \
    chromium-chromedriver \
    tzdata \
    iproute2 \
    iputils \
    bind-tools \
    vim \
    tini \
    musl-locales \
    musl-locales-lang \
    gcc \
    musl-dev \
    linux-headers

# 设置时区为中国上海
RUN cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

# 设置语言环境为简体中文
ENV LANG=zh_CN.UTF-8
ENV LC_ALL=zh_CN.UTF-8

# 创建日志保存目录
RUN mkdir -p /tmp/log

# 安装 git 用于克隆仓库
RUN apk add --no-cache git

# 克隆 Git 仓库到 /app 目录
RUN git clone https://github.com/smysong/mediamaster-v2.git /app

# 创建虚拟环境
RUN python3 -m venv /app/venv

# 激活虚拟环境并安装 Python 依赖
# 在容器中永久设置 PATH 以优先使用虚拟环境中的 python 和 pip
ENV VIRTUAL_ENV=/app/venv
ENV PATH="/app/venv/bin:$PATH"

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 安装 schedule 库
RUN pip install schedule

# 确保 set_ulimits.sh 脚本具有执行权限
RUN chmod +x /app/set_ulimits.sh

# 声明监听端口
EXPOSE 8888

# 使用 tini 作为主进程管理工具
ENTRYPOINT ["/sbin/tini", "--"]

# 启动应用
CMD ["python", "main.py"]