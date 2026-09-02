# Preserve the baseline OS, fonts, dependencies and entrypoint; replace only Moli.
ARG MOLI_BASE_IMAGE=lex-browser-use-eval-moli:1.0.6
FROM ${MOLI_BASE_IMAGE}
ARG TARGETARCH
ARG MOLI_VERSION=v1.1.1
ARG MOLI_AMD64_SHA256=7b3eb9cbbf2cc8bd5ea9ef4a5bdb24cee2df35d26da621216a8b69c2aff3ebaa
ARG MOLI_ARM64_SHA256=549484765476b8dd3fd93ebf59a089e4424425a961c14874974a88bba6d8b5b4
USER root
RUN case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
         amd64) asset="moli-x86_64-unknown-linux-gnu.tar.gz"; sha="$MOLI_AMD64_SHA256" ;; \
         arm64) asset="moli-aarch64-unknown-linux-gnu.tar.gz"; sha="$MOLI_ARM64_SHA256" ;; \
         *) echo "unsupported TARGETARCH=$TARGETARCH" >&2; exit 2 ;; \
       esac \
    && curl --proto '=https' --tlsv1.2 -fsSL \
         "https://github.com/lexmount/moli/releases/download/${MOLI_VERSION}/${asset}" \
         -o /tmp/moli-upgrade.tar.gz \
    && printf '%s  %s\n' "$sha" /tmp/moli-upgrade.tar.gz | sha256sum -c - \
    && mkdir /tmp/moli-upgrade \
    && tar -xzf /tmp/moli-upgrade.tar.gz -C /tmp/moli-upgrade \
    && find /tmp/moli-upgrade -type f -name moli -exec install -m 0755 {} /usr/local/bin/moli \; \
    && moli --version | grep -Fx "moli ${MOLI_VERSION#v}" \
    && rm -rf /tmp/moli-upgrade.tar.gz /tmp/moli-upgrade
USER browser
