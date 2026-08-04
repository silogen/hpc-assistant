# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

FROM python:3.12-slim

WORKDIR /workspace

RUN pip install uv --no-cache-dir
RUN uv tool install chroma-mcp

ENV PATH="/root/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY LICENSE.TXT THIRD-PARTY-NOTICES.txt /licenses/

VOLUME ["/workspace/chroma_data"]

CMD ["uvx", "chroma-mcp", "--client-type", "persistent", "--data-dir", "/workspace/chroma_data"]
