"""Faculty list (from the PM&R publications site dropdown).

PubMed indexes most authors as "LastName Initials" (e.g. "Boninger ML"), so a search on the
full first name misses most papers. Each entry therefore has:

  pubmed_author  The author term used in the search, e.g. "Boninger M" (matches Boninger M,
                 Boninger ML, ...). Add more initials to disambiguate a common name.
  scope          How strictly to filter by affiliation:
                   "pitt" -> author AND Pittsburgh[Affiliation]                       (default)
                   "pmr"  -> ... AND (Physical Medicine OR Rehabilitation)[Affiliation]
                             (for common names that collide with other Pitt researchers)
                   "none" -> author only (for distinctive names with few/no Pitt-tagged papers)
  query          Optional raw PubMed query that overrides everything above.

The scopes below were chosen by checking PubMed hit counts and sample titles in Nov 2026.
After running ingest.py, review data/ingest_report.txt and adjust anything that looks off.

`title` can be filled in by hand; `research_interests` is generated automatically from each
person's publications during ingestion (MeSH terms + author keywords).
"""

FACULTY = [
    {"name": "Ted Allaire Barrios", "pubmed_author": "Barrios TA", "scope": "none"},
    {"name": "Allison Bean", "pubmed_author": "Bean AC", "scope": "pitt"},
    {"name": "Corina Bondi", "pubmed_author": "Bondi CO", "scope": "pitt"},
    {"name": "Michael Boninger", "pubmed_author": "Boninger M", "scope": "pitt"},
    {"name": "Jorge L. Candiotti", "pubmed_author": "Candiotti J", "scope": "pitt"},
    {"name": "Aileen Chou", "pubmed_author": "Chou A", "scope": "pmr"},
    {"name": "Jennifer Collinger", "pubmed_author": "Collinger J", "scope": "pitt"},
    {"name": "Rory A. Cooper", "pubmed_author": "Cooper RA", "scope": "pitt"},
    {"name": "Brandon Daveler", "pubmed_author": "Daveler B", "scope": "pitt"},
    {"name": "Brad Dicianno", "pubmed_author": "Dicianno BE", "scope": "pitt"},
    {"name": "Jonathan Duvall", "pubmed_author": "Duvall J", "scope": "pitt"},
    {"name": "Lee Fisher", "pubmed_author": "Fisher LE", "scope": "pitt"},
    {"name": "Robert Gaunt", "pubmed_author": "Gaunt RA", "scope": "pitt"},
    {"name": "Amy Houtrow", "pubmed_author": "Houtrow A", "scope": "pitt"},
    {"name": "Jessica Jarvis", "pubmed_author": "Jarvis JM", "scope": "pitt"},
    {"name": "Anthony Kline", "pubmed_author": "Kline AE", "scope": "pitt"},
    {"name": "Renée Lajiness-O'Neill", "pubmed_author": "Lajiness-O'Neill R", "scope": "none"},
    {"name": "Gina McKernan", "pubmed_author": "McKernan G", "scope": "pitt"},
    {"name": "Brendan McNeish", "pubmed_author": "McNeish BL", "scope": "none"},
    {"name": "Bailey Petersen", "pubmed_author": "Petersen BA", "scope": "pmr"},
    {"name": "Elvira Pirondini", "pubmed_author": "Pirondini E", "scope": "pitt"},
    {"name": "Amrita Sahu", "pubmed_author": "Sahu A", "scope": "pmr"},
    {"name": "Gwendolyn Sowa", "pubmed_author": "Sowa G", "scope": "pitt"},
    {"name": "Amery Treble-Barna", "pubmed_author": "Treble-Barna A", "scope": "pitt"},
    {"name": "Amy Wagner", "pubmed_author": "Wagner AK", "scope": "pitt"},
    {"name": "Lynn Worobey", "pubmed_author": "Worobey L", "scope": "pitt"},
    {"name": "Christina Zigler", "pubmed_author": "Zigler CK", "scope": "none"},
]

for _f in FACULTY:
    _f.setdefault("title", "")
    _f.setdefault("research_interests", "")
    _f.setdefault("scope", "pitt")
