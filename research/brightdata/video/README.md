# Bright Data narrated explainer

The current implementation uses Remotion. `editorial.json` contains reviewed narration, semantic targets, source-word anchors and visual recap instructions. `remotion/src/timeline.json` is generated from actual speech timestamps and supplies captions, emphasis, chapter navigation and the text version. The original architecture diagram and 20 dependency relationships remain part of the film.

## Reproduce

Production requires the existing local report inputs listed in `content-bindings.json`. They remain outside Git under `research/brightdata/`; verify them with `uv run --frozen python research/brightdata/video/check_content.py` from the repository root. Missing or changed inputs are an error, not a reason to use an older report.

Requires Python 3.10+, NumPy, ffmpeg/ffprobe, Node.js and Google Chrome. Install Node dependencies with `npm ci` in `remotion`.

1. Configure `DOUBAO_TTS_API_KEY`, `DOUBAO_TTS_RESOURCE_ID=seed-tts-2.0` and `DOUBAO_TTS_VOICE_TYPE` in environment variables; the existing private `~/.env` is also supported. Credentials are never production assets. `editorial.json` selects the production voice and delivery direction.
2. Run `uv run --frozen python synthesize_current.py` from this directory only when sending this narration to Doubao is authorized. Chapters are cached by text, voice, speaking rate and direction. This is a paid external service; local rendering does not need service access once audio exists.
3. Run `uv run --frozen --with mlx-whisper python recognize_current.py` (Apple Silicon; `--model` accepts a cached model directory), then `uv run --frozen --with numpy --with opencc-python-reimplemented==0.1.7 --with pypinyin==0.55.0 python build_remotion_timeline.py`. Independent recognition supplies caption and focus timing as well as words on both sides of each scene boundary; waveform analysis must also find a sustained quiet interval. Provider word timestamps alone are insufficient for cutting speech, especially around English names. Recognition is hash-bound to the audio, and never replaces authored captions. Speech remains at its synthesized speed; orientation and stable recap intervals supply thinking time. A recap retains one focal view while its progress cue advances; it does not cycle through a list of targets.
4. In `remotion`, run `node build_reel.mjs`, `npm run check` and `node review.mjs`. Inspect the resulting focus images. The sparse rendering map includes every animated recap frame, not only speech transitions.
5. Run `node render.mjs`, then `uv run --frozen python assemble.py` and `uv run --frozen python verify_render.py`. The reel composition must retain the full film duration because Remotion clamps nested frame clocks. Encoded frames are compared with the source composition.
6. Run `uv run --frozen --with opencc-python-reimplemented==0.1.7 --with pypinyin==0.55.0 python -m unittest discover -s ../tests -p 'test_*alignment.py'` and the timeline tests. When updating the existing report bundle, copy the reviewed output to `../assets/video/brightdata.mp4`, regenerate the poster, then run `build_watch_page.py`, `update_report_entry.py` and `verify_video.py`. Report builders require the existing full report bundle; they deliberately preserve its other chapters.
7. Run `node playback-test.mjs` against the existing local preview on port 8769. Check actual chapter seeking, playback and mobile layout. Publish only the selected report bundle through the existing Artifact Site; do not publish source, credentials, caches or private review files.

## Revision and review

Read the project `video-gen` skill before editorial changes. `content-contract.json` defines retained ideas and figures. A voice change rebuilds timing from the new audio; copying old timestamps or stretching the previous recording is not a substitute. A new renderer does not justify rewriting the argument.

`review.mjs` checks visible layout and captures semantic focus; `verify_render.py` detects export/assembly drift. These are technical and visual checks, not a claim that a human listened through the film. Inspect narration and picture together wherever synchronized audiovisual review is available, and describe review scope accurately.

Generated audio, videos, screenshots, alignment responses and historical renderers are intentionally excluded from Git. The pull request includes the current production code, path/hash bindings and regression tests. Report prose, illustrations, generated timelines and audio remain outside Git. CI uses an explicit synthetic compilation fixture and never treats it as a film; the two production-content integration tests additionally run against the local publication inputs.
