"""Small helpers shared by ingest.py, embed.py, query.py and app.py."""
import json
import re
import sqlite3
import unicodedata

import config
from faculty import FACULTY


def norm(text: str) -> str:
    """Lowercase, strip accents and non-alphanumerics: "Lajiness-O'Neill" -> "lajinessoneill"."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def slug(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", norm_spaces(name))).strip("_")


def norm_spaces(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return text.lower()


def faculty_key(fac: dict) -> tuple:
    """("boninger", "M") from pubmed_author "Boninger M"."""
    parts = fac["pubmed_author"].rsplit(" ", 1)
    last, initials = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")
    return norm(last), initials.upper()


def last_name(full_name: str) -> str:
    return full_name.replace(".", "").split()[-1]


def match_faculty(authors: list) -> list:
    """Return names of our faculty who appear in a PubMed author list.

    `authors` items are dicts with "last" and "initials" keys.
    """
    found = []
    for fac in FACULTY:
        flast, finit = faculty_key(fac)
        for a in authors:
            if norm(a.get("last", "")) == flast and (a.get("initials", "") or "").upper().startswith(finit):
                found.append(fac["name"])
                break
    return found


_TOPIC_PATTERNS = {
    key: re.compile(
        "|".join(r"\b" + re.escape(k) + (r"\b" if len(k) <= 4 else "") for k in t["keywords"]),
        re.IGNORECASE,
    )
    for key, t in config.TOPICS.items()
}


def tag_topics(text: str) -> list:
    return [key for key, pat in _TOPIC_PATTERNS.items() if pat.search(text or "")]


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_pub(row) -> dict:
    d = dict(row)
    for k in ("authors", "faculty_authors", "mesh_terms", "keywords", "topics"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                d[k] = []
    return d


def short_authors(authors: list, limit: int = 6) -> str:
    names = [a if isinstance(a, str) else f"{a.get('last', '')} {a.get('initials', '')}".strip() for a in authors]
    if len(names) > limit:
        return ", ".join(names[:limit]) + ", et al."
    return ", ".join(names)
