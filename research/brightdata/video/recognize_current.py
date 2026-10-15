"""Independently locate spoken scene boundaries; never replace authored captions."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT = Path(__file__).parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='mlx-community/whisper-small-mlx')
    args = parser.parse_args()
    import mlx_whisper
    for audio in sorted((ROOT / 'audio/current').glob('chapter-*.mp3')):
        output = audio.with_suffix('.recognition.json')
        digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        if output.exists() and json.loads(output.read_text()).get('audioSha256') == digest:
            continue
        result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=args.model,
                                      language='zh', word_timestamps=True, verbose=False)
        result['audioSha256'] = digest
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print('Recognized', audio.name, flush=True)

if __name__ == '__main__':
    main()
