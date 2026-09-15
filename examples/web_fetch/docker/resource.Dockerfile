FROM ghcr.io/astral-sh/uv:0.7.8@sha256:0178a92d156b6f6dbe60e3b52b33b421021f46d634aa9f81f42b91445bb81cdf AS uv
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS node
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS downloads

ARG TARGETARCH
ARG LIGHTPANDA_URL_ARM64=https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-aarch64-linux
ARG LIGHTPANDA_SHA_ARM64=f199d9f288fb804db137f19a06c06a7b49c3021031a15dfc2ebd1300b3daa810
ARG LIGHTPANDA_URL_AMD64=https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux
ARG LIGHTPANDA_SHA_AMD64=4200e6dd7b7dd4ab60224c83f5d5f3688e7fd8cfe9389155f3bc5d64cfb92d1b
ARG OBSCURA_VERSION=v0.2.1
ARG OBSCURA_SHA_ARM64=0297c26d583f598f0126a7271cc40750598a9a9cbd86d1d6f79b2b99097d5244
ARG OBSCURA_SHA_AMD64=6a1a66b3f1ab118fa7d31330894a868617aea68c06d75436d851356c39df1ed3
ARG MOLI_1_1_1_SHA_ARM64=549484765476b8dd3fd93ebf59a089e4424425a961c14874974a88bba6d8b5b4
ARG MOLI_1_1_1_SHA_AMD64=7b3eb9cbbf2cc8bd5ea9ef4a5bdb24cee2df35d26da621216a8b69c2aff3ebaa

COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates chromium curl fonts-liberation fonts-noto-cjk \
       libcurl4 libgcc-s1 libstdc++6 nodejs npm python3 python3-venv tar \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 evaluator

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends aria2 \
    && rm -rf /var/lib/apt/lists/*

RUN target_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$target_arch" in \
      arm64) url="$LIGHTPANDA_URL_ARM64"; sha="$LIGHTPANDA_SHA_ARM64" ;; \
      amd64) url="$LIGHTPANDA_URL_AMD64"; sha="$LIGHTPANDA_SHA_AMD64" ;; \
      *) echo "unsupported TARGETARCH=$target_arch" >&2; exit 2 ;; \
    esac \
    && aria2c --allow-overwrite=true --check-certificate=true --console-log-level=warn \
       --max-connection-per-server=8 --split=8 --min-split-size=1M \
       --dir=/usr/local/bin --out=lightpanda "$url" \
    && printf '%s  %s\n' "$sha" /usr/local/bin/lightpanda | sha256sum -c - \
    && chmod 0755 /usr/local/bin/lightpanda

RUN target_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$target_arch" in \
      arm64) archive_arch=aarch64; sha="$OBSCURA_SHA_ARM64" ;; \
      amd64) archive_arch=x86_64; sha="$OBSCURA_SHA_AMD64" ;; \
      *) echo "unsupported TARGETARCH=$target_arch" >&2; exit 2 ;; \
    esac \
    && mkdir -p /opt/obscura \
    && aria2c --allow-overwrite=true --check-certificate=true --console-log-level=warn \
       --max-connection-per-server=8 --split=8 --min-split-size=1M \
       --dir=/tmp --out=obscura.tar.gz \
       "https://github.com/h4ckf0r0day/obscura/releases/download/${OBSCURA_VERSION}/obscura-${archive_arch}-linux.tar.gz" \
    && printf '%s  %s\n' "$sha" /tmp/obscura.tar.gz | sha256sum -c - \
    && tar -xzf /tmp/obscura.tar.gz -C /opt/obscura \
    && find /opt/obscura -type f -name obscura -exec ln -s '{}' /usr/local/bin/obscura \; \
    && test -x /usr/local/bin/obscura \
    && rm /tmp/obscura.tar.gz

RUN target_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$target_arch" in \
      arm64) archive_arch=aarch64; sha="$MOLI_1_1_1_SHA_ARM64" ;; \
      amd64) archive_arch=x86_64; sha="$MOLI_1_1_1_SHA_AMD64" ;; \
      *) echo "unsupported TARGETARCH=$target_arch" >&2; exit 2 ;; \
    esac \
    && aria2c --allow-overwrite=true --check-certificate=true --console-log-level=warn \
       --max-connection-per-server=8 --split=8 --min-split-size=1M \
       --dir=/tmp --out=moli-1.1.1.tar.gz \
       "https://github.com/lexmount/moli/releases/download/v1.1.1/moli-${archive_arch}-unknown-linux-gnu.tar.gz" \
    && printf '%s  %s\n' "$sha" /tmp/moli-1.1.1.tar.gz | sha256sum -c - \
    && mkdir -p /tmp/moli-1.1.1 \
    && tar -xzf /tmp/moli-1.1.1.tar.gz -C /tmp/moli-1.1.1 \
    && find /tmp/moli-1.1.1 -type f -name moli -exec install -m 0755 '{}' /usr/local/bin/moli-1.1.1 \; \
    && test -x /usr/local/bin/moli-1.1.1 \
    && rm -rf /tmp/moli-*

FROM python:3.11-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security
COPY --from=node /usr/local /usr/local
COPY --from=uv /uv /usr/local/bin/uv
COPY examples/web_fetch/docker/download_apt_uris.py /usr/local/bin/download_apt_uris.py
RUN sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
       /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && packages="ca-certificates chromium curl fonts-liberation fonts-noto-cjk libcurl4 libgcc-s1 libstdc++6 tar" \
    && DEBIAN_FRONTEND=noninteractive apt-get --print-uris --yes --no-install-recommends install $packages > /tmp/apt-uris.txt \
    && python /usr/local/bin/download_apt_uris.py /tmp/apt-uris.txt /var/cache/apt/archives \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-download --no-install-recommends $packages \
    && rm -rf /var/lib/apt/lists/* \
    && rm /tmp/apt-uris.txt /usr/local/bin/download_apt_uris.py \
    && useradd --create-home --uid 10001 evaluator
COPY --from=downloads /usr/local/bin/lightpanda /usr/local/bin/lightpanda
COPY --from=downloads /opt/obscura /opt/obscura
COPY --from=downloads /usr/local/bin/moli-1.1.1 /usr/local/bin/moli-1.1.1
RUN find /opt/obscura -type f -name obscura -exec ln -s '{}' /usr/local/bin/obscura \;

WORKDIR /workspace
COPY pyproject.toml uv.lock package.json package-lock.json ./
RUN uv sync --frozen --extra headless-eval --no-install-project && npm ci
COPY harness ./harness
COPY scripts ./scripts
RUN uv sync --frozen --extra headless-eval \
    && chown -R evaluator:evaluator /workspace

ENV HOME=/home/evaluator
USER evaluator
CMD ["sleep", "infinity"]
