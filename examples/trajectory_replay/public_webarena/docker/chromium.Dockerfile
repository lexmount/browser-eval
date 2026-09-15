FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

LABEL lex.browser-use-eval.debian-mirror="${DEBIAN_MIRROR}" \
      lex.browser-use-eval.debian-security-mirror="${DEBIAN_SECURITY_MIRROR}"

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates chromium fonts-liberation fonts-noto-cjk socat \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 browser

COPY examples/trajectory_replay/public_webarena/docker/start-chromium.sh /usr/local/bin/start-browser
RUN chmod 0755 /usr/local/bin/start-browser

USER browser
EXPOSE 9223
ENTRYPOINT ["/usr/local/bin/start-browser"]
