# Layered on the base sandbox image rather than duplicating its whole build:
# same Codex/git/node/skills setup, plus everything a Salesforce DX repo needs
# to lint / format / unit-test / static-analyse WITHOUT any runtime install.
# Any update to the base Dockerfile (new skills, Codex version, etc.) is picked
# up here automatically on the next build.
FROM agent-sandbox:latest

USER root

# --------------------------------------------------
# Node.js 22 - the base image's Node 20 is missing a Web API
# (webidl.util.markAsUncloneable) that Salesforce CLI's bundled undici
# dependency requires; it crashes on startup under Node 20.
# --------------------------------------------------

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# --------------------------------------------------
# Java (JRE) - Salesforce Code Analyzer v5's PMD and CPD engines are Java
# programs. Without a JRE `sf code-analyzer run` fails with "Could not locate
# Java" and every review dinged the ticket for static analysis it never ran
# (AG-191..197). default-jre-headless pulls OpenJDK 17 on bookworm and provides
# the stable /usr/lib/jvm/default-java path.
# --------------------------------------------------

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/* \
    && java -version

ENV JAVA_HOME=/usr/lib/jvm/default-java

# --------------------------------------------------
# Salesforce CLI + Code Analyzer plugin (pre-installed, not fetched per run)
# --------------------------------------------------

RUN npm install -g @salesforce/cli \
    && sf --version

# --------------------------------------------------
# Global fallback toolchain for repos that ship no package.json. When the repo
# HAS one, prepare_sandbox_repo runs `npm ci` and the project's own pinned
# versions win (see agents/pipeline_agent/sandbox_runtime.py). NODE_PATH lets a
# project `.prettierrc` that names "prettier-plugin-apex" resolve the global
# copy when node_modules is absent.
# --------------------------------------------------

RUN npm install -g \
        prettier \
        prettier-plugin-apex \
        @prettier/plugin-xml \
        @salesforce/sfdx-lwc-jest \
        eslint \
    && prettier --version \
    && (sfdx-lwc-jest --version || true)

ENV NODE_PATH=/usr/local/lib/node_modules
# husky git hooks re-run lint/jest inside every `git commit`; the worker is told
# to commit early and often even with failing checks, so disable hook install.
ENV HUSKY=0
# WS-2.2: kill `sf` startup cost + the deploy-spinner ANSI spam that used to
# ride in every session's diary (18-27 spinner events per run in the traces).
ENV SF_DISABLE_TELEMETRY=true
ENV SF_AUTOUPDATE_DISABLE=true
ENV SF_DISABLE_AUTOUPDATE=true
ENV SF_NO_SPINNER=true
ENV SF_DISABLE_LOG_FILE=true

# /opt is root-owned; make the baked-toolchain dir writable for agentuser
# before we drop privileges (the npm cache must be agentuser's ~/.npm).
RUN mkdir -p /opt/sf-toolchain && chown -R agentuser:agentuser /opt/sf-toolchain

USER agentuser

# Pre-install the Code Analyzer sf plugin into agentuser's config and pre-warm
# the npm cache with a representative Salesforce DX devDependency set, so the
# per-run `npm ci` is a near-offline cache restore instead of a cold download.
RUN (echo y | sf plugins install code-analyzer \
     || echo y | sf plugins install @salesforce/plugin-code-analyzer \
     || true) \
    && (sf code-analyzer --help > /dev/null 2>&1 && echo "code-analyzer OK" || echo "code-analyzer NOT pre-installed")

# WS-2.1: bake a full node_modules for the canonical Salesforce DX devDeps at a
# fixed path and KEEP it. At sandbox boot _npm_install_project symlinks this
# into the cloned repo (~0.5s) instead of running `npm ci` (27-35s every run).
# The pipeline generates repos from `sf project generate --template standard`,
# so the dep set is fixed; a repo with genuinely custom deps still falls back
# to `npm ci`. package-lock.json is kept so a boot-time checksum compare is
# possible later.
# --legacy-peer-deps: the SF DX eslint stack has long-standing peer conflicts
# (@lwc/eslint-plugin-lwc v3 wants eslint 9, the SF configs want 8). The baked
# tree only has to make `npm run lint / prettier / test:unit` runnable; repos
# with a real lockfile fall back to `npm ci` anyway. On failure we drop the
# dir so _npm_install_project cleanly takes the npm-ci path instead.
RUN cd /opt/sf-toolchain \
    && npm init -y > /dev/null \
    && ( npm install --no-audit --no-fund --legacy-peer-deps \
        @lwc/eslint-plugin-lwc@^1 \
        @prettier/plugin-xml \
        @salesforce/eslint-config-lwc \
        @salesforce/eslint-plugin-aura \
        @salesforce/eslint-plugin-lightning \
        @salesforce/sfdx-lwc-jest \
        eslint@^8 \
        eslint-plugin-import \
        eslint-plugin-jest \
        jest \
        prettier \
        prettier-plugin-apex \
      && ./node_modules/.bin/prettier --version \
      && chmod -R a+rX /opt/sf-toolchain \
      || ( echo "WARN: baked toolchain skipped (npm resolve failed)"; rm -rf /opt/sf-toolchain/node_modules ) )

WORKDIR /workspace
