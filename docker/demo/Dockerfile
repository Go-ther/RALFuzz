# RALFuzz reproducibility capsule: offline cJSON_Parse demo (Ubuntu 22.04 + Clang 18).
# Build from repo root:
#   docker compose -f docker/demo/docker-compose.yml build

FROM ubuntu:22.04

ARG CJSON_VERSION=v1.7.19

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=Etc/UTC \
    RALFUZZ_ROOT=/opt/ralfuzz \
    CC=clang-18 \
    CXX=clang++-18 \
    PATH=/usr/lib/llvm-18/bin:/opt/ralfuzz-venv/bin:${PATH}

RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release software-properties-common \
    git wget \
    python3.11 python3.11-venv python3.11-dev \
    build-essential binutils \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://apt.llvm.org/llvm.sh \
    && chmod +x llvm.sh \
    && ./llvm.sh 18 \
    && rm llvm.sh

COPY docker/demo/requirements-demo.txt /tmp/requirements-demo.txt
RUN python3.11 -m venv /opt/ralfuzz-venv \
    && /opt/ralfuzz-venv/bin/pip install --upgrade pip \
    && /opt/ralfuzz-venv/bin/pip install --no-cache-dir -r /tmp/requirements-demo.txt

WORKDIR ${RALFUZZ_ROOT}

# Core toolchain (minimal; no batch experiment scripts).
COPY mutation/ ${RALFUZZ_ROOT}/mutation/
COPY seed_generation/ ${RALFUZZ_ROOT}/seed_generation/
COPY pipeline/run_full_pipeline.py \
     pipeline/run_full_pipeline.sh \
     pipeline/fetch_cjson.sh \
     ${RALFUZZ_ROOT}/pipeline/
RUN chmod +x ${RALFUZZ_ROOT}/pipeline/run_full_pipeline.sh \
             ${RALFUZZ_ROOT}/pipeline/fetch_cjson.sh

COPY repro_artifacts/illustrative/ ${RALFUZZ_ROOT}/repro_artifacts/illustrative/

COPY docker/demo/scripts/ ${RALFUZZ_ROOT}/docker/demo/scripts/
RUN chmod +x ${RALFUZZ_ROOT}/docker/demo/scripts/*.sh \
    && ln -sf ${RALFUZZ_ROOT}/docker/demo/scripts/entrypoint.sh /usr/local/bin/ralfuzz-demo

# Pin external cJSON target input at image build time (no runtime download needed).
RUN bash ${RALFUZZ_ROOT}/pipeline/fetch_cjson.sh

RUN clang-18 --version | head -n 1 > ${RALFUZZ_ROOT}/docker/demo/.toolchain-pin \
    && python3.11 --version >> ${RALFUZZ_ROOT}/docker/demo/.toolchain-pin \
    && echo "cJSON=${CJSON_VERSION}" >> ${RALFUZZ_ROOT}/docker/demo/.toolchain-pin

ENTRYPOINT ["/usr/local/bin/ralfuzz-demo"]
CMD ["inspect-frozen"]
