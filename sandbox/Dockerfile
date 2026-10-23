
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV NPM_CONFIG_PREFIX=/usr/local
ENV WORKSPACE=/workspace

# --------------------------------------------------
# System dependencies
# --------------------------------------------------

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    tree \
    ca-certificates \
    build-essential \
    ripgrep \
    jq \
    bubblewrap \
    gnupg \
    && rm -rf /var/lib/apt/lists/*


# --------------------------------------------------
# Node.js 20
# --------------------------------------------------

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && npm --version


# --------------------------------------------------
# GitHub CLI
# The pipeline opens pull requests with `gh` after the coding engine commits
# and pushes a branch (see SandboxManager.open_pull_request). Installed from
# GitHub's apt repo so the arch matches the base image (amd64 / arm64).
# GH_TOKEN is injected as a container env var at runtime (see sandbox/manager.py).
# --------------------------------------------------

RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && gh --version


# --------------------------------------------------
# Python tools
# --------------------------------------------------

RUN pip install --no-cache-dir \
    pytest \
    black \
    tree-sitter \
    requests


# --------------------------------------------------
# OpenAI Codex CLI
# --------------------------------------------------

RUN npm install -g @openai/codex@latest \
    && codex --version


# --------------------------------------------------
# opencode CLI (free/OpenRouter-backed alternative engine)
# --------------------------------------------------

# RUN npm install -g opencode-ai


# --------------------------------------------------
# Repo-snapshot script (github-memory), generated once per sandbox at boot
# --------------------------------------------------

COPY generate_github_memory.py /usr/local/bin/generate_github_memory.py


# --------------------------------------------------
# Create non-root agent user
# --------------------------------------------------

RUN useradd \
    --create-home \
    --shell /bin/bash \
    agentuser

RUN mkdir -p /workspace \
    && chown -R agentuser:agentuser /workspace /home/agentuser


# --------------------------------------------------
# Switch to agent user
# --------------------------------------------------

USER agentuser

WORKDIR /workspace


# --------------------------------------------------
# Codex config: model/effort/agent limits. Overwritten at sandbox boot with
# the per-run model pin (see agents/pipeline_agent/sandbox_runtime.py).
# --------------------------------------------------

COPY --chown=agentuser:agentuser config.toml /home/agentuser/.codex/config.toml


# --------------------------------------------------
# Static skills - baked into the image at build time, identical for every
# run, unlike the per-ticket plan/context which is injected at runtime.
# --------------------------------------------------

RUN npx --yes skills add https://github.com/alirezarezvani/claude-skills \
    --skill jira-expert \
    --agent codex \
    # --agent opencode \
    --global \
    --yes


# --------------------------------------------------
# Verify Codex / opencode
# --------------------------------------------------

RUN codex --version
# RUN opencode --version


# --------------------------------------------------
# Start sandbox
# --------------------------------------------------

CMD ["/bin/bash"]
