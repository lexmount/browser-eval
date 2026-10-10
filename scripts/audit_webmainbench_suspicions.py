"""Flag frozen WebMainBench evidence anomalies without changing scores."""

from harness.web_fetch.webmainbench_diagnostics import suspicion_main


if __name__ == "__main__":
    suspicion_main()
