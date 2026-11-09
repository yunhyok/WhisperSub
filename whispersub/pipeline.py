"""Core pipeline: decode audio (PyAV) → faster-whisper → filter → SRT / TSV → optional LLM translation."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

log = logging.getLogger("whispersub.pipeline")

SAMPLE_RATE = 16000
MEDIA_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg",
              ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

MODEL_CHOICES = ["large-v3", "large-v3-turbo", "medium", "small"]
LANG_CHOICES = [("ja", "일본어 (ja)"), ("en", "영어 (en)"), ("ko", "한국어 (ko)"), ("zh", "중국어 (zh)"),
                ("auto", "자동 감지")]


class Cancelled(Exception):
    pass


@dataclass
class Settings:
    model: str = "large-v3"
    device: str = "auto"            # auto | cuda | cpu
    language: str = "ja"            # whisper language code or "auto"
    fast: bool = False              # beam 2 / no temperature fallback / stricter VAD
    max_dur: float = 10.0           # max seconds a subtitle line stays on screen
    overwrite: bool = False         # overwrite existing *.srt instead of writing *.whisper.srt
    write_tsv: bool = True          # write <name>.translate.tsv for manual translation
    # translation via OpenAI-compatible chat API (LM Studio / Ollama / OpenAI ...)
    translate: bool = False
    llm_url: str = "http://localhost:1234/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    target_lang: str = "ko"
    llm_batch: int = 40

    @staticmethod
    def from_dict(d: dict) -> "Settings":
        s = Settings()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, type(getattr(s, k))(v) if not isinstance(getattr(s, k), bool) else str(v).lower() in ("1", "true", "yes"))
        return s

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- audio
def decode_audio(path: str, start: Optional[float] = None, end: Optional[float] = None,
                 progress: Optional[Callable[[float], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> np.ndarray:
    """Decode any media file to float32 mono 16 kHz with PyAV (bundled FFmpeg libs; no ffmpeg.exe needed)."""
    import av
    chunks = []
    with av.open(path, mode="r") as container:
        if not container.streams.audio:
            raise RuntimeError("오디오 스트림이 없습니다")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        duration = (container.duration or 0) / av.time_base if container.duration else None
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        if start:
            container.seek(int(start * av.time_base), backward=True, any_frame=False)
        last_report = 0.0
        for frame in container.decode(stream):
            t = float(frame.pts * stream.time_base) if frame.pts is not None else None
            if start and t is not None and t < start - 0.5:
                continue
            if end and t is not None and t > end:
                break
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
            if progress and t is not None and duration and t - last_report > 5.0:
                last_report = t
                progress(min(1.0, (t - (start or 0)) / ((end or duration) - (start or 0) or 1)))
            if cancel and cancel():
                raise Cancelled()
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0
    if start:
        # trim samples decoded before `start` (seek lands on an earlier keyframe)
        first_t = None
        with av.open(path) as c2:
            c2.seek(int(start * av.time_base), backward=True, any_frame=False)
            for fr in c2.decode(c2.streams.audio[0]):
                first_t = float(fr.pts * c2.streams.audio[0].time_base) if fr.pts is not None else start
                break
        if first_t is not None and first_t < start:
            cut = int((start - first_t) * SAMPLE_RATE)
            audio = audio[cut:] if cut < len(audio) else audio[:0]
    return audio


# --------------------------------------------------------------------------- model
def hf_repo_for(model: str) -> str:
    try:
        from faster_whisper.utils import _MODELS
        return _MODELS.get(model, model)
    except Exception:
        return model


def model_cache_dir(model: str) -> Optional[Path]:
    repo = hf_repo_for(model)
    if "/" not in repo:
        return None
    base = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface") )
    if "HF_HOME" in os.environ and "HF_HUB_CACHE" not in os.environ:
        base = base / "hub"
    elif "HF_HUB_CACHE" not in os.environ:
        base = base / "hub"
    return base / ("models--" + repo.replace("/", "--"))


def model_is_cached(model: str) -> bool:
    d = model_cache_dir(model)
    if not d or not d.exists():
        return False
    snaps = d / "snapshots"
    return snaps.exists() and any((s / "model.bin").exists() for s in snaps.iterdir())


def model_cache_bytes(model: str) -> int:
    d = model_cache_dir(model)
    if not d or not d.exists():
        return 0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


class Transcriber:
    """Holds one loaded WhisperModel; reloads only when model/device changes."""

    def __init__(self):
        self.model = None
        self.key = None
        self.device = None

    def load(self, model_name: str, device: str, logger: Callable[[str], None]):
        key = (model_name, device)
        if self.model is not None and self.key == key:
            return self.device
        from faster_whisper import WhisperModel
        self.model = None
        order = {"auto": [("cuda", "float16"), ("cpu", "int8")],
                 "cuda": [("cuda", "float16")],
                 "cpu": [("cpu", "int8")]}[device]
        last_err = None
        for dev, ct in order:
            try:
                logger(f"모델 로드: {model_name} ({dev}/{ct})")
                kw = dict(device=dev, compute_type=ct)
                if dev == "cpu":
                    kw["cpu_threads"] = max(4, (os.cpu_count() or 8) // 2)
                self.model = WhisperModel(model_name, **kw)
                self.key, self.device = key, dev
                return dev
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger(f"  {dev} 로드 실패: {type(e).__name__}: {str(e)[:200]}")
        raise RuntimeError(f"모델을 로드할 수 없습니다: {last_err}")

    def transcribe(self, audio: np.ndarray, language: str, fast: bool, offset: float,
                   progress: Optional[Callable[[float], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None,
                   logger: Callable[[str], None] = log.info) -> list[dict]:
        params = dict(task="transcribe", vad_filter=True, condition_on_previous_text=False,
                      word_timestamps=False, no_speech_threshold=0.6, log_prob_threshold=-1.0,
                      compression_ratio_threshold=2.4)
        if language and language != "auto":
            params["language"] = language
        if fast:
            params.update(beam_size=2, best_of=1, temperature=[0.0],
                          vad_parameters=dict(min_silence_duration_ms=800, speech_pad_ms=200, threshold=0.6))
        else:
            params.update(beam_size=5, best_of=5, temperature=[0.0, 0.2],
                          vad_parameters=dict(min_silence_duration_ms=600, speech_pad_ms=300, threshold=0.55))
        segments, info = self.model.transcribe(audio, **params)
        total = max(info.duration, 1e-6)
        logger(f"오디오 {info.duration/60:.1f}분, VAD 후 음성 {info.duration_after_vad/60:.1f}분"
               + (f", 감지 언어 {info.language} ({info.language_probability:.2f})" if language == "auto" else ""))
        out, t0 = [], time.time()
        for s in segments:
            if cancel and cancel():
                raise Cancelled()
            out.append({"start": round(s.start + offset, 3), "end": round(s.end + offset, 3),
                        "text": s.text.strip(), "lp": round(s.avg_logprob, 3),
                        "nsp": round(s.no_speech_prob, 3), "cr": round(s.compression_ratio, 3)})
            if progress:
                progress(min(1.0, s.end / total))
            if len(out) % 50 == 0:
                logger(f"  {len(out)} seg · {s.end/60:.1f}/{total/60:.1f}분 · {time.time()-t0:.0f}s")
        logger(f"전사 완료: {len(out)} seg, {time.time()-t0:.0f}s")
        return out


# --------------------------------------------------------------------------- filter
PREFIX = re.compile(r"^(佐久間|岩﨑|岩崎|スタッフ：|ナレーション：)\s*")  # fake speaker names Whisper sometimes emits
HALLU = re.compile(r"(ご視聴|ご覧いただき|チャンネル登録|高評価|字幕|翻訳|最後まで|次回|サブタイトル|提供|www\.|http|"
                   r"Thank you for watching|Subtitles by|Amara\.org)")
NOISE = re.compile(r"^[\s「」『』、。,.!?！？…・〜～ーｰ~\-ぁぃぅぇぉあいうえおんっゃゅょはへほふひやよわを"
                   r"アイウエオンッャュョハヒフヘホヤヨワヲahmou]*$", re.IGNORECASE)
FILLER = {"うん", "はい", "ねえ", "ねぇ", "ね", "え", "えっ", "いや", "そう", "じゃあ", "ちょっと", "あの", "なに", "何",
          "はーい", "おお", "うーん", "まあ", "で", "だね", "ほんと", "本当", "本当に", "マジ", "マジで", "よし", "うわ",
          "うわー", "やばい", "やばいやばい", "危ない", "危ない危ない", "ふふ", "はは", "ははは", "あはは", "ん", "んん",
          "あー", "あ", "お", "おー", "へー", "ほら", "ほい", "よいしょ", "せーの", "いえーい", "イエーイ", "ありがとう",
          "ありがとうございます", "ありがと", "すごい", "すごーい", "気持ちいい", "イク", "いく", "いくいく", "だめ", "ダメ",
          "やだ", "やだやだ", "もう", "ほんとに", "ほんとだ", "笑", "(笑)", "にゃー",
          "yeah", "yes", "no", "oh", "ah", "um", "uh", "hmm", "okay", "ok", "wow"}


def _norm(t: str) -> str:
    return re.sub(r"[「」『』。、,.!?！？…・\s〜～ーｰ~]", "", t).lower()


_FILLER_N = {_norm(x) for x in FILLER}


def filter_segments(segs: list[dict], logger: Callable[[str], None] = log.info) -> list[dict]:
    out = []
    for s in segs:
        t = PREFIX.sub("", s["text"].strip()).strip()
        n = _norm(t)
        reason = None
        if not n:
            reason = "empty"
        elif NOISE.match(t):
            reason = "noise"
        elif n in _FILLER_N:
            reason = "filler"
        elif HALLU.search(t):
            reason = "hallucination"
        elif s["nsp"] > 0.6 and s["lp"] < -1.0:
            reason = "no-speech"
        elif s["cr"] > 2.4:
            reason = "repeat"
        elif re.fullmatch(r"(.{1,8}?)\1{2,}", n):
            reason = "intra-repeat"
        d = dict(s)
        d["text"], d["drop"] = t, reason
        out.append(d)
    merged: list[dict] = []
    for s in out:
        if merged and _norm(merged[-1]["text"]) == _norm(s["text"]) and merged[-1]["drop"] == s["drop"]:
            merged[-1]["end"] = s["end"]
            merged[-1]["count"] += 1
        else:
            s["count"] = 1
            merged.append(s)
    for s in merged:
        if s["count"] >= 4 and not s["drop"]:
            s["drop"] = "loop"
    for i, s in enumerate(merged):
        s["id"] = i
    kept = sum(1 for s in merged if not s["drop"])
    logger(f"필터: {len(segs)} → 병합 {len(merged)} → 유지 {kept} · 제거 {dict(Counter(s['drop'] for s in merged if s['drop']))}")
    return merged


# --------------------------------------------------------------------------- output
def fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, items: Iterable[dict], max_dur: float) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for e in items:
            n += 1
            end = min(max(e["end"], e["start"] + 0.8), e["start"] + max_dur)
            f.write(f"{n}\n{fmt_ts(e['start'])} --> {fmt_ts(end)}\n{e['text']}\n\n")
    return n


def output_path(video: Path, suffix: str, overwrite: bool) -> Path:
    """<video stem>.<suffix>.srt; if it already exists and overwrite is off, use <stem>.<suffix>.whisper.srt"""
    p = video.with_name(f"{video.stem}.{suffix}.srt")
    if p.exists() and not overwrite:
        p = video.with_name(f"{video.stem}.{suffix}.whisper.srt")
    return p


def merge_translated(merged: list[dict], ko: dict) -> list[dict]:
    items: list[dict] = []
    for s in merged:
        if s["drop"]:
            continue
        t = ko.get(s["id"], "")
        if not t or t == "-":
            continue
        if items and items[-1]["text"] == t and s["start"] - items[-1]["end"] < 3.0:
            items[-1]["end"] = s["end"]
        else:
            items.append({"start": s["start"], "end": s["end"], "text": t})
    return items


def read_translation_tsv(path: Path) -> dict:
    """Accepts 'id<TAB>text' or the generated 'id<TAB>start<TAB>end<TAB>src<TAB>translation'. '||' = line break."""
    ko = {}
    for line in open(path, encoding="utf-8-sig"):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        try:
            i = int(parts[0])
        except ValueError:
            continue
        ko[i] = parts[-1].strip().replace("||", "\n") if len(parts) >= 2 else ""
    return ko


# --------------------------------------------------------------------------- translation (OpenAI-compatible)
LANG_NAMES = {"ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文"}


def system_prompt(src: str, tgt: str) -> str:
    tgt_name = LANG_NAMES.get(tgt, tgt)
    return (
        f"You are a professional subtitle translator. Translate each line of {LANG_NAMES.get(src, src)} dialogue into natural, "
        f"colloquial {tgt_name} subtitles, keeping the register (casual/polite) implied by the speakers.\n"
        "Rules:\n"
        "1) Input lines are 'ID<TAB>text'. Output exactly one line per input: 'ID<TAB>translation'. Keep the IDs.\n"
        "2) If a line is only an interjection, onomatopoeia, laughter or meaningless filler, output 'ID<TAB>-'.\n"
        "3) No explanations, notes, quotes or extra text. Do not merge or split lines.\n"
        "4) The source comes from speech recognition; fix obvious mis-recognitions from context."
    )


def check_llm(url: str, api_key: str = "") -> Optional[list[str]]:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", [])]
    except Exception:  # noqa: BLE001
        return None


def translate_llm(lines: list[tuple[int, str]], st: Settings, src_lang: str,
                  logger: Callable[[str], None] = log.info,
                  progress: Optional[Callable[[float], None]] = None,
                  cancel: Optional[Callable[[], bool]] = None) -> dict:
    url = st.llm_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if st.llm_api_key:
        headers["Authorization"] = f"Bearer {st.llm_api_key}"
    model = st.llm_model or (check_llm(st.llm_url, st.llm_api_key) or ["default"])[0]
    result, prev = {}, []
    batch = max(5, int(st.llm_batch))
    sysmsg = system_prompt(src_lang if src_lang != "auto" else "ja", st.target_lang)
    for b in range(0, len(lines), batch):
        if cancel and cancel():
            raise Cancelled()
        chunk = lines[b:b + batch]
        ctx = "\n".join(f"(context) {t}" for _, t in prev[-6:])
        body = "\n".join(f"{i}\t{t}" for i, t in chunk)
        payload = {"model": model, "temperature": 0.2,
                   "messages": [{"role": "system", "content": sysmsg},
                                {"role": "user", "content": (ctx + "\n\n" if ctx else "") + body}]}
        txt = ""
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=600) as r:
                    txt = json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
                break
            except Exception as e:  # noqa: BLE001
                logger(f"  번역 요청 실패 ({attempt+1}/3): {e}")
                time.sleep(3)
        ids = {i for i, _ in chunk}
        got = 0
        for line in txt.splitlines():
            m = re.match(r"^\s*(\d+)\s*[\t:：.)\-]\s*(.*)$", line)
            if m and int(m.group(1)) in ids:
                tr = m.group(2).strip().strip('"「」')
                result[int(m.group(1))] = "" if tr in ("-", "", "—") else tr
                got += 1
        logger(f"  번역 {min(b+batch, len(lines))}/{len(lines)} (응답 {got}/{len(chunk)})")
        if progress:
            progress(min(1.0, (b + len(chunk)) / max(1, len(lines))))
        prev = chunk
    return result


# --------------------------------------------------------------------------- one file
@dataclass
class Result:
    video: str
    outputs: list[str] = field(default_factory=list)
    segments: int = 0
    kept: int = 0
    seconds: float = 0.0


def process_file(video_path: str, st: Settings, tr: Transcriber,
                 logger: Callable[[str], None] = log.info,
                 progress: Optional[Callable[[str, float], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 start: Optional[float] = None, end: Optional[float] = None) -> Result:
    """Transcribe one media file and write outputs next to it."""
    t0 = time.time()
    video = Path(video_path)
    res = Result(video=str(video))
    lang = st.language

    def prog(stage, v):
        if progress:
            progress(stage, v)

    logger(f"── {video.name}")
    prog("decode", 0.0)
    audio = decode_audio(str(video), start, end, progress=lambda v: prog("decode", v), cancel=cancel)
    if audio.size == 0:
        raise RuntimeError("디코딩된 오디오가 없습니다")
    logger(f"오디오 디코딩 완료: {audio.size / SAMPLE_RATE / 60:.1f}분")

    tr.load(st.model, st.device, logger)
    prog("transcribe", 0.0)
    raw = tr.transcribe(audio, lang, st.fast, start or 0.0, progress=lambda v: prog("transcribe", v),
                        cancel=cancel, logger=logger)
    del audio
    merged = filter_segments(raw, logger)
    kept = [s for s in merged if not s["drop"]]
    res.segments, res.kept = len(raw), len(kept)

    src_tag = lang if lang != "auto" else "src"
    srt_src = output_path(video, src_tag, st.overwrite)
    write_srt(srt_src, ({"start": s["start"], "end": s["end"], "text": s["text"]} for s in kept), st.max_dur)
    res.outputs.append(str(srt_src))
    logger(f"원문 SRT 저장: {srt_src.name} ({len(kept)}줄)")

    if st.write_tsv:
        tsv = video.with_name(f"{video.stem}.translate.tsv")
        with open(tsv, "w", encoding="utf-8") as f:
            f.write("# id\tstart\tend\tsource\ttranslation  ← 마지막 열을 채운 뒤 앱의 '번역 TSV 적용'으로 자막 생성. 삭제할 줄은 '-'\n")
            for s in kept:
                f.write(f"{s['id']}\t{fmt_ts(s['start'])}\t{fmt_ts(s['end'])}\t{s['text']}\t\n")
        res.outputs.append(str(tsv))
    # keep filtered segments for later TSV apply
    seg_json = video.with_name(f"{video.stem}.whisper.json")
    json.dump({"settings": st.to_dict(), "segments": merged}, open(seg_json, "w", encoding="utf-8"), ensure_ascii=False)
    res.outputs.append(str(seg_json))

    if st.translate:
        prog("translate", 0.0)
        models = check_llm(st.llm_url, st.llm_api_key)
        if models is None:
            logger(f"번역 서버에 연결할 수 없습니다 ({st.llm_url}) — 번역을 건너뜁니다. "
                   f"LM Studio/Ollama 서버를 켜거나 URL·API 키를 확인하세요.")
        else:
            logger(f"번역 시작: {st.llm_model or models[0]} → {st.target_lang}, {len(kept)}줄")
            ko = translate_llm([(s["id"], s["text"]) for s in kept], st, lang, logger,
                               progress=lambda v: prog("translate", v), cancel=cancel)
            ko_tsv = video.with_name(f"{video.stem}.{st.target_lang}.tsv")
            with open(ko_tsv, "w", encoding="utf-8") as f:
                for s in kept:
                    f.write(f"{s['id']}\t{ko.get(s['id'], '')}\n")
            srt_tgt = output_path(video, st.target_lang, st.overwrite)
            n = write_srt(srt_tgt, merge_translated(merged, ko), st.max_dur)
            res.outputs += [str(ko_tsv), str(srt_tgt)]
            logger(f"번역 SRT 저장: {srt_tgt.name} ({n}줄)")

    res.seconds = time.time() - t0
    prog("done", 1.0)
    logger(f"완료 ({res.seconds:.0f}s)")
    return res


def apply_translation(video_path: str, tsv_path: str, st: Settings, logger: Callable[[str], None] = log.info) -> Path:
    """Build <stem>.<target>.srt from <stem>.whisper.json + a translation TSV."""
    video = Path(video_path)
    seg_json = video.with_name(f"{video.stem}.whisper.json")
    if not seg_json.exists():
        raise FileNotFoundError(f"{seg_json.name} 이(가) 없습니다. 먼저 전사를 실행하세요.")
    merged = json.load(open(seg_json, encoding="utf-8"))["segments"]
    ko = read_translation_tsv(Path(tsv_path))
    out = output_path(video, st.target_lang, st.overwrite)
    n = write_srt(out, merge_translated(merged, ko), st.max_dur)
    logger(f"번역 SRT 저장: {out.name} ({n}줄)")
    return out
