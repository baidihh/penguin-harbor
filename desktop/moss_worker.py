"""Keep the local ONNX models loaded in an isolated Python process."""

import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    if len(sys.argv) == 4 and sys.argv[2] == '--prepare-reference':
        from desktop.moss_engine import prepare_voice
        prepare_voice(sys.argv[1], sys.argv[3])
        print(json.dumps({'prepared': True}), flush=True)
        return
    engine = None
    backend = ''
    print(json.dumps({'ready': True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            text = request['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ValueError('请提供一段有效的短句。')
            voice = request.get('voice', 'moss_wanwan')
            requested_backend = 'kokoro' if voice.startswith('kokoro_') else 'moss'
            if requested_backend != backend:
                engine = None
                backend = ''
                if requested_backend == 'kokoro':
                    from desktop.kokoro_engine import KokoroEngine
                    engine = KokoroEngine(sys.argv[1])
                else:
                    from desktop.moss_engine import MossEngine
                    engine = MossEngine(sys.argv[1])
                backend = requested_backend
            audio, stats = engine.synthesize(text, voice) if backend == 'kokoro' else engine.synthesize(text)
            Path(request['output']).write_bytes(audio)
            print(json.dumps({'ok': True, **stats}), flush=True)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
