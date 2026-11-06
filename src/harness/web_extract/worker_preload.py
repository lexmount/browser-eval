"""Import native SDKs once without opening clients, browsers or request sessions."""

import hashlib
import os
from pathlib import Path

# Extraction uses remote models. BLAS startup must not create host-sized thread
# pools inside a container with a much smaller CPU quota.
for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(name, "1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import crawl4ai.extraction_strategy  # noqa: E402,F401
import scrapegraphai.graphs  # noqa: E402,F401
import stagehand  # noqa: E402,F401

if len(os.listdir("/proc/self/task")) != 1:
    raise RuntimeError("Forkserver preload requires one native thread; check BLAS thread limits")

from harness.web_extract.self_hosted import installed_environment_sha256  # noqa: E402

PRELOAD_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
WORKER_SHA256 = hashlib.sha256(Path(__file__).with_name("self_hosted.py").read_bytes()).hexdigest()
ENVIRONMENT_SHA256 = installed_environment_sha256()
