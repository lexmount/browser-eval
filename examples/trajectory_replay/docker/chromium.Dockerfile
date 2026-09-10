FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates chromium fonts-liberation fonts-noto-cjk socat \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 browser

COPY examples/trajectory_replay/docker/start-chromium.sh /usr/local/bin/start-browser
RUN chmod 0755 /usr/local/bin/start-browser

USER browser
EXPOSE 9223
ENTRYPOINT ["/usr/local/bin/start-browser"]
