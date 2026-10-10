"""Authorize Artifact Site and save its token in the user-scoped credential file."""
from harness.web_fetch.artifact_auth import authorize_main


if __name__ == "__main__":
    authorize_main()
