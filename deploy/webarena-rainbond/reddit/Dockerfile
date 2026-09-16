FROM am1n3e/webarena-verified-reddit@sha256:0594908059a03e5f610005689440a95c05e776e734006197c7b1eba12734cb46

COPY entrypoint-wrapper.sh /usr/local/bin/webarena-rainbond-entrypoint
RUN chmod 0755 /usr/local/bin/webarena-rainbond-entrypoint

ENV WA_ORIGINAL_ENTRYPOINT=/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/webarena-rainbond-entrypoint"]
CMD ["supervisord", "-n", "-j", "/supervisord.pid"]
