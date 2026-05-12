# LIULIAN unified workspace image.
#
# One container, all repos mounted via volume, all CLIs preinstalled.
# Mirrors neobanker-dev-env's Codespaces pattern for the LIULIAN federation.
#
# Build:   docker compose -f docker-compose.dev.yml build workspace
# Run:     make dev   (from liulian-ops/devcontainer/)
# Shell:   make shell

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_VERSION=20
ARG PYTHON_VERSION=3.11
ARG UV_VERSION=0.5.7

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Europe/Zurich \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Layer 1: system packages ────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git gpg \
        build-essential pkg-config \
        software-properties-common \
        locales tzdata \
        sudo vim less jq tree htop ripgrep fd-find \
        openssh-client autossh \
        unzip zip tar \
        python3 python3-pip python3-venv python3-dev \
        libpq-dev libssl-dev libffi-dev \
        graphviz \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 2: Node + pnpm via nvm-equivalent (NodeSource) ────────────
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pnpm@9 yarn typescript ts-node eas-cli \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 3: Python toolchain (uv) ─────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv --version

# ── Layer 4: container / IaC tooling ────────────────────────────────
RUN curl -fsSL https://get.docker.com -o /tmp/get-docker.sh \
    && DRY_RUN=1 sh /tmp/get-docker.sh \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl

# helm
RUN curl https://baltocdn.com/helm/signing.asc | gpg --dearmor -o /usr/share/keyrings/helm.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" > /etc/apt/sources.list.d/helm-stable-debian.list \
    && apt-get update && apt-get install -y --no-install-recommends helm \
    && rm -rf /var/lib/apt/lists/*

# terraform (HashiCorp apt)
RUN wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | tee /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null \
    && echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs 2>/dev/null || echo noble) main" > /etc/apt/sources.list.d/hashicorp.list \
    && apt-get update && apt-get install -y --no-install-recommends terraform \
    && rm -rf /var/lib/apt/lists/*

# gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 5: user setup (avoid root inside container) ──────────────
ARG USER_NAME=liulian
ARG USER_UID=1000
ARG USER_GID=1000
RUN groupadd --gid ${USER_GID} ${USER_NAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m -s /bin/bash ${USER_NAME} \
    && echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers.d/${USER_NAME}

USER ${USER_NAME}
WORKDIR /workspace

# Friendly shell
RUN echo 'export PS1="\[\033[01;32m\]\u@liulian-workspace\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> ~/.bashrc \
    && echo 'alias ll="ls -lah"' >> ~/.bashrc \
    && echo 'alias gst="git status -sb"' >> ~/.bashrc \
    && echo 'cd /workspace' >> ~/.bashrc

CMD ["sleep", "infinity"]
