"""Knowledge base in PostgreSQL with pgvector: the same hybrid search, in SQL.

One query does all of it, so a search is one round trip (this matters later,
when Aurora's Data API puts every call over HTTPS):

* vector search: pgvector cosine distance over an HNSW index;
* keyword search: Postgres full-text search. Each passage is indexed with the
  English or Spanish settings for its language (so "floods" finds "flood" and
  "inundaciones" finds "inundación"), accents removed;
* exact disaster codes (``DR-9901-MO``) through a ``dr_codes`` array, because
  the text parser splits codes into pieces;
* reciprocal rank fusion of the two rankings, as in ``knowledge.rrf``.

Needs the ``psycopg`` package (``pip install "psycopg[binary]"``) and a
database where the ``vector`` and ``unaccent`` extensions can be created. The
connection string comes from ``KB_DATABASE_URL`` or the caller.

With ``KB_DB_AUTH=iam`` the connection string has no password: each connection
signs in with a 15-minute IAM token made from the AWS credentials (the ECS task
role, or ``aws login`` on a laptop). Aurora clusters made with express
configuration accept only this, e.g.
``KB_DATABASE_URL=postgresql://kb_app@<cluster endpoint>:5432/kb``.

Command line (``--fake`` uses the offline embedder, no Voyage key)::

    python3 -m disaster_aid.knowledge_pg init  [--fake]
    python3 -m disaster_aid.knowledge_pg load  fixtures/kb_passages.json [--fake]
    python3 -m disaster_aid.knowledge_pg search "Will FEMA pay for a hotel?" [--fake] [--rerank]
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import date
from typing import Sequence

from .knowledge import (
    _CODE_RE, _FOLDED_STOPWORDS, Embedder, FakeEmbedder, Hit, HybridSearch, Passage,
    Reranker, VoyageEmbedder, VoyageReranker, _check_unique, _fold, load_passages, passage_text,
)

RRF_K = 60
SCHEMA_VERSION = "1"

# {dimensions} is filled in from the embedder: the column size must match it.
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent() is only STABLE (its dictionary could change), and a generated
-- column needs IMMUTABLE. Naming the dictionary makes this wrapper safe to mark so.
CREATE OR REPLACE FUNCTION kb_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- Which model made the vectors. Questions must be embedded by the same one.
CREATE TABLE IF NOT EXISTS kb_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_passages (
    id         text PRIMARY KEY,
    lang       text NOT NULL CHECK (lang IN ('en', 'es')),
    source_url text NOT NULL DEFAULT '',
    title      text NOT NULL DEFAULT '',
    body       text NOT NULL,
    published  date,
    dr_codes   text[] NOT NULL DEFAULT '{{}}',
    embedding  vector({dimensions}) NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS (
        to_tsvector(CASE lang WHEN 'es' THEN 'spanish'::regconfig ELSE 'english'::regconfig END,
                    kb_unaccent(title || ' ' || body))
    ) STORED
);

CREATE INDEX IF NOT EXISTS kb_passages_embedding ON kb_passages USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_passages_tsv       ON kb_passages USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_passages_dr_codes  ON kb_passages USING gin (dr_codes);
"""

# The filters repeat in both halves so each half can use its own index.
_FILTERS = """(%(lang)s::text IS NULL OR p.lang = %(lang)s)
          AND (%(code)s::text IS NULL OR %(code)s = ANY(p.dr_codes))"""

SEARCH_SQL = f"""
WITH q AS (
    SELECT %(qvec)s::vector AS v,
           to_tsquery('english', %(terms)s) || to_tsquery('spanish', %(terms)s) AS tsq,
           %(codes)s::text[] AS codes
),
vec AS (
    -- ORDER BY the distance to the query parameter itself (not q.v through a
    -- join), with no tie-breaker, or Postgres can't walk the HNSW index.
    SELECT id, row_number() OVER (ORDER BY dist, id) AS r
    FROM (SELECT p.id, p.embedding <=> %(qvec)s::vector AS dist
          FROM kb_passages p
          WHERE {_FILTERS}
          ORDER BY p.embedding <=> %(qvec)s::vector
          LIMIT %(pool)s) s
),
kw AS (
    SELECT id, row_number() OVER (ORDER BY score DESC, id) AS r
    FROM (SELECT p.id,
                 ts_rank_cd(p.tsv, q.tsq) + CASE WHEN p.dr_codes && q.codes THEN 1 ELSE 0 END AS score
          FROM kb_passages p, q
          WHERE {_FILTERS}
            AND (p.tsv @@ q.tsq OR p.dr_codes && q.codes)
          ORDER BY score DESC, p.id
          LIMIT %(pool)s) s
),
fused AS (
    SELECT coalesce(vec.id, kw.id) AS id, vec.r AS vr, kw.r AS kr,
           coalesce(1.0 / (%(rrf_k)s + vec.r), 0) + coalesce(1.0 / (%(rrf_k)s + kw.r), 0) AS score
    FROM vec FULL JOIN kw ON vec.id = kw.id
)
SELECT p.id, p.lang, p.source_url, p.title, p.body, p.published, p.dr_codes,
       f.score::float8, (1 - (p.embedding <=> q.v))::float8 AS similarity, f.vr, f.kr
FROM fused f JOIN kb_passages p ON p.id = f.id CROSS JOIN q
ORDER BY f.score DESC, p.id
LIMIT %(n)s
"""

UPSERT_SQL = """
INSERT INTO kb_passages (id, lang, source_url, title, body, published, dr_codes, embedding)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
ON CONFLICT (id) DO UPDATE SET
    lang = EXCLUDED.lang, source_url = EXCLUDED.source_url, title = EXCLUDED.title,
    body = EXCLUDED.body, published = EXCLUDED.published, dr_codes = EXCLUDED.dr_codes,
    embedding = EXCLUDED.embedding
"""

_WORD_RE = re.compile(r"[a-z0-9]+")


def vector_literal(vec: Sequence[float]) -> str:
    """pgvector's text form, "[0.1,0.2,...]". Avoids needing the pgvector package."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def query_terms(query: str) -> tuple[str, list[str]]:
    """Split a question into full-text terms and disaster codes.

    Terms are joined with ``|`` (OR), so a passage needs only some of the
    words; ranking rewards the ones with more. Postgres does the stemming, so
    words are passed whole. Only ``[a-z0-9]`` reaches ``to_tsquery``, so a
    question can't inject tsquery operators.
    """
    codes = sorted({c.upper() for c in _CODE_RE.findall(query)})
    words = [w for w in _WORD_RE.findall(_fold(_CODE_RE.sub(" ", query)))
             if w not in _FOLDED_STOPWORDS and (len(w) > 1 or w.isdigit())]
    return " | ".join(dict.fromkeys(words)), codes


def schema_sql(dimensions: int) -> str:
    if not isinstance(dimensions, int) or not 1 <= dimensions <= 16000:
        raise ValueError("dimensions must be a whole number from 1 to 16000.")
    return SCHEMA.format(dimensions=dimensions)


# Seconds to wait for a connection. Aurora Serverless v2 at 0 ACU pauses when idle
# and takes about 15 seconds to resume, so the first question after a pause waits.
CONNECT_TIMEOUT = 30
_RDS_HOST_REGION = re.compile(r"\.([a-z]{2}(?:-[a-z]+)+-\d+)\.rds\.amazonaws\.com$")


def iam_auth_enabled() -> bool:
    return os.environ.get("KB_DB_AUTH", "").strip().lower() == "iam"


def iam_token(host: str, port: int, user: str) -> str:
    """A 15-minute RDS IAM sign-in token, made locally from the AWS credentials."""
    import boto3
    m = _RDS_HOST_REGION.search(host)
    region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
              or (m.group(1) if m else None))
    if not region:
        raise RuntimeError("Set AWS_REGION to sign in to the knowledge base with IAM.")
    try:
        return boto3.client("rds", region_name=region).generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user, Region=region)
    except Exception as err:   # no or expired AWS credentials, most often
        raise RuntimeError(f"Could not make an IAM sign-in token: {type(err).__name__}") from None


def connection_args(dsn: str, *, iam: bool | None = None, token_maker=iam_token,
                    timeout: int = CONNECT_TIMEOUT) -> dict:
    """psycopg connection settings for ``dsn``, with an IAM token as the password when asked."""
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(dsn)
    params.setdefault("connect_timeout", str(timeout))
    if iam_auth_enabled() if iam is None else iam:
        host, user = params.get("host"), params.get("user")
        if not host or not user:
            raise RuntimeError("With KB_DB_AUTH=iam, KB_DATABASE_URL needs a user and host, "
                               "like postgresql://kb_app@HOST:5432/kb.")
        if params.get("password"):
            raise RuntimeError("With KB_DB_AUTH=iam, leave the password out of KB_DATABASE_URL.")
        params["password"] = token_maker(host, int(params.get("port") or 5432), user)
        if params.get("sslmode", "prefer") in ("disable", "allow", "prefer"):
            params["sslmode"] = "require"   # IAM sign-in only works over SSL
    return params


def connect(dsn: str | None = None, *, timeout: int = CONNECT_TIMEOUT):
    """Open an autocommit connection to the knowledge base (``KB_DATABASE_URL`` by default)."""
    import psycopg
    dsn = dsn or os.environ.get("KB_DATABASE_URL")
    if not dsn:
        raise RuntimeError("Set KB_DATABASE_URL (a PostgreSQL connection string).")
    return psycopg.connect(**connection_args(dsn, timeout=timeout), autocommit=True)


_connect = connect   # older name, still used by evals/run_kb_evals.py


def init_schema(conn, embedder: Embedder) -> None:
    """Create the tables and indexes, and record the embedding model.

    Safe to run again with the same model. With a different model or vector
    size it refuses: drop ``kb_passages`` and ``kb_meta`` and reload instead.
    """
    with conn.transaction():
        conn.execute(schema_sql(embedder.dimensions))
        found = dict(conn.execute("SELECT key, value FROM kb_meta").fetchall())
        want = {"model": embedder.model, "dimensions": str(embedder.dimensions),
                "schema_version": SCHEMA_VERSION}
        if found and found != want:
            raise ValueError(f"The database holds vectors from {found.get('model')} "
                             f"({found.get('dimensions')} dims); the embedder is {embedder.model} "
                             f"({embedder.dimensions} dims). Drop kb_passages and kb_meta and reload.")
        if not found:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO kb_meta (key, value) VALUES (%s, %s)", list(want.items()))


class PostgresKnowledgeBase(HybridSearch):
    """Hybrid search in PostgreSQL + pgvector. Same ``search()`` as the in-memory one."""

    def __init__(self, conn, embedder: Embedder, *, candidates: int = 20,
                 reranker: Reranker | None = None, rerank_candidates: int = 20):
        self.conn = conn
        self.embedder = embedder
        self.candidates = candidates
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        self._check_model()

    @classmethod
    def connect(cls, dsn: str | None = None, embedder: Embedder | None = None, **kw) -> "PostgresKnowledgeBase":
        return cls(_connect(dsn), embedder or VoyageEmbedder(), **kw)

    def _check_model(self) -> None:
        import psycopg
        try:
            meta = dict(self.conn.execute("SELECT key, value FROM kb_meta").fetchall())
        except psycopg.errors.UndefinedTable:
            raise RuntimeError("The knowledge base tables don't exist yet; run init first.") from None
        if (meta.get("model"), meta.get("dimensions")) != (self.embedder.model, str(self.embedder.dimensions)):
            raise ValueError(f"The database holds vectors from {meta.get('model')} "
                             f"({meta.get('dimensions')} dims), but the embedder is {self.embedder.model} "
                             f"({self.embedder.dimensions} dims). Questions and passages must use the same model.")

    def upsert(self, passages: Sequence[Passage]) -> int:
        """Embed and insert or update passages. Returns how many were written."""
        _check_unique(passages)
        if not passages:
            return 0
        vectors = self.embedder.embed_documents([passage_text(p) for p in passages])
        rows = [(p.id, p.lang, p.source_url, p.title, p.body, p.published, list(p.dr_codes), vector_literal(v))
                for p, v in zip(passages, vectors)]
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        return len(rows)

    def replace_all(self, passages: Sequence[Passage]) -> int:
        """Make the table hold exactly these passages (upsert, then delete the rest)."""
        with self.conn.transaction():
            written = self.upsert(passages)
            self.conn.execute("DELETE FROM kb_passages WHERE NOT (id = ANY(%s))", ([p.id for p in passages],))
        return written

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM kb_passages").fetchone()[0]

    def _candidates(self, query: str, n: int, lang: str | None, code: str | None) -> list[Hit]:
        terms, codes = query_terms(query)
        params = {
            "qvec": vector_literal(self.embedder.embed_query(query)),
            "terms": terms, "codes": codes, "lang": lang, "code": code,
            "pool": max(n, self.candidates), "rrf_k": RRF_K, "n": n,
        }
        rows = self.conn.execute(SEARCH_SQL, params).fetchall()
        return [
            Hit(Passage(id=r[0], lang=r[1], source_url=r[2], title=r[3], body=r[4],
                        published=r[5] if isinstance(r[5], date) else None, dr_codes=tuple(r[6])),
                score=r[7], similarity=r[8], vector_rank=r[9], keyword_rank=r[10])
            for r in rows
        ]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m disaster_aid.knowledge_pg",
                                     description="Knowledge base in PostgreSQL + pgvector.")
    parser.add_argument("command", choices=["init", "load", "search", "schema"])
    parser.add_argument("arg", nargs="?", help="passages JSON for load; question for search")
    parser.add_argument("--fake", action="store_true", help="offline hashed-word embedder, no Voyage key")
    parser.add_argument("--rerank", action="store_true", help="rerank results with Voyage")
    parser.add_argument("--lang", choices=["en", "es"])
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--dsn", help="connection string (default: KB_DATABASE_URL)")
    args = parser.parse_args(argv)

    embedder: Embedder = FakeEmbedder() if args.fake else VoyageEmbedder()
    if args.command == "schema":
        print(schema_sql(embedder.dimensions))
        return
    conn = _connect(args.dsn)
    if args.command == "init":
        init_schema(conn, embedder)
        print(f"Ready: kb_passages for {embedder.model} ({embedder.dimensions} dims).")
        return
    if not args.arg:
        parser.error(f"{args.command} needs an argument.")
    base = PostgresKnowledgeBase(conn, embedder, reranker=VoyageReranker() if args.rerank else None)
    if args.command == "load":
        n = base.replace_all(load_passages(args.arg))
        print(f"Loaded {n} passages; the table now holds {base.count()}.")
        return
    for i, h in enumerate(base.search(args.arg, k=args.k, lang=args.lang), start=1):
        rerank = f" rerank={h.rerank_score:.3f}" if h.rerank_score is not None else ""
        print(f"{i}. {h.passage.id} [{h.passage.lang}] rrf={h.score:.4f} cos={h.similarity:.3f}"
              f" vec#{h.vector_rank} kw#{h.keyword_rank}{rerank}\n   {h.passage.title or h.passage.body[:80]}")


if __name__ == "__main__":
    main()
