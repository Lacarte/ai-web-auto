"""faster-whisper child process (run with the whisper environment's Python, like on-device does).

    python whisper_worker.py <media> <model> <language|auto> -> JSON on stdout: {language, duration, words:[{t, w}]}
"""
import json
import sys

from faster_whisper import WhisperModel

media, model_size = sys.argv[1], sys.argv[2]
language = None if len(sys.argv) < 4 or sys.argv[3] == "auto" else sys.argv[3]
model = WhisperModel(model_size, device="cpu", compute_type="int8")
segments, info = model.transcribe(media, language=language, beam_size=5, word_timestamps=True, vad_filter=False)
words = [{"t": round(w.start, 2), "w": w.word.strip()} for s in segments for w in (s.words or []) if w.word.strip()]
print(json.dumps({"language": info.language, "duration": round(info.duration, 2), "words": words}))
