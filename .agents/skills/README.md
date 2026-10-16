# Skill sources and updates

`benchmark-report`, `benchmark-editorial` and `video-gen` are maintained by this project. `video-gen` provides guidance for scripts, storyboards and video production, with an optional timeline consistency guard; individual films and their production projects live outside the repository. `humanizer` and `humanizer-chinese` are vendored dependencies:

| Skill | Upstream | Installed source |
| --- | --- | --- |
| Humanizer | [blader/humanizer](https://github.com/blader/humanizer) | [Commit, version and hashes](humanizer/SOURCE.json) |
| Humanizer Chinese | [jiji262/humanizer-chinese](https://github.com/jiji262/humanizer-chinese) | [Commit, version and hashes](humanizer-chinese/SOURCE.json) |

Keep upstream `SKILL.md` and `LICENSE` bytes unchanged. `SOURCE.json` is project-maintained provenance and is not an upstream file. Put project-specific language, evidence and report requirements in `AGENTS.md` or the project-owned benchmark skills. Propose general improvements upstream rather than maintaining a private fork inside a vendored skill.

To update either dependency:

1. Resolve the upstream default branch to a full commit ID, inspect its changes and license, and download `SKILL.md` and `LICENSE` from that same commit.
2. Replace the two files without translating, reformatting or adding project instructions. Include any newly required upstream support files in the provenance inventory.
3. Update `SOURCE.json` with the repository URL, ref, commit, upstream version, retrieval date, license and SHA-256 of every copied file. Link to the pinned commit when reviewing the change. An unchanged upstream HEAD needs no invented version bump.
4. Run `uv run --frozen --extra test python -m pytest tests/test_skill_sources.py -q` and verify the copied bytes against the downloaded upstream files. The offline test detects local drift; the upstream comparison establishes origin.

Never execute downloaded upstream scripts as part of a skill update. Read the updated skill before applying it. Preserve copyright notices and check project integration for changed instructions.
