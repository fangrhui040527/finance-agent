"""FinBERT, run locally: a financial-tone score for a headline, no vendor involved.

Added 2026-11-02 so the Bursa names have a sentiment line at all. No vendor
this project can reach scores them: Alpha Vantage's news call went premium on
2026-10-07, and Marketaux's free tier answers nothing for the `.KL` symbols
(checked from a runner on 2026-10-29). FinBERT (Araci 2019, the ProsusAI
checkpoint) is BERT fine-tuned on the Financial PhraseBank; it reads a
sentence and gives P(positive), P(negative), P(neutral). The score this
project stores is P(positive) - P(negative), in [-1, 1].

It runs as an ONNX export (`Xenova/finbert` on Hugging Face, the ProsusAI
weights converted for ONNX Runtime) so the collector needs `onnxruntime` and
`tokenizers` - two wheels, about 20 MB - instead of PyTorch. They are an
optional dependency group (`uv sync --group sentiment`): the system runs
offline and keyless without them, and only collect.yml installs them.

The model files are downloaded once, into `cache_dir`
(`~/.cache/finance-agent/finbert` unless `FINBERT_CACHE` says otherwise), and
read from there afterwards; collect.yml keeps that directory in the Actions
cache. The repository commit the files came from is recorded beside them and
reported in every observation, so a score can always be traced to the exact
weights that produced it.

Limits that matter when reading the number:

  * it scores the TITLE. Most stored articles are titles only (google_news,
    the Malaysian RSS feeds), and scoring title-plus-body for some sources and
    the title alone for others would make the days incomparable;
  * FinBERT was trained on English financial news. A Malay or Chinese title is
    not scored (the collector filters on the stored language);
  * a headline's tone is not a cause. The pages still offer a cause only for
    the part of a move the decomposition leaves unexplained.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

REPO = "Xenova/finbert"
REVISION = os.environ.get("FINBERT_REVISION", "main")
#: Tried in order: the int8-quantised export (about 110 MB) first, the
#: full-precision one (about 440 MB) if a revision lacks it.
MODEL_FILES = ("onnx/model_quantized.onnx", "onnx/model_int8.onnx", "onnx/model.onnx")
SIDE_FILES = ("config.json", "tokenizer.json")
MAX_TOKENS = 64
BATCH = 32
USER_AGENT = "finplanet-analyst-mind/0.1 (personal research)"


class FinbertUnavailable(RuntimeError):
    """The model cannot run here: a dependency or a model file is missing."""


def default_cache_dir() -> Path:
    raw = os.environ.get("FINBERT_CACHE", "").strip()
    return Path(raw) if raw else Path.home() / ".cache" / "finance-agent" / "finbert"


def _download(url: str, dest: Path, opener=None) -> str:
    """Fetch `url` into `dest` atomically. Returns the repo commit, if given."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_ = opener or urllib.request.urlopen
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A temporary file of its own, so two processes filling one cache never
    # write into each other's half-downloaded file; the rename is atomic.
    fd, name = tempfile.mkstemp(prefix=f"{dest.name}.", suffix=".part", dir=dest.parent)
    tmp = Path(name)
    commit = ""
    try:
        with open_(req, timeout=120) as resp, os.fdopen(fd, "wb") as out:
            headers = getattr(resp, "headers", None)
            if headers is not None:
                commit = headers.get("X-Repo-Commit", "") or ""
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return commit


def fetch_model(cache_dir: Path | None = None, opener=None) -> tuple[Path, dict]:
    """The model directory, downloading what is missing. Returns (dir, meta)."""
    root = (cache_dir or default_cache_dir()) / REPO.replace("/", "__") / REVISION
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    base = f"https://huggingface.co/{REPO}/resolve/{REVISION}/"
    try:
        for name in SIDE_FILES:
            if not (root / name).exists():
                commit = _download(base + name, root / name, opener)
                meta["commit"] = meta.get("commit") or commit
        if not (meta.get("model_file") and (root / meta["model_file"]).exists()):
            last: Exception | None = None
            for name in MODEL_FILES:
                try:
                    commit = _download(base + name, root / name, opener)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        last = e
                        continue
                    raise
                meta["model_file"] = name
                meta["commit"] = meta.get("commit") or commit
                break
            else:
                raise FinbertUnavailable(
                    f"none of {', '.join(MODEL_FILES)} exists in {REPO}@{REVISION}: {last}"
                )
    except (urllib.error.URLError, OSError) as e:
        raise FinbertUnavailable(f"could not download {REPO}@{REVISION}: {e}") from e
    meta["repo"], meta["revision"] = REPO, REVISION
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return root, meta


class FinBert:
    """Scores headlines. Construct once; `score` takes a batch of strings."""

    def __init__(self, cache_dir: Path | None = None, opener=None) -> None:
        try:
            # The optional `sentiment` group; absent from a default install.
            import numpy as np  # noqa: F401  # pyright: ignore[reportMissingImports]
            import onnxruntime as ort  # pyright: ignore[reportMissingImports]
            from tokenizers import Tokenizer  # pyright: ignore[reportMissingImports]
        except ImportError as e:
            raise FinbertUnavailable(
                f"FinBERT needs onnxruntime and tokenizers ({e.name} is missing): "
                "`uv sync --group sentiment`"
            ) from e
        root, meta = fetch_model(cache_dir, opener)
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        id2label = {int(k): str(v).lower() for k, v in (config.get("id2label") or {}).items()}
        if not {"positive", "negative", "neutral"} <= set(id2label.values()):
            raise FinbertUnavailable(f"unexpected labels in {REPO} config.json: {id2label}")
        self.labels = [id2label[i] for i in sorted(id2label)]
        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, os.cpu_count() or 1)
        self.session = ort.InferenceSession(
            str(root / meta["model_file"]), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.version = f"{REPO}@{(meta.get('commit') or REVISION)[:12]} {meta['model_file']}"

    def score(self, texts: Sequence[str]) -> list[dict[str, float]]:
        """{positive, negative, neutral, score} per text, score = pos - neg."""
        import numpy as np  # pyright: ignore[reportMissingImports]

        out: list[dict[str, float]] = []
        for i in range(0, len(texts), BATCH):
            batch = [t or "" for t in texts[i : i + BATCH]]
            enc = self.tokenizer.encode_batch(batch)
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
            }
            logits = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            for row in logits:
                probs = softmax([float(x) for x in row])
                p = dict(zip(self.labels, probs, strict=True))
                out.append({**p, "score": p["positive"] - p["negative"]})
        return out


def softmax(values: Sequence[float]) -> list[float]:
    top = max(values)
    exps = [math.exp(v - top) for v in values]
    total = sum(exps)
    return [e / total for e in exps]
