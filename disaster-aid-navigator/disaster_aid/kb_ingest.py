"""Build knowledge-base passages from public help pages.

Reads a list of sources (``data/kb_sources.json``), downloads each page,
keeps only its main content, splits it at headings and writes passages JSON
that ``python3 -m disaster_aid.knowledge_pg load`` puts into PostgreSQL.

Downloads are polite: the site's robots.txt is honored, there is one request
a second, and the user agent names this tool. Some sites refuse scripted
downloads (fema.gov, dol.gov) or build their text in the browser with
JavaScript (sba.gov). Save those pages from a browser instead (Chrome: Save
Page As, "Webpage, Complete") and list the saved file as ``"file"`` next to
the page's ``"url"``. This tool never works around a refusal.

Command line::

    python3 -m disaster_aid.kb_ingest build              # writes data/kb_passages.json
    python3 -m disaster_aid.kb_ingest preview da-fema-ihp-en

Standard library only, like the rest of the core pipeline.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from .explainer import _ssl_context
from .knowledge import LANGS, Passage, _check_unique, chunk_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = PROJECT_ROOT / "data" / "kb_sources.json"
DEFAULT_OUT = PROJECT_ROOT / "data" / "kb_passages.json"

USER_AGENT = "disaster-aid-navigator-kb-ingest/0.1 (builds a local FEMA-help knowledge base)"
MAX_PAGE_BYTES = 5_000_000


class IngestError(RuntimeError):
    """A source could not be read; the message says what to do about it."""


# --- Sources ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Source:
    """One page to turn into passages."""

    id: str                 # stable; passages are "<id>#1", "<id>#2", ...
    url: str                # the official page, kept as every passage's citation
    lang: str               # "en" or "es"
    file: str | None = None  # a copy saved from a browser, used instead of downloading
    title: str = ""          # replaces a vague page title ("Disaster recovery") in passages
    agency: str = ""
    topic: str = ""
    audience: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        sid = str(d.get("id", ""))
        if not _ID_RE.match(sid):
            raise ValueError(f"Source id {sid!r}: use lowercase letters, digits and dashes.")
        url = str(d.get("url", ""))
        if not url.startswith("https://"):
            raise ValueError(f"Source {sid}: url must start with https://, got {url!r}.")
        if d.get("lang") not in LANGS:
            raise ValueError(f"Source {sid}: lang must be one of {LANGS}.")
        return cls(id=sid, url=url, lang=d["lang"], file=d.get("file") or None,
                   title=d.get("title", ""), agency=d.get("agency", ""), topic=d.get("topic", ""),
                   audience=d.get("audience", ""))


def load_sources(path: str | Path) -> list[Source]:
    """Read sources from JSON: a list, or ``{"sources": [...]}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["sources"] if isinstance(data, dict) else data
    sources = [Source.from_dict(r) for r in rows]
    dupes = sorted({s.id for s in sources if sum(t.id == s.id for t in sources) > 1})
    if dupes:
        raise ValueError(f"Duplicate source ids: {', '.join(dupes[:5])}")
    return sources


# --- Pulling the main text out of a page -----------------------------------------------

# Never content: page furniture, scripts, forms, and things hidden from readers.
_SKIP_TAGS = frozenset("""script style noscript template svg iframe nav header footer
aside form button select dialog""".split())
_SKIP_ROLES = frozenset("navigation banner contentinfo dialog search complementary".split())
# The usa-* names are the U.S. Web Design System's, used by many .gov sites.
# hidden-xs (Bootstrap) marks a wide-screen copy of text the phone layout also
# shows; keeping only the phone copy avoids embedding the same facts twice.
_SKIP_CLASSES = frozenset("""hidden visually-hidden sr-only usa-sr-only element-invisible
breadcrumb breadcrumbs usa-breadcrumb no-print usa-sidenav usa-layout-docs__sidenav
usa-in-page-nav usa-skipnav usa-banner usa-identifier usa-header usa-footer hidden-xs""".split())
_VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK_TAGS = frozenset("""p li h1 h2 h3 h4 h5 h6 td th dt dd div section article main
blockquote pre figcaption caption summary details ul ol dl table tr""".split())
_HEADINGS = {f"h{n}": n for n in range(1, 7)}

# Leftover controls that are not caught by tag or class.
_JUNK_RE = re.compile(
    r"^(back|back to top|top|share|print|atrás|volver|volver arriba|compartir|imprimir)$", re.I)

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
_MONTHS.update({m: i for i, m in enumerate(
    "ene feb mar abr may jun jul ago sep oct nov dic".split(), start=1)})
_UPDATED_LABEL = (r"(?:page\s+)?(?:last\s+(?:reviewed\s+or\s+)?updated|"
                  r"última\s+(?:revisión\s+o\s+)?actualización|página\s+revisada\s+o\s+actualizada)"
                  r"(?:\s+el)?\s*:?\s*")
_DATE = (r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Za-z]{3,9}\.?-\d{4}"
         r"|[A-Za-z]{3,9}\.? \d{1,2}, \d{4}|\d{1,2} de [a-z]{3,10} de \d{4}")
_UPDATED_LINE = re.compile(rf"^{_UPDATED_LABEL}(?P<date>{_DATE})\.?$", re.I)
_UPDATED_ANYWHERE = re.compile(rf"{_UPDATED_LABEL}(?P<date>{_DATE})", re.I)


def parse_updated(text: str) -> date | None:
    """The date in a "Last updated: ..." line, or None if this is not one."""
    m = _UPDATED_LINE.match(text.strip())
    return _parse_date(m.group("date")) if m else None


def find_updated(html: str) -> date | None:
    """The first "Last updated: ..." date anywhere on the page, even in a sidebar."""
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
    m = _UPDATED_ANYWHERE.search(text)
    return _parse_date(m.group("date")) if m else None


def _parse_date(raw: str) -> date | None:
    raw = raw.strip().rstrip(".")
    try:
        if mm := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw):
            return date(int(mm[3]), int(mm[1]), int(mm[2]))
        if mm := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw):
            return date(int(mm[1]), int(mm[2]), int(mm[3]))
        if mm := re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})[a-z]*\.?-(\d{4})", raw):
            return date(int(mm[3]), _MONTHS[mm[2].lower()], int(mm[1]))
        if mm := re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})", raw):
            return date(int(mm[3]), _MONTHS[mm[1].lower()], int(mm[2]))
        if mm := re.fullmatch(r"(\d{1,2}) de ([a-z]{3})[a-z]* de (\d{4})", raw, re.I):   # 7 de julio de 2025
            return date(int(mm[3]), _MONTHS[mm[2].lower()], int(mm[1]))
    except (KeyError, ValueError):
        return None
    return None


@dataclass
class Section:
    heading: str                     # "" for text before the first heading
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = ([self.heading] if self.heading else []) + self.paragraphs
        return "\n\n".join(parts)


@dataclass
class Page:
    title: str
    sections: list[Section]
    updated: date | None = None

    @property
    def words(self) -> int:
        return sum(len(s.text.split()) for s in self.sections)


@dataclass
class _Block:
    text: str
    level: int        # heading level 1-6, or 0 for body text
    in_main: bool
    in_article: bool


class _BlockParser(HTMLParser):
    """Turns HTML into text blocks, remembering which were inside <main>/<article>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        # (tag, skip, in_main, in_article, in_list_item), cumulative from the root.
        self.stack: list[tuple[str, bool, bool, bool, bool]] = []
        self.blocks: list[_Block] = []
        self.buf: list[str] = []
        self.heading = 0
        self.title_parts: list[str] = []
        self.in_title = False

    def _state(self):
        return self.stack[-1][1:] if self.stack else (False, False, False, False)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            skip, in_main, in_article, in_li = self._state()
            if in_li and not self.heading:
                text = "- " + text
            self.blocks.append(_Block(text, self.heading, in_main, in_article))

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in _VOID_TAGS:
            if tag == "br":
                self.buf.append(" ")
            return
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        skip, in_main, in_article, in_li = self._state()
        # A button inside a heading is an accordion toggle: its text is the heading.
        own_skip = ((tag in _SKIP_TAGS and not (tag == "button" and self.heading))
                    or a.get("role") in _SKIP_ROLES
                    or a.get("aria-hidden") == "true" or "hidden" in a
                    or bool(classes & _SKIP_CLASSES))
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in _HEADINGS and not (skip or own_skip):
            self.heading = _HEADINGS[tag]
        self.stack.append((tag, skip or own_skip,
                           in_main or tag == "main" or a.get("role") == "main",
                           in_article or tag == "article",
                           in_li or tag == "li"))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if not any(t == tag for t, *_ in self.stack):
            return                      # stray end tag
        if tag in _BLOCK_TAGS or tag in _HEADINGS:
            self._flush()
        while self.stack:
            if self.stack.pop()[0] == tag:
                break
        if tag in _HEADINGS:
            self.heading = 0

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
            return
        if not self._state()[0]:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def extract_page(html: str) -> Page:
    """Main content of a page as a title and sections split at headings.

    Uses <main> (or role="main") when it holds most of the page's text, else
    <article>, else the whole body (some sites put only a banner in <main>).
    Text above the page's <h1> is dropped. A section's heading includes its
    parent ("EIDL details › Terms"), so pages that repeat a subheading under
    each program stay clear. Text repeated under the same heading is kept once.
    """
    parser = _BlockParser()
    parser.feed(html)
    parser.close()
    blocks = parser.blocks
    # The "last updated" line is often in a sidebar or footer, so look at the whole page.
    updated = find_updated(html)
    total = sum(len(b.text.split()) for b in blocks)
    for scope in ("in_main", "in_article"):
        inside = [b for b in blocks if getattr(b, scope)]
        if sum(len(b.text.split()) for b in inside) >= max(20, total / 2):
            blocks = inside
            break
    first_h1 = next((i for i, b in enumerate(blocks) if b.level == 1), 0)
    blocks = blocks[first_h1:]

    title = next((b.text for b in blocks if b.level == 1), "")
    if not title:
        raw = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
        title = re.split(r"\s+[|–—-]\s+", raw)[0] if raw else ""

    sections = [Section("")]
    path: list[tuple[int, str]] = []        # open headings, outermost first
    seen: set[tuple[str, str]] = set()
    for b in blocks:
        text = b.text.replace(" ", " ").strip()
        if b.level == 1 or parse_updated(text) or _JUNK_RE.match(text):
            continue
        if b.level:
            while path and path[-1][0] >= b.level:
                path.pop()
            parent = path[-1][1] if path else ""
            path.append((b.level, text))
            name = f"{parent} › {text}" if parent and parent != text else text
            sections.append(Section(name))
            continue
        key = (sections[-1].heading, re.sub(r"\W+", " ", text.lower()).strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        sections[-1].paragraphs.append(text)
    return Page(title=title, sections=[s for s in sections if s.paragraphs], updated=updated)


def passages_from_page(source: Source, page: Page, max_words: int = 350,
                       overlap: int = 50) -> list[Passage]:
    """Pack a page's sections into passages of at most ``max_words`` words.

    Short sections share a passage; a passage starts at a section boundary
    when the next section would not fit, and a section longer than
    ``max_words`` is cut on its own with ``chunk_text``. Headings stay in the
    text, so every passage says what it is about.
    """
    groups: list[tuple[str, str]] = []          # (first heading, text)
    current: list[Section] = []

    def close_group():
        if current:
            groups.append((current[0].heading, "\n\n".join(s.text for s in current)))
            current.clear()

    for s in page.sections:
        n = len(s.text.split())
        if n > max_words:
            close_group()
            # Cut the body only, then put the heading on top of every piece.
            head = len(s.heading.split())
            room = max(max_words - head, overlap + 1)
            body = "\n\n".join(s.paragraphs)
            for chunk in chunk_text(body, room, overlap):
                groups.append((s.heading, f"{s.heading}\n\n{chunk}" if s.heading else chunk))
            continue
        if current and sum(len(c.text.split()) for c in current) + n > max_words:
            close_group()
        current.append(s)
    close_group()

    passages = []
    page_title = source.title or page.title
    for n, (heading, text) in enumerate(groups, start=1):
        title = f"{page_title} – {heading}" if page_title and heading else (page_title or heading)
        passages.append(Passage(id=f"{source.id}#{n}", lang=source.lang, source_url=source.url,
                                body=text, title=title, published=page.updated))
    return passages


# --- Getting the page ------------------------------------------------------------------

Opener = Callable[[urllib.request.Request, float], tuple[int, bytes, str]]


def _urlopen(req: urllib.request.Request, timeout: float) -> tuple[int, bytes, str]:
    """(status, body, content type). HTTP errors come back as a status, not raised."""
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            return r.status, r.read(MAX_PAGE_BYTES + 1), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        return err.code, b"", ""


class Fetcher:
    """Downloads pages one at a time, asking each site's robots.txt first."""

    def __init__(self, opener: Opener = _urlopen, delay: float = 1.0, timeout: float = 30,
                 sleep: Callable[[float], None] = time.sleep):
        self.opener, self.delay, self.timeout, self.sleep = opener, delay, timeout, sleep
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last = 0.0

    def _get(self, url: str) -> tuple[int, bytes, str]:
        wait = self.last + self.delay - time.monotonic()
        if self.last and wait > 0:
            self.sleep(wait)
        try:
            return self.opener(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                               self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise IngestError(f"{url}: could not connect ({err}).") from None
        finally:
            self.last = time.monotonic()

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
            status, body, _ = self._get(host + "/robots.txt")
            if status in (401, 403):
                rp.disallow_all = True           # as robotparser itself treats these
            elif status >= 400:
                rp.allow_all = True              # no robots.txt: nothing is off limits
            else:
                rp.parse(body.decode("utf-8", "replace").splitlines())
            self.robots[host] = rp
        return self.robots[host].can_fetch(USER_AGENT, url)

    def fetch(self, url: str) -> str:
        if not self._allowed(url):
            raise IngestError(f"{url}: the site's robots.txt does not allow automated "
                              "downloads. Save the page from a browser and add \"file\".")
        status, body, ctype = self._get(url)
        if status in (401, 403, 429):
            raise IngestError(f"{url}: the site refused the download (HTTP {status}). "
                              "Save the page from a browser and add \"file\".")
        if status >= 400:
            raise IngestError(f"{url}: HTTP {status}. Check the address.")
        if len(body) > MAX_PAGE_BYTES:
            raise IngestError(f"{url}: page is larger than {MAX_PAGE_BYTES // 1_000_000} MB.")
        if "html" not in ctype.lower():
            raise IngestError(f"{url}: not an HTML page ({ctype or 'unknown type'}).")
        charset = re.search(r"charset=([\w-]+)", ctype)
        return body.decode(charset.group(1) if charset else "utf-8", "replace")


def read_source(source: Source, fetcher: Fetcher, root: Path = PROJECT_ROOT) -> str:
    """The page's HTML: the saved copy when there is one, else a download."""
    if source.file:
        path = Path(source.file)
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise IngestError(f"{source.id}: saved page {path} not found.")
        return path.read_text(encoding="utf-8", errors="replace")
    return fetcher.fetch(source.url)


# --- Building --------------------------------------------------------------------------

MIN_PAGE_WORDS = 40   # less than this usually means the text is built by JavaScript


@dataclass
class Result:
    source: Source
    passages: list[Passage] = field(default_factory=list)
    words: int = 0
    error: str = ""


def build(sources: Sequence[Source], fetcher: Fetcher, root: Path = PROJECT_ROOT,
          max_words: int = 350, overlap: int = 50,
          progress: Callable[[Result], None] | None = None) -> list[Result]:
    """Passages for every source. A source that fails gets an ``error``, not an exception."""
    results = []
    for src in sources:
        r = Result(src)
        try:
            page = extract_page(read_source(src, fetcher, root))
            r.words = page.words
            if r.words < MIN_PAGE_WORDS:
                raise IngestError(f"{src.url}: only {r.words} words of text. The page may be "
                                  "built by JavaScript; save it from a browser and add \"file\".")
            r.passages = passages_from_page(src, page, max_words, overlap)
        except IngestError as err:
            r.error = str(err)
        results.append(r)
        if progress:
            progress(r)
    return results


def write_passages(passages: Iterable[Passage], out: Path, sources_path: Path) -> int:
    passages = list(passages)
    _check_unique(passages)
    try:
        sources_name = sources_path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        sources_name = sources_path
    doc = {
        "_note": (f"Built by disaster_aid.kb_ingest from {sources_name} on "
                  f"{date.today().isoformat()}. Text from public U.S. government pages; "
                  "each passage keeps its source_url. Rebuild instead of editing by hand."),
        "passages": [p.to_dict() for p in passages],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(passages)


def _report(r: Result) -> None:
    if r.error:
        print(f"  FAILED {r.source.id}: {r.error}", file=sys.stderr)
    else:
        print(f"  ok     {r.source.id}: {r.words} words -> {len(r.passages)} passages")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m disaster_aid.kb_ingest",
                                     description="Build knowledge-base passages from help pages.")
    parser.add_argument("command", choices=["build", "preview"])
    parser.add_argument("ids", nargs="*", help="source ids (default: all)")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-words", type=int, default=350)
    parser.add_argument("--skip-failed", action="store_true",
                        help="write the file even if some sources failed (their passages are left out)")
    args = parser.parse_args(argv)

    sources = load_sources(args.sources)
    if args.ids:
        unknown = sorted(set(args.ids) - {s.id for s in sources})
        if unknown:
            parser.error(f"unknown source ids: {', '.join(unknown)}")
        sources = [s for s in sources if s.id in args.ids]
    elif args.command == "preview":
        parser.error("preview needs one or more source ids.")

    results = build(sources, Fetcher(), max_words=args.max_words,
                    progress=_report if args.command == "build" else None)
    failed = [r for r in results if r.error]

    if args.command == "preview":
        for r in results:
            if r.error:
                print(f"FAILED {r.source.id}: {r.error}")
            for p in r.passages:
                print(f"=== {p.id}  [{p.lang}]  {len(p.body.split())} words  published={p.published}")
                print(f"title: {p.title}\n{p.body}\n")
        sys.exit(1 if failed else 0)

    if failed and not args.skip_failed:
        sys.exit(f"{len(failed)} of {len(results)} sources failed; nothing written. "
                 "Fix them, or rerun with --skip-failed.")
    if args.ids and not args.skip_failed:
        print("Note: this file holds only the sources you named. Loading it replaces everything "
              "in the database.", file=sys.stderr)
    n = write_passages((p for r in results for p in r.passages), args.out, args.sources)
    print(f"Wrote {n} passages from {len(results) - len(failed)} sources to {args.out}.")


if __name__ == "__main__":
    main()
