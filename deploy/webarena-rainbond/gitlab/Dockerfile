FROM am1n3e/webarena-verified-gitlab@sha256:673ba94d757c5b3f4bb55751ad8b745a33bad2b987349322403cbc41035a92e0

COPY entrypoint-wrapper.sh /usr/local/bin/webarena-rainbond-entrypoint
RUN chmod 0755 /usr/local/bin/webarena-rainbond-entrypoint

ENV WA_ORIGINAL_ENTRYPOINT=/opt/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/webarena-rainbond-entrypoint"]
