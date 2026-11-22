import json
from pathlib import Path

import pytest

from tests.test_finishing_audio import _speech
from video_editor.finishing import audio

pytestmark = pytest.mark.slow
SNAPSHOT = Path(__file__).parent / 'fixtures' / 'audio_analysis_snapshot.json'


def test_analyze_matches_numeric_snapshot(tmp_path):
    expected = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    actual = audio.analyze(_speech(tmp_path / 'speech.wav', duration=3))
    for key in ('integrated_lufs', 'true_peak_dbtp', 'lra_lu', 'peak_dbfs', 'rms_dbfs'):
        assert actual[key] == pytest.approx(expected[key], abs=.2)
    assert actual['clipped_samples'] == expected['clipped_samples']
    for key in ('low_dbfs', 'mid_dbfs', 'high_dbfs', 'sibilance_dbfs'):
        assert actual['bands'][key] == pytest.approx(expected['bands'][key], abs=.2)
