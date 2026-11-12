"""Knowledge base: hybrid search over FEMA help documents.

This is for general questions ("Will FEMA pay for a hotel?", "I rent, can I
apply?"). It does not answer "can my county apply, and by when?": that stays
in the FEMA pipeline (fema_client -> status), where code computes every fact.

Hybrid search runs two searches and merges their rankings:

* **Keyword search (BM25)** catches exact terms: disaster codes such as
  ``DR-4798-TX``, program names such as "IHP".
* **Vector search** catches meaning. A multilingual embedding model puts
  "¿FEMA paga el hotel?" close to an English passage about hotel costs.
* **Reciprocal rank fusion (RRF)** merges the two lists by rank, so their
  scores (BM25 points and cosine similarity) never need to be on one scale.

Pieces, each swappable:

* ``Embedder``: ``VoyageEmbedder`` (Voyage AI API, production) or
  ``FakeEmbedder`` (hashed words, no network, for tests and offline runs).
* ``Reranker`` (optional): ``VoyageReranker`` (Voyage AI API) or
  ``FakeReranker`` re-scores the fused candidates, question and passage together.
* ``KnowledgeBase``: ``InMemoryKnowledgeBase`` here, ``PostgresKnowledgeBase``
  (pgvector) in ``knowledge_pg.py``; both share the ``HybridSearch`` flow.

Standard library only, like the rest of the core pipeline.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from .explainer import _ssl_context

LANGS = ("en", "es")
INDEX_FORMAT = 1


# --- Passages and results ---------------------------------------------------------

@dataclass(frozen=True)
class Passage:
    """One searchable chunk of a source document."""

    id: str                      # stable, e.g. "fema-ihp-guide#3"
    lang: str                    # language of the text: "en" or "es"
    source_url: str              # where the text came from, shown as the citation
    body: str
    title: str = ""
    published: date | None = None
    dr_codes: tuple[str, ...] = ()   # disasters a press release is about

    def __post_init__(self):
        if not self.id or not self.body.strip():
            raise ValueError("A passage needs an id and some text.")
        if self.lang not in LANGS:
            raise ValueError(f"Passage {self.id}: lang must be one of {LANGS}.")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "lang": self.lang, "source_url": self.source_url,
            "title": self.title, "body": self.body,
            "published": self.published.isoformat() if self.published else None,
            "dr_codes": list(self.dr_codes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Passage":
        published = d.get("published")
        return cls(
            id=d["id"], lang=d["lang"], source_url=d.get("source_url", ""),
            body=d["body"], title=d.get("title", ""),
            published=date.fromisoformat(published) if published else None,
            dr_codes=tuple(c.upper() for c in d.get("dr_codes", ())),
        )


@dataclass(frozen=True)
class Hit:
    """A search result. ``score`` is the fused RRF score.

    ``similarity`` (cosine, -1..1) and ``rerank_score`` (0..1, set only when a
    reranker ran) let a caller refuse to answer when even the best passage is
    not close to the question. With a reranker, results are ordered by
    ``rerank_score``; without one, by ``score``.
    """

    passage: Passage
    score: float
    similarity: float
    vector_rank: int | None    # 1-based rank in the vector list, None if not in it
    keyword_rank: int | None   # 1-based rank in the keyword list, None if not in it
    rerank_score: float | None = None


# --- Text handling -----------------------------------------------------------------

_CODE_RE = re.compile(r"\b(?:DR|EM|FM)-\d{3,5}(?:-[A-Z]{2})?\b", re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset("""
a an and are as at be by can could do does for from has have how i if in into is it its
me my of on or our so than that the their them then there these they this to was we
were what when where which who why will with you your
el la los las un una unos unas y o de del al en con por para que qué es son se su sus
mi mis tu lo le les me nos como cómo cuando cuándo donde dónde si sí no
""".split())


def _fold(text: str) -> str:
    """Lowercase and drop accents, so "inundación" and "inundacion" match."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


_FOLDED_STOPWORDS = frozenset(_fold(w) for w in _STOPWORDS)


def tokenize(text: str) -> list[str]:
    """Words for keyword search. Disaster codes stay whole ("dr-4798-tx").

    A light plural strip ("floods" -> "flood") stands in for a stemmer; the
    Postgres backend will use its English and Spanish text-search settings.
    """
    codes = [c.lower() for c in _CODE_RE.findall(text)]
    rest = _CODE_RE.sub(" ", text)
    words = []
    for w in _WORD_RE.findall(_fold(rest)):
        if w in _FOLDED_STOPWORDS or (len(w) == 1 and not w.isdigit()):
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return codes + words


def chunk_text(text: str, max_words: int = 350, overlap: int = 50) -> list[str]:
    """Split a document into passages of at most ``max_words`` words.

    Paragraphs are kept together where they fit. A paragraph longer than
    ``max_words`` is cut into windows that share ``overlap`` words, so a
    sentence split at a boundary is still whole in one of the two chunks.
    """
    if max_words < 1 or not 0 <= overlap < max_words:
        raise ValueError("Need max_words >= 1 and 0 <= overlap < max_words.")
    paragraphs = [p.split() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[list[str]] = []
    current: list[str] = []
    for words in paragraphs:
        if len(words) > max_words:
            if current:
                chunks.append(current)
                current = []
            step = max_words - overlap
            for start in range(0, len(words), step):
                chunks.append(words[start:start + max_words])
                if start + max_words >= len(words):
                    break
            continue
        if len(current) + len(words) > max_words:
            chunks.append(current)
            current = []
        current = current + words
    if current:
        chunks.append(current)
    return [" ".join(c) for c in chunks]


def passages_from_document(doc_id: str, text: str, *, lang: str, source_url: str,
                           title: str = "", published: date | None = None,
                           dr_codes: Iterable[str] = (), max_words: int = 350,
                           overlap: int = 50) -> list[Passage]:
    """Chunk one document into passages with ids ``<doc_id>#<n>``."""
    codes = tuple(c.upper() for c in dr_codes)
    return [
        Passage(id=f"{doc_id}#{n}", lang=lang, source_url=source_url, body=chunk,
                title=title, published=published, dr_codes=codes)
        for n, chunk in enumerate(chunk_text(text, max_words, overlap), start=1)
    ]


def load_passages(path: str | Path) -> list[Passage]:
    """Read passages from JSON: a list, or ``{"passages": [...]}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["passages"] if isinstance(data, dict) else data
    passages = [Passage.from_dict(r) for r in rows]
    _check_unique(passages)
    return passages


def _check_unique(passages: Sequence[Passage]) -> None:
    seen = Counter(p.id for p in passages)
    dupes = sorted(i for i, n in seen.items() if n > 1)
    if dupes:
        raise ValueError(f"Duplicate passage ids: {', '.join(dupes[:5])}")


# --- Embedders -----------------------------------------------------------------------

class Embedder(Protocol):
    """Turns text into vectors. Documents and questions are embedded differently:
    retrieval models are trained with separate "document" and "query" modes."""

    model: str
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class FakeEmbedder:
    """Deterministic vectors from hashed words: no network, no key, no model.

    Similar wording gives similar vectors, which is enough to test ranking and
    fusion. It does not understand meaning or translate, so cross-language
    tests need a real model (see the evals).
    """

    def __init__(self, dimensions: int = 256):
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8.")
        self.dimensions = dimensions
        self.model = f"fake-hash-{dimensions}"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dimensions
            vec[slot] += 1.0 if digest[4] & 1 else -1.0
        return _normalize(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class _VoyageClient:
    """POSTs JSON to the Voyage AI API, retrying rate limits and server errors."""

    BASE_URL = "https://api.voyageai.com/v1"
    RETRY_STATUS = {429, 500, 502, 503, 504}
    model: str

    def __init__(self, api_key: str | None, attempts: int, opener: Callable | None,
                 sleep: Callable[[float], None]):
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self.api_key:
            raise RuntimeError(f"Set VOYAGE_API_KEY to use {type(self).__name__}")
        self.attempts = max(1, attempts)
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=30, context=_ssl_context()))
        self._sleep = sleep
        self.tokens_used = 0

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.BASE_URL}/{path}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"})
        for attempt in range(1, self.attempts + 1):
            try:
                with self._open(req) as r:
                    data = json.loads(r.read())
                self.tokens_used += int(data.get("usage", {}).get("total_tokens", 0))
                return data
            except urllib.error.HTTPError as err:
                if err.code in self.RETRY_STATUS and attempt < self.attempts:
                    self._sleep(2 ** (attempt - 1))
                    continue
                text = err.read().decode(errors="replace")
                try:
                    message = json.loads(text).get("detail") or text
                except (ValueError, AttributeError):
                    message = text
                raise RuntimeError(f"Voyage API error {err.code} (model={self.model}): {message}") from None
            except urllib.error.URLError as err:
                if attempt < self.attempts:
                    self._sleep(2 ** (attempt - 1))
                    continue
                raise RuntimeError(f"Voyage API unreachable: {err.reason}") from None
        raise AssertionError("unreachable")


class VoyageEmbedder(_VoyageClient):
    """Embeddings from the Voyage AI API (needs VOYAGE_API_KEY).

    The model is multilingual, so Spanish questions match English passages.
    Settings: VOYAGE_MODEL (default voyage-4) and VOYAGE_DIMENSIONS (default
    1024; the Postgres column must be the same size).
    """

    BATCH = 128          # texts per request; well under the API's limit

    def __init__(self, model: str | None = None, dimensions: int | None = None,
                 api_key: str | None = None, *, attempts: int = 4,
                 opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep):
        self.model = model or os.environ.get("VOYAGE_MODEL", "voyage-4")
        self.dimensions = int(dimensions or os.environ.get("VOYAGE_DIMENSIONS", 1024))
        super().__init__(api_key, attempts, opener, sleep)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            out.extend(self._embed(list(texts[start:start + self.BATCH]), "document"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        if not texts:
            return []
        data = self._post("embeddings", {"input": texts, "model": self.model, "input_type": input_type,
                                         "output_dimension": self.dimensions})
        rows = sorted(data.get("data", []), key=lambda r: r["index"])
        if len(rows) != len(texts):
            raise RuntimeError(f"Voyage returned {len(rows)} embeddings for {len(texts)} texts.")
        vectors = [r["embedding"] for r in rows]
        if any(len(v) != self.dimensions for v in vectors):
            raise RuntimeError(f"Voyage returned vectors that are not {self.dimensions} long "
                               f"(model={self.model}).")
        return vectors


# --- Rerankers -----------------------------------------------------------------------

class Reranker(Protocol):
    """Scores (question, passage) pairs together, which is more accurate than
    comparing two separately made vectors, but costs one call per search."""

    model: str

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """One relevance score (0..1, higher is better) per document, in input order."""
        ...


class FakeReranker:
    """Share of the question's words found in each document. Tests only."""

    model = "fake-overlap"

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        q = set(tokenize(query))
        return [len(q & set(tokenize(d))) / len(q) if q else 0.0 for d in documents]


class VoyageReranker(_VoyageClient):
    """Reranking from the Voyage AI API (needs VOYAGE_API_KEY).

    Setting: VOYAGE_RERANK_MODEL (default rerank-3).
    """

    def __init__(self, model: str | None = None, api_key: str | None = None, *, attempts: int = 4,
                 opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep):
        self.model = model or os.environ.get("VOYAGE_RERANK_MODEL", "rerank-3")
        super().__init__(api_key, attempts, opener, sleep)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        data = self._post("rerank", {"query": query, "documents": list(documents), "model": self.model,
                                     "truncation": True})
        scores: list[float | None] = [None] * len(documents)
        for row in data.get("data", []):
            scores[row["index"]] = float(row["relevance_score"])
        if any(x is None for x in scores):
            raise RuntimeError(f"Voyage returned scores for {sum(x is not None for x in scores)} "
                               f"of {len(documents)} documents.")
        return scores  # type: ignore[return-value]


# --- Search ------------------------------------------------------------------------

class KnowledgeBase(Protocol):
    def search(self, query: str, *, k: int = 5, lang: str | None = None,
               dr_code: str | None = None) -> list[Hit]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf(rankings: Iterable[Sequence[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: each list adds 1 / (k + rank) for every id in it.

    k = 60 is the value from the original paper (Cormack et al., 2009); it keeps
    one list's top hit from drowning out an id that both lists rank well.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return scores


class _BM25:
    """Okapi BM25 over the passage tokens (k1 = 1.2, b = 0.75, the usual values)."""

    def __init__(self, docs: Sequence[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, query: list[str], i: int) -> float:
        tf, total = self.tf[i], 0.0
        norm = self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1))
        for t in query:
            f = tf.get(t)
            if f:
                total += self.idf[t] * f * (self.k1 + 1) / (f + norm)
        return total


def passage_text(p: Passage) -> str:
    """What gets embedded and reranked: the title (if any) and the body."""
    return f"{p.title}\n{p.body}" if p.title else p.body


class HybridSearch:
    """The search flow every backend shares: check input, fetch fused hybrid
    candidates from the backend, then rerank them if a reranker is set.

    A backend implements ``_candidates(query, n, lang, dr_code)``: up to ``n``
    hits ordered by fused RRF score.
    """

    embedder: Embedder
    reranker: Reranker | None = None
    rerank_candidates: int = 20

    def _candidates(self, query: str, n: int, lang: str | None, dr_code: str | None) -> list[Hit]:
        raise NotImplementedError

    def search(self, query: str, *, k: int = 5, lang: str | None = None,
               dr_code: str | None = None) -> list[Hit]:
        """Top ``k`` passages for ``query``, best first.

        ``lang`` limits results to passages in that language. Leave it None to
        let a Spanish question find English passages. ``dr_code`` limits results
        to passages tagged with that disaster.
        """
        query = (query or "").strip()
        if not query:
            raise ValueError("Type a question to search for.")
        if k < 1:
            raise ValueError("k must be at least 1.")
        if lang is not None and lang not in LANGS:
            raise ValueError(f"lang must be one of {LANGS}.")
        code = dr_code.upper() if dr_code else None
        if self.reranker is None:
            return self._candidates(query, k, lang, code)
        hits = self._candidates(query, max(k, self.rerank_candidates), lang, code)
        scores = self.reranker.rerank(query, [passage_text(h.passage) for h in hits])
        ranked = sorted(zip(hits, scores), key=lambda hs: -hs[1])
        return [replace(h, rerank_score=sc) for h, sc in ranked[:k]]


class InMemoryKnowledgeBase(HybridSearch):
    """Hybrid search held in memory. Fine for a few thousand passages.

    Build with ``from_passages`` (embeds every passage) or ``load`` (reads a
    saved index, so a container start makes no embedding calls).
    """

    def __init__(self, passages: Sequence[Passage], vectors: Sequence[Sequence[float]],
                 embedder: Embedder, *, candidates: int = 20, reranker: Reranker | None = None,
                 rerank_candidates: int = 20):
        if len(passages) != len(vectors):
            raise ValueError("Need one vector per passage.")
        _check_unique(passages)
        for p, v in zip(passages, vectors):
            if len(v) != embedder.dimensions:
                raise ValueError(f"Passage {p.id}: vector has {len(v)} numbers, "
                                 f"the embedder makes {embedder.dimensions}.")
        self.passages = list(passages)
        self.vectors = [list(v) for v in vectors]
        self.embedder = embedder
        self.candidates = candidates
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        self._bm25 = _BM25([tokenize(f"{p.title} {p.body}") for p in self.passages])

    @classmethod
    def from_passages(cls, passages: Sequence[Passage], embedder: Embedder, **kw) -> "InMemoryKnowledgeBase":
        _check_unique(passages)
        return cls(passages, embedder.embed_documents([passage_text(p) for p in passages]), embedder, **kw)

    def _candidates(self, query: str, n: int, lang: str | None, code: str | None) -> list[Hit]:
        allowed = [i for i, p in enumerate(self.passages)
                   if (lang is None or p.lang == lang) and (code is None or code in p.dr_codes)]
        if not allowed:
            return []

        qvec = self.embedder.embed_query(query)
        sims = {i: cosine(qvec, self.vectors[i]) for i in allowed}
        by_vector = sorted(allowed, key=lambda i: (-sims[i], i))[:self.candidates]

        qtokens = tokenize(query)
        bm25 = {i: self._bm25.score(qtokens, i) for i in allowed}
        by_keyword = sorted((i for i in allowed if bm25[i] > 0), key=lambda i: (-bm25[i], i))[:self.candidates]

        vrank = {i: r for r, i in enumerate(by_vector, start=1)}
        krank = {i: r for r, i in enumerate(by_keyword, start=1)}
        fused = rrf([[str(i) for i in by_vector], [str(i) for i in by_keyword]])
        order = sorted(fused, key=lambda s: (-fused[s], int(s)))[:n]
        return [
            Hit(self.passages[i], fused[s], sims[i], vrank.get(i), krank.get(i))
            for s in order for i in [int(s)]
        ]

    # Saved index: passages plus vectors, tagged with the model that made them.

    def save(self, path: str | Path) -> None:
        data = {
            "format": INDEX_FORMAT,
            "model": self.embedder.model,
            "dimensions": self.embedder.dimensions,
            "passages": [p.to_dict() for p in self.passages],
            "vectors": [[round(x, 6) for x in v] for v in self.vectors],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, embedder: Embedder, **kw) -> "InMemoryKnowledgeBase":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != INDEX_FORMAT:
            raise ValueError(f"Unknown index format {data.get('format')!r}; rebuild the index.")
        if data.get("model") != embedder.model or data.get("dimensions") != embedder.dimensions:
            raise ValueError(
                f"Index was built with {data.get('model')} ({data.get('dimensions')} dims), "
                f"but the embedder is {embedder.model} ({embedder.dimensions} dims). "
                "Questions and passages must use the same model; rebuild the index.")
        passages = [Passage.from_dict(d) for d in data["passages"]]
        return cls(passages, data["vectors"], embedder, **kw)

