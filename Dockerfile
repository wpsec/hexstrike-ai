FROM kalilinux/kali-rolling

ARG KALI_MIRROR=http://mirrors.aliyun.com/kali
ARG SECURITY_TOOLS_PROFILE=standard
ARG SECURITY_TOOLS_STRICT=0
ARG SECURITY_TOOLS_NO_BROWSER=1

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.mirrors.ustc.edu.cn/simple/ \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /opt/hexstrike

RUN printf 'deb %s kali-rolling main non-free contrib\n' "${KALI_MIRROR}" > /etc/apt/sources.list && \
    printf 'deb-src %s kali-rolling main non-free contrib\n' "${KALI_MIRROR}" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      python3 \
      python3-pip \
      python3-venv \
      build-essential \
      python3-dev \
      chromium \
      chromium-driver && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY scripts/install_security_tools.sh /usr/local/bin/install_security_tools.sh

RUN chmod +x /usr/local/bin/install_security_tools.sh && \
    INSTALL_ARGS="--profile ${SECURITY_TOOLS_PROFILE} --non-interactive" && \
    if [ "${SECURITY_TOOLS_NO_BROWSER}" = "1" ]; then INSTALL_ARGS="${INSTALL_ARGS} --no-browser"; fi && \
    if [ "${SECURITY_TOOLS_STRICT}" = "1" ]; then INSTALL_ARGS="${INSTALL_ARGS} --strict"; fi && \
    /usr/local/bin/install_security_tools.sh ${INSTALL_ARGS} && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "${VIRTUAL_ENV}" && \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

COPY . .

EXPOSE 8888

CMD ["python3", "hexstrike_server.py", "--port", "8888"]
