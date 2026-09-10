FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG TARGETARCH
ARG MOLI_VERSION=v1.0.6
ARG MOLI_AMD64_SHA256=8c265be9ae49d77b6f86d38fadebe8da6e6d4dd1e5f7894c1819dba737721b91
ARG MOLI_ARM64_SHA256=c64e7cfdb7ee314b0bf9eeaafcec128020a64a4cea592f576dcb8f9c12b3c2f5
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

LABEL lex.browser-use-eval.debian-mirror="${DEBIAN_MIRROR}" \
      lex.browser-use-eval.debian-security-mirror="${DEBIAN_SECURITY_MIRROR}"

RUN --mount=type=bind,source=.,target=/mnt/build-context,ro \
    sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl fontconfig fonts-liberation fonts-noto-cjk \
        libcurl4 libgcc-s1 libstdc++6 tar \
    && case "$TARGETARCH" in \
         amd64) asset="moli-x86_64-unknown-linux-gnu.tar.gz"; sha="$MOLI_AMD64_SHA256" ;; \
         arm64) asset="moli-aarch64-unknown-linux-gnu.tar.gz"; sha="$MOLI_ARM64_SHA256" ;; \
         *) echo "unsupported TARGETARCH=$TARGETARCH" >&2; exit 2 ;; \
       esac \
    && prefetched="/mnt/build-context/${asset%.tar.gz}-${MOLI_VERSION}.tar.gz" \
    && if [ -f "$prefetched" ]; then \
         cp "$prefetched" /tmp/moli.tar.gz; \
       else \
         curl --proto '=https' --tlsv1.2 -fsSL \
           "https://github.com/lexmount/moli/releases/download/${MOLI_VERSION}/${asset}" \
           -o /tmp/moli.tar.gz; \
       fi \
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
