"""Daily knowledge-base sync: pick up changes on the source pages.

Reads every source in ``data/kb_sources.json`` the same way ``kb_ingest build``
does, compares the passages with what the database holds, and writes only the
difference:

* new passages are embedded and inserted;
* passages whose text, title, date or address changed are embedded again and updated;
* passages a page no longer produces are deleted, and so are passages of sources
  that were taken off the list;
* unchanged passages are left alone, so a quiet day costs no embedding calls.

A source that can't be read today (site down, refused, too little text) keeps
its passages as they are. If the changes would delete more than
``--max-delete`` of the table (default 25%), nothing is written: a site
redesign or an error page should be looked at by a person first.

Built to run as a scheduled ECS task (EventBridge Scheduler), but works the
same from a laptop::

    python3 -m disaster_aid.kb_sync --dry-run      # show what would change
    python3 -m disaster_aid.kb_sync                # apply it

Needs ``KB_DATABASE_URL`` (a user that may write ``kb_passages``),
``VOYAGE_API_KEY`` when something changed, and ``KB_DB_AUTH=iam`` for Aurora.
The last line printed is one JSON object, easy to find in CloudWatch Logs.

Exit status: 0 all sources read; 2 some sources failed (the rest were still
synced); 1 nothing written (an error, or too many deletions).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from .kb_ingest import DEFAULT_SOURCES, Fetcher, Result, Source, build, load_sources
from .knowledge import Embedder, FakeEmbedder, Passage, VoyageEmbedder

DEFAULT_MAX_DELETE = 0.25

SELECT_SQL = "SELECT id, lang, source_url, title, body, published, dr_codes FROM kb_passages"


def source_id(passage_id: str) -> str:
    """``"da-fema-ihp-en#3"`` -> ``"da-fema-ihp-en"``."""
    return passage_id.split("#", 1)[0]


@dataclass
class Plan:
    """What a sync would change. Passages to write are complete; deletions are ids."""

    added: list[Passage] = field(default_factory=list)
    changed: list[Passage] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    failed: dict[str, str] = field(default_factory=dict)       # source id -> error
    dropped_sources: list[str] = field(default_factory=list)   # no longer listed
    existing: int = 0

    @property
    def to_write(self) -> list[Passage]:
        return self.added + self.changed

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)

    def delete_share(self) -> float:
        return len(self.removed) / self.existing if self.existing else 0.0


def make_plan(existing: Sequence[Passage], sources: Sequence[Source],
              results: Sequence[Result]) -> Plan:
    """Compare freshly built passages with the database's."""
    plan = Plan(existing=len(existing))
    by_id = {p.id: p for p in existing}
    listed = {s.id for s in sources}
    read_ok = set()
    for r in results:
        if r.error:
            plan.failed[r.source.id] = r.error
            continue
        read_ok.add(r.source.id)
        for p in r.passages:
            old = by_id.get(p.id)
            if old is None:
                plan.added.append(p)
            elif old != p:
                plan.changed.append(p)
            else:
                plan.unchanged += 1
    fresh_ids = {p.id for r in results if not r.error for p in r.passages}
    dropped = set()
    for pid in sorted(by_id):
        sid = source_id(pid)
        if sid not in listed:
            dropped.add(sid)
            plan.removed.append(pid)
        elif sid in read_ok and pid not in fresh_ids:
            plan.removed.append(pid)
    plan.dropped_sources = sorted(dropped)
    return plan


def read_existing(conn) -> list[Passage]:
    rows = conn.execute(SELECT_SQL).fetchall()
    return [Passage(id=r[0], lang=r[1], source_url=r[2], title=r[3], body=r[4],
                    published=r[5] if isinstance(r[5], date) else None, dr_codes=tuple(r[6]))
            for r in rows]


def apply_plan(base, plan: Plan) -> None:
    """Write the plan in one transaction. ``base`` is a PostgresKnowledgeBase."""
    with base.conn.transaction():
        base.upsert(plan.to_write)        # embeds only these
        if plan.removed:
            base.conn.execute("DELETE FROM kb_passages WHERE id = ANY(%s)", (plan.removed,))


def summary(plan: Plan, *, applied: bool, total: int | None, seconds: float,
            error: str = "") -> dict:
    return {
        "event": "kb_sync",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "applied": applied,
        "added": [p.id for p in plan.added],
        "changed": [p.id for p in plan.changed],
        "removed": plan.removed,
        "unchanged": plan.unchanged,
        "failed_sources": plan.failed,
        "dropped_sources": plan.dropped_sources,
        "passages_before": plan.existing,
        "passages_after": total,
        "seconds": round(seconds, 1),
        **({"error": error} if error else {}),
    }


def _print_plan(plan: Plan) -> None:
    for label, ids in (("added", [p.id for p in plan.added]),
                       ("changed", [p.id for p in plan.changed]),
                       ("removed", plan.removed)):
        for pid in ids:
            print(f"  {label:8} {pid}")
    for sid, err in plan.failed.items():
        print(f"  FAILED   {sid}: {err}", file=sys.stderr)
    print(f"{len(plan.added)} added, {len(plan.changed)} changed, {len(plan.removed)} removed, "
          f"{plan.unchanged} unchanged; {len(plan.failed)} sources failed.")


def run(conn, embedder: Embedder, sources: Sequence[Source], fetcher: Fetcher, *,
        dry_run: bool = False, max_delete: float = DEFAULT_MAX_DELETE,
        root: Path | None = None) -> tuple[int, dict]:
    """One sync. Returns (exit status, summary)."""
    from .knowledge_pg import PostgresKnowledgeBase
    started = time.monotonic()
    base = PostgresKnowledgeBase(conn, embedder)      # refuses a different embedding model
    existing = read_existing(conn)
    kw = {"root": root} if root else {}
    results = build(sources, fetcher, **kw)
    plan = make_plan(existing, sources, results)
    _print_plan(plan)

    if plan.delete_share() > max_delete:
        err = (f"would delete {len(plan.removed)} of {plan.existing} passages "
               f"({plan.delete_share():.0%}, limit {max_delete:.0%}); nothing written. "
               "Check the sources, then rerun with a higher --max-delete if this is expected.")
        print(err, file=sys.stderr)
        return 1, summary(plan, applied=False, total=plan.existing,
                          seconds=time.monotonic() - started, error=err)
    applied = False
    if plan.has_changes and not dry_run:
        apply_plan(base, plan)
        applied = True
    total = base.count()
    status = 2 if plan.failed else 0
    return status, summary(plan, applied=applied, total=total, seconds=time.monotonic() - started)


def connect_with_retry(dsn: str | None, *, attempts: int = 4, wait: float = 15,
                       connect=None, sleep=time.sleep):
    """Connect, retrying while a paused Aurora Serverless cluster resumes (15-40 s)."""
    if connect is None:
        from .knowledge_pg import connect
    for attempt in range(1, attempts + 1):
        try:
            return connect(dsn)
        except Exception as err:   # psycopg.OperationalError: timeout or refused while resuming
            if attempt == attempts or type(err).__name__ != "OperationalError":
                raise
            print(f"Database not ready ({attempt}/{attempts}), retrying in {wait:.0f} s.",
                  file=sys.stderr)
            sleep(wait)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m disaster_aid.kb_sync",
                                     description="Update the knowledge base with changes on the source pages.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--dsn", help="connection string (default: KB_DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    parser.add_argument("--max-delete", type=float, default=DEFAULT_MAX_DELETE,
                        help="largest share of passages one run may delete (default 0.25)")
    parser.add_argument("--fake", action="store_true", help="offline hashed-word embedder, no Voyage key")
    args = parser.parse_args(argv)

    started = time.monotonic()
    try:
        sources = load_sources(args.sources)
        embedder: Embedder = FakeEmbedder() if args.fake else VoyageEmbedder()
        with connect_with_retry(args.dsn) as conn:
            status, info = run(conn, embedder, sources, Fetcher(), dry_run=args.dry_run,
                               max_delete=args.max_delete)
    except Exception as err:   # report every failure in the same one-line form
        status, info = 1, summary(Plan(), applied=False, total=None,
                                  seconds=time.monotonic() - started,
                                  error=f"{type(err).__name__}: {err}")
        print(info["error"], file=sys.stderr)
    print(json.dumps(info, ensure_ascii=False))
    sys.exit(status)


if __name__ == "__main__":
    main()
