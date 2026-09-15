FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG TARGETARCH
ARG MOLI_VERSION=v1.1.3
ARG MOLI_AMD64_SHA256=e1f923f7fb06a684721e24a6c65c20567eaf81388a2225d1a9a175fc6c305df3
ARG MOLI_ARM64_SHA256=f65ce90c38fdfeaa73b7e1d491823ec6d19371e5b74dbe35b43c8d61f0a8f6b7

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl fontconfig fonts-liberation fonts-noto-cjk \
        libcurl4 libgcc-s1 libstdc++6 tar \
    && resolved_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$resolved_arch" in \
         amd64) asset="moli-x86_64-unknown-linux-gnu.tar.gz"; sha="$MOLI_AMD64_SHA256" ;; \
         arm64) asset="moli-aarch64-unknown-linux-gnu.tar.gz"; sha="$MOLI_ARM64_SHA256" ;; \
         *) echo "unsupported architecture=$resolved_arch" >&2; exit 2 ;; \
       esac \
    && curl --proto '=https' --tlsv1.2 -fsSL \
         "https://github.com/lexmount/moli/releases/download/${MOLI_VERSION}/${asset}" \
         -o /tmp/moli.tar.gz \
    && printf '%s  %s\n' "$sha" /tmp/moli.tar.gz | sha256sum -c - \
    && tar -xzf /tmp/moli.tar.gz -C /tmp \
    && find /tmp -type f -name moli -exec install -m 0755 {} /usr/local/bin/moli \; \
    && test -x /usr/local/bin/moli \
    && rm -rf /tmp/moli.tar.gz /tmp/moli-* \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 browser

USER browser
EXPOSE 9222
ENTRYPOINT ["moli", "serve", "--host", "0.0.0.0", "--port", "9222", "--layout", "--profile-dir", "/tmp/moli-profile", "--log-level", "warn"]
