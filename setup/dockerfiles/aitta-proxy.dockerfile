# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

# Aitta proxy = the official LiteLLM proxy with our Aitta config baked in.
#
# This replaces the previous hand-written FastAPI Anthropic-to-OpenAI proxy.
# LiteLLM is the well-known proxy framework for exactly this translation; we
# moved off it temporarily during the March 2026 PyPI supply-chain incident
# (compromised litellm 1.82.7/1.82.8) and now pin to a vetted stable release.
#
# v1.83.14-stable is built by LiteLLM's hardened CI/CD v2 pipeline (post
# incident) and is well past the CVE-2026-42208 SQL-injection fix, which
# affected proxy versions <= 1.83.6 (fixed in 1.83.7+).
FROM ghcr.io/berriai/litellm:v1.83.14-stable

WORKDIR /app

COPY setup/aitta-proxy/config.yaml /app/config.yaml
COPY LICENSE.TXT THIRD-PARTY-NOTICES.txt /licenses/

# The launch scripts route via the `hosted_vllm/` provider, which already goes
# straight to /chat/completions (not the OpenAI Responses API). This flag is a
# defensive default in case someone runs an `openai/`-prefixed model: since
# LiteLLM v1.82.0 the `openai` provider routes /v1/messages through /responses,
# which Aitta doesn't implement. It must be an env var — the equivalent
# litellm_settings YAML key is not reliably loaded (see BerriAI/litellm#28756).
ENV LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES=true

EXPOSE 8001

# The base image's entrypoint runs the `litellm` CLI; these are its arguments.
# The launch scripts override the port via a separate `exec` when running as a
# Singularity/Apptainer instance, but this keeps `docker run` / `singularity
# run` working out of the box.
CMD ["--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "8001"]
