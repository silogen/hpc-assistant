# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

FROM python:3.12-slim

WORKDIR /workspace

COPY setup/requirements/build-db.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm

COPY setup/build_db.py /workspace/build_db.py
COPY LICENSE.TXT THIRD-PARTY-NOTICES.txt /licenses/

VOLUME ["/workspace/chroma_data"]

ENTRYPOINT ["python", "/workspace/build_db.py"]
