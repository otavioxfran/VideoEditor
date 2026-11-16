import json
import os
from pathlib import Path
from types import SimpleNamespace

from video_editor.export import renderer

SNAPSHOT = Path(__file__).parent / 'fixtures' / 'export_snapshot' / 'single.json'


def test_export_ffmpeg_arguments_characterization(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x')
    output = tmp_path / 'out.mp4'
    snapshot = dict(
        source=dict(path=str(source)), protected_sources=[],
        media=dict(width=1920, height=1080, fps=30, origin=0, timing_contract=2, tail_padding=0),
        timeline=dict(keeps=[dict(source_start=1, source_end=2, output_start=0, output_end=1)], duration=1),
        transcript=dict(segments=[]),
        config=dict(renderer.DEFAULT_CONFIG, video_codec='h264_nvenc', hardware_decode=False),
    )
    calls = []

    def execute(arguments):
        calls.append([arg.replace(str(tmp_path), '<tmp>') for arg in arguments])
        Path(arguments[-1]).write_bytes(b'x')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(renderer, '_execute', execute)
    monkeypatch.setattr(renderer.media, 'probe', lambda path: dict(
        duration=1, width=1920, height=1080, fps=30
    ))
    renderer.render(snapshot, output, tmp_path)
    path = SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(calls, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if os.getenv('UPDATE_EXPORT_SNAPSHOT') == '1':
        path.write_text(encoded, encoding='utf-8')
    else:
        assert calls == json.loads(path.read_text(encoding='utf-8'))
