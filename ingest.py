"""Pull PM&R faculty publications from PubMed into SQLite.

Usage:  python ingest.py            # all faculty
        python ingest.py --only "Boninger"   # one person (for testing a query)

Steps: esearch per faculty -> efetch in batches of 200 -> parse XML -> deduplicate by PMID
-> store in data/publications.db -> build per-faculty research profiles by frequency analysis.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

import requests

import config
from faculty import FACULTY
from utils import match_faculty, slug, tag_topics

# MeSH terms that describe study design / demographics rather than research topics.
GENERIC_TERMS = {
    "humans", "male", "female", "adult", "middle aged", "aged", "aged, 80 and over", "young adult",
    "adolescent", "child", "child, preschool", "infant", "animals", "rats", "mice", "rats, sprague-dawley",
    "retrospective studies", "prospective studies", "cohort studies", "cross-sectional studies",
    "follow-up studies", "treatment outcome", "time factors", "surveys and questionnaires",
    "reproducibility of results", "pilot projects", "united states", "pennsylvania", "case-control studies",
    "risk factors", "longitudinal studies", "disease models, animal", "mice, inbred c57bl", "rehabilitation",
    "single-blind method", "double-blind method", "random allocation", "severity of illness index",
    "sex factors", "age factors", "cross-over studies", "feasibility studies", "logistic models",
    "regression analysis", "linear models", "incidence", "prevalence", "registries", "databases, factual",
}

session = requests.Session()
_last_request = 0.0


def eutils(endpoint: str, params: dict, method: str = "GET") -> requests.Response:
    """Rate-limited call to NCBI E-utilities with simple retry."""
    global _last_request
    params = {**params, "tool": config.NCBI_TOOL}
    if config.NCBI_EMAIL:
        params["email"] = config.NCBI_EMAIL
    if config.NCBI_API_KEY:
        params["api_key"] = config.NCBI_API_KEY
    url = config.PUBMED_BASE_URL + endpoint
    for attempt in range(5):
        wait = config.PUBMED_REQUEST_DELAY - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            if method == "POST":
                r = session.post(url, data=params, timeout=60)
            else:
                r = session.get(url, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.RequestException, requests.HTTPError) as e:
            backoff = 2 ** attempt
            print(f"    ! {endpoint} failed ({e}); retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
    raise RuntimeError(f"PubMed request to {endpoint} failed after retries")


def build_query(fac: dict) -> str:
    if fac.get("query"):
        return fac["query"]
    q = f"{fac['pubmed_author']}[Author]"
    if fac["scope"] in ("pitt", "pmr"):
        q += f' AND "{config.PUBMED_AFFILIATION}"[Affiliation]'
    if fac["scope"] == "pmr":
        q += " AND (" + " OR ".join(f'"{t}"[Affiliation]' for t in config.PUBMED_DEPARTMENT_TERMS) + ")"
    return q


def search_pmids(query: str) -> tuple:
    r = eutils("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json",
                                "retmax": config.MAX_PUBS_PER_FACULTY, "sort": "pub_date"})
    res = r.json()["esearchresult"]
    return int(res.get("count", 0)), res.get("idlist", [])


def text_of(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_year(article) -> int | None:
    for path in ("MedlineCitation/Article/Journal/JournalIssue/PubDate/Year",
                 "MedlineCitation/Article/ArticleDate/Year",
                 "PubmedData/History/PubMedPubDate[@PubStatus='pubmed']/Year"):
        el = article.find(path)
        if el is not None and el.text and el.text[:4].isdigit():
            return int(el.text[:4])
    md = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate")
    if md is not None and md.text and md.text[:4].isdigit():
        return int(md.text[:4])
    return None


def parse_article(article) -> dict | None:
    mc = article.find("MedlineCitation")
    art = mc.find("Article") if mc is not None else None
    if art is None:
        return None
    pmid = mc.findtext("PMID")
    pub_types = [text_of(p) for p in art.findall("PublicationTypeList/PublicationType")]
    if any(t in ("Published Erratum", "Retraction of Publication") for t in pub_types):
        return None

    abstract_parts = []
    for ab in art.findall("Abstract/AbstractText"):
        label = ab.get("Label")
        t = text_of(ab)
        if t:
            abstract_parts.append(f"{label}: {t}" if label and label.upper() != "UNLABELLED" else t)

    authors = []
    for a in art.findall("AuthorList/Author"):
        if a.find("CollectiveName") is not None:
            continue
        authors.append({
            "last": a.findtext("LastName", ""),
            "fore": a.findtext("ForeName", ""),
            "initials": a.findtext("Initials", ""),
            "affiliation": text_of(a.find("AffiliationInfo/Affiliation")),
        })

    doi = ""
    for aid in article.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip()
    if not doi:
        for eid in art.findall("ELocationID"):
            if eid.get("EIdType") == "doi":
                doi = (eid.text or "").strip()

    mesh = [text_of(m.find("DescriptorName")) for m in mc.findall("MeshHeadingList/MeshHeading")]
    keywords = [text_of(k) for k in mc.findall("KeywordList/Keyword") if text_of(k)]
    journal = text_of(art.find("Journal/Title")) or text_of(art.find("Journal/ISOAbbreviation"))

    return {
        "pmid": pmid,
        "title": text_of(art.find("ArticleTitle")).rstrip("."),
        "abstract": "\n".join(abstract_parts),
        "authors": authors,
        "journal": journal,
        "year": parse_year(article),
        "doi": doi,
        "mesh_terms": mesh,
        "keywords": keywords,
        "pub_types": pub_types,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def fetch_records(pmids: list, fac_slug: str) -> list:
    records = []
    for i in range(0, len(pmids), config.PUBMED_FETCH_BATCH):
        batch = pmids[i:i + config.PUBMED_FETCH_BATCH]
        r = eutils("efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}, method="POST")
        with open(os.path.join(config.RAW_XML_DIR, f"{fac_slug}_{i // config.PUBMED_FETCH_BATCH}.xml"), "wb") as f:
            f.write(r.content)
        root = ET.fromstring(r.content)
        for article in root.findall("PubmedArticle"):
            rec = parse_article(article)
            if rec:
                records.append(rec)
    return records


SCHEMA = """
DROP TABLE IF EXISTS publications;
DROP TABLE IF EXISTS faculty;
DROP TABLE IF EXISTS meta;
CREATE TABLE publications (
    pmid TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    authors TEXT,          -- JSON list of {last, fore, initials, affiliation}
    faculty_authors TEXT,  -- JSON list of department faculty on this paper
    journal TEXT,
    year INTEGER,
    doi TEXT,
    mesh_terms TEXT,       -- JSON list
    keywords TEXT,         -- JSON list (author keywords)
    topics TEXT,           -- JSON list of config.TOPICS keys
    pub_types TEXT,        -- JSON list
    pubmed_url TEXT
);
CREATE TABLE faculty (
    name TEXT PRIMARY KEY,
    slug TEXT,
    title TEXT,
    research_interests TEXT,
    publication_count INTEGER,
    first_year INTEGER,
    last_year INTEGER,
    top_topics TEXT,        -- JSON list of [term, count]
    topic_counts TEXT,      -- JSON {topic_key: count}
    top_coauthors TEXT,     -- JSON list of [faculty name, shared papers]
    pubmed_query TEXT,
    search_hits INTEGER
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX idx_pub_year ON publications(year);
"""


def build_profiles(pubs: dict, search_info: dict) -> list:
    profiles = []
    for fac in FACULTY:
        mine = [p for p in pubs.values() if fac["name"] in p["faculty_authors"]]
        terms = Counter()
        topic_counts = Counter()
        coauthors = Counter()
        for p in mine:
            seen = set()
            for t in p["mesh_terms"] + p["keywords"]:
                key = t.strip().lower()
                if key and key not in GENERIC_TERMS and key not in seen:
                    seen.add(key)
                    terms[t.strip()] += 1
            topic_counts.update(p["topics"])
            coauthors.update(n for n in p["faculty_authors"] if n != fac["name"])
        # merge case variants, keep the most common spelling
        merged = Counter()
        spelling = {}
        for t, c in terms.items():
            k = t.lower()
            merged[k] += c
            if k not in spelling or terms[spelling[k]] < c:
                spelling[k] = t
        top = [(spelling[k], c) for k, c in merged.most_common(15)]
        years = [p["year"] for p in mine if p["year"]]
        profiles.append({
            "name": fac["name"],
            "slug": slug(fac["name"]),
            "title": fac.get("title", ""),
            "research_interests": fac.get("research_interests") or "; ".join(t for t, _ in top[:8]),
            "publication_count": len(mine),
            "first_year": min(years) if years else None,
            "last_year": max(years) if years else None,
            "top_topics": json.dumps(top),
            "topic_counts": json.dumps(dict(topic_counts.most_common())),
            "top_coauthors": json.dumps(coauthors.most_common(8)),
            "pubmed_query": search_info[fac["name"]]["query"],
            "search_hits": search_info[fac["name"]]["count"],
        })
    return profiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring of a faculty name; only ingest matching faculty (dry run, no DB write)")
    args = ap.parse_args()

    os.makedirs(config.RAW_XML_DIR, exist_ok=True)
    faculty = [f for f in FACULTY if not args.only or args.only.lower() in f["name"].lower()]

    pubs: dict = {}
    search_info: dict = {}
    report = []
    start = time.time()
    for i, fac in enumerate(faculty, 1):
        query = build_query(fac)
        count, pmids = search_pmids(query)
        search_info[fac["name"]] = {"query": query, "count": count}
        flag = "  <-- REVIEW: unusually many results, name may be ambiguous" if count > config.REVIEW_THRESHOLD else ""
        flag = flag or ("  <-- REVIEW: no results" if count == 0 else "")
        line = f"[{i:2}/{len(faculty)}] {fac['name']:<28} {count:>5} hits   {query}{flag}"
        print(line, flush=True)
        report.append(line)

        new_ids = [p for p in pmids if p not in pubs]
        for rec in fetch_records(new_ids, slug(fac["name"])) if new_ids else []:
            pubs[rec["pmid"]] = rec
        for pid in pmids:
            if pid in pubs:
                pubs[pid].setdefault("_searched_for", set()).add(fac["name"])

    # Faculty attribution: whoever we searched for + any other faculty found in the author list
    for p in pubs.values():
        names = set(p.pop("_searched_for", set())) | set(match_faculty(p["authors"]))
        p["faculty_authors"] = [f["name"] for f in FACULTY if f["name"] in names]
        p["topics"] = tag_topics(" ".join([p["title"], p["abstract"], " ".join(p["mesh_terms"]),
                                           " ".join(p["keywords"])]))

    print(f"\n{len(pubs)} unique publications in {time.time() - start:.0f}s")
    if args.only:
        for p in sorted(pubs.values(), key=lambda x: -(x["year"] or 0))[:15]:
            print(f"  {p['year']}  {p['title'][:90]}")
        print("(--only is a dry run; database not modified)")
        return

    if not pubs:
        sys.exit("No publications found; not overwriting the database.")

    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO publications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(p["pmid"], p["title"], p["abstract"], json.dumps(p["authors"]), json.dumps(p["faculty_authors"]),
          p["journal"], p["year"], p["doi"], json.dumps(p["mesh_terms"]), json.dumps(p["keywords"]),
          json.dumps(p["topics"]), json.dumps(p["pub_types"]), p["pubmed_url"]) for p in pubs.values()],
    )
    profiles = build_profiles(pubs, search_info)
    cols = list(profiles[0].keys())
    conn.executemany(f"INSERT INTO faculty ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [tuple(p[c] for c in cols) for p in profiles])
    conn.execute("INSERT INTO meta VALUES ('last_updated', ?)", (datetime.now(timezone.utc).isoformat(),))
    conn.execute("INSERT INTO meta VALUES ('publication_count', ?)", (str(len(pubs)),))
    conn.commit()
    conn.close()

    report.append("")
    for p in profiles:
        report.append(f"{p['name']:<28} {p['publication_count']:>4} pubs ({p['first_year']}-{p['last_year']})  "
                      f"{p['research_interests'][:100]}")
    with open(os.path.join(config.DATA_DIR, "ingest_report.txt"), "w") as f:
        f.write("\n".join(report) + "\n")
    print(f"Saved to {config.SQLITE_PATH}; review data/ingest_report.txt")


if __name__ == "__main__":
    main()
