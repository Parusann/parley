# The dictation history gains an optional latency_ms field. Old entries
# without it must keep loading fine (the file is append-only across versions).

import json

from parley import transcript_log


def test_latency_recorded_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    transcript_log.record_transcript('hello world', app='code', duration_s=1.2,
                                     latency_ms=142)
    entry = transcript_log.load_transcripts()[0]
    assert entry['latency_ms'] == 142


def test_latency_absent_when_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    transcript_log.record_transcript('hello', app='', duration_s=0.5)
    entry = transcript_log.load_transcripts()[0]
    assert 'latency_ms' not in entry


def test_old_entries_without_latency_still_load(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    (tmp_path / 't.jsonl').write_text(
        json.dumps({'timestamp': '2026-01-01T00:00:00', 'text': 'old', 'app': '',
                    'duration_s': 1.0, 'chars': 3}) + '\n', encoding='utf-8')
    transcript_log.record_transcript('new', latency_ms=99)
    texts = [e['text'] for e in transcript_log.load_transcripts()]
    assert texts == ['new', 'old']
