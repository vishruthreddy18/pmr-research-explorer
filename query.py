"""RAG engine: query preprocessing -> retrieval (ChromaDB + SQLite) -> LLM generation."""
import json
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from datetime import date

import config
from faculty import FACULTY
from utils import db_connect, last_name, norm_spaces, row_to_pub, short_authors, slug, tag_topics

SYSTEM_PROMPT = """You answer questions about research in the Department of Physical Medicine & \
Rehabilitation at the University of Pittsburgh. The people asking are prospective students, \
faculty candidates and possible collaborators. Write like a well-informed colleague giving a \
quick, friendly answer in a hallway conversation, not like a report.

Content rules:
- Use ONLY the publication data below. Never invent papers, grants, titles or contact details. \
If the data doesn't cover the question, say so in one sentence.
- Cite a paper by putting its reference number in brackets right after the claim, like this: [3]. \
Don't write out authors, journals or years in citations; the reader sees full citation cards \
below your answer. Only use numbers that appear in the context.
- Write faculty by their full name the first time (e.g. Jennifer Collinger), then by last name.

Style rules:
- Lead with the direct answer in the first sentence. No preamble ("Based on the data...", \
"Great question"), no restating the question, no closing offer or summary.
- Keep it short: usually 80-180 words. Only go longer if the user asks for detail or a comparison.
- Plain, specific language. Say what the person actually studies ("records from electrodes in the \
motor cortex so people with paralysis can control a robotic arm"), not jargon lists \
("biomechanical phenomena, equipment design, ergonomics").
- When recommending people, name 2-4 of the best matches, one or two sentences each on why, \
grounded in a specific paper. Don't list everyone loosely related.
- Formatting: short paragraphs. A simple bullet list is fine for 3+ people or items. No headings, \
no bold, no nested bullets, no horizontal rules. For comparisons, one short paragraph per person \
and a closing sentence on how they differ or overlap.
- Don't speak for the department ("we", "our"), and don't use marketing words like "cutting-edge", \
"world-class", "robust", "extensive", "leverages", "spearheads".

For broad questions about the department's strengths or themes, give 4-6 themes in one bullet \
each, naming the one or two faculty most associated with each.

Today's date is {today}.

Context from department publications:
{retrieved_chunks}"""

OVERVIEW_RE = re.compile(
    r"\b(strengths?|themes?|overview|main areas|focus areas|biggest|major research|research areas|"
    r"what (does|do) (the|this) department|department'?s research|summari[sz]e|landscape|all faculty|"
    r"everyone|who are the faculty|list (the|all)? ?faculty)\b", re.I)
RECENT_RE = re.compile(r"\b(recent|latest|newest|new work|current work|lately|these days|nowadays)\b", re.I)
FOLLOWUP_RE = re.compile(r"\b(her|his|their|them|they|she|he|this person|that person|these|those|"
                         r"it|that paper|more)\b", re.I)


class TTLCache:
    def __init__(self, size: int, ttl: int):
        self.size, self.ttl = size, ttl
        self.data: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if not item or time.time() - item[0] > self.ttl:
                return None
            self.data.move_to_end(key)
            return item[1]

    def set(self, key, value):
        with self.lock:
            self.data[key] = (time.time(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


class RAGEngine:
    def __init__(self):
        self.conn = db_connect()
        self._collection = None
        self._llm = None
        self.cache = TTLCache(config.CACHE_SIZE, config.CACHE_TTL_SECONDS)
        self.faculty_rows = {r["name"]: dict(r) for r in self.conn.execute("SELECT * FROM faculty")}
        self.meta = {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM meta")}
        # Patterns for detecting faculty mentions: full name (any case) or capitalised last name.
        self._name_patterns = []
        for f in FACULTY:
            ln = last_name(f["name"])
            first = f["name"].split()[0]
            full = re.compile(r"\b" + re.escape(norm_spaces(first)) + r"\b.{0,15}\b"
                              + re.escape(norm_spaces(ln)) + r"\b", re.I)
            last = re.compile(r"\b" + re.escape(norm_spaces(ln)) + r"\b")  # case-sensitive
            alt = None
            if "-" in ln:  # "Treble-Barna" also matches "Treble Barna"
                alt = re.compile(r"\b" + re.escape(norm_spaces(ln).replace("-", " ")) + r"\b", re.I)
            self._name_patterns.append((f["name"], full, re.compile(last.pattern, re.I), last, alt))

    # ------------------------------------------------------------------ helpers
    @property
    def collection(self):
        if self._collection is None:
            from embed import get_collection
            self._collection = get_collection()
        return self._collection

    def detect_faculty(self, text: str, strict_case: bool = True) -> list:
        t = norm_spaces(text)
        t_cased = re.sub(r"[^\x00-\x7f]", "", text)
        found = []
        for name, full, last_ci, last_cs, alt in self._name_patterns:
            ln_lower = norm_spaces(last_name(name))
            hit = full.search(t) or (alt and alt.search(t))
            if not hit:
                # Capitalised surname ("Cooper", "Dr. Kline"), or any case if the query uses "Dr."
                hit = last_cs.search(t_cased) or re.search(r"\bdr\.?\s+" + re.escape(ln_lower) + r"\b", t)
                if not hit and not strict_case:
                    hit = last_ci.search(t)
            if hit:
                found.append((hit.start(), name))
        return [n for _, n in sorted(found)]

    @staticmethod
    def detect_years(text: str) -> tuple:
        this_year = date.today().year
        t = text.lower()
        m = re.search(r"(?:between|from)\s+((?:19|20)\d{2})\s+(?:and|to|-|–)\s+((?:19|20)\d{2})", t) \
            or re.search(r"\b((?:19|20)\d{2})\s*(?:-|–|to)\s*((?:19|20)\d{2})\b", t)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            return a, b
        m = re.search(r"\b(?:last|past|previous)\s+(\d+|two|three|four|five|ten)\s+years?\b", t)
        if m:
            words = {"two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
            n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
            return this_year - n, None
        m = re.search(r"\b(after|since|from|post)\s+((?:19|20)\d{2})\b", t)
        if m:
            y = int(m.group(2))
            return (y + 1 if m.group(1) in ("after", "post") else y), None
        m = re.search(r"\b(before|prior to|until|pre)\s+((?:19|20)\d{2})\b", t)
        if m:
            y = int(m.group(2))
            return None, (y - 1 if m.group(1) in ("before", "prior to", "pre") else y)
        m = re.search(r"\b(?:in|during|of)\s+((?:19|20)\d{2})\b", t)
        if m:
            y = int(m.group(1))
            return y, y
        return None, None

    @staticmethod
    def _where(*conds):
        conds = [c for c in conds if c]
        if not conds:
            return None
        return conds[0] if len(conds) == 1 else {"$and": conds}

    def _pubs_by_pmid(self, pmids: list) -> dict:
        if not pmids:
            return {}
        rows = self.conn.execute(f"SELECT * FROM publications WHERE pmid IN ({','.join('?' * len(pmids))})",
                                 pmids).fetchall()
        return {r["pmid"]: row_to_pub(r) for r in rows}

    def _vector_search(self, text: str, n: int, where: dict | None) -> list:
        try:
            res = self.collection.query(query_texts=[text], n_results=n, where=where)
        except Exception as e:  # e.g. filter matches nothing
            print(f"vector search failed: {e}")
            return []
        out = []
        for id_, meta, dist in zip(res["ids"][0], res["metadatas"][0], res["distances"][0]):
            # Papers without abstracts (editorials, meeting notes) have short chunks that match
            # generic questions too easily, so they get a small penalty.
            penalty = 0.0 if meta.get("has_abstract", True) else 0.08
            out.append({"id": id_, "meta": meta, "distance": dist, "score": dist + penalty})
        return out

    @classmethod
    def _merge(cls, hits: list, boosted: list, n: int) -> list:
        """Reserve at least half the slots for papers tagged with a topic named in the question."""
        top_boosted = cls._rerank(boosted, (n + 1) // 2)
        rest = [p for p in cls._rerank(hits + boosted, n * 2) if p not in top_boosted]
        return (top_boosted + rest)[:n]

    @staticmethod
    def _rerank(hits: list, n: int) -> list:
        best: dict = {}
        for h in hits:
            pmid = h["meta"].get("pmid")
            if pmid and (pmid not in best or h["score"] < best[pmid]):
                best[pmid] = h["score"]
        return [p for p, _ in sorted(best.items(), key=lambda kv: kv[1])[:n]]

    _META_WORDS = set("""a an the and or of in on for to with about by from me my i we you your is are was were be
        show tell give list find what which who whom whose how why when where does do did has have had their his her
        dr dr. doctor prof professor compare comparison contrast versus vs between research focus work works working
        recent latest newest new most papers paper publications publication published publish studies study
        overview summary summarize interests interest areas area main primary current lately lab group please
        can could would should someone anyone department faculty member members pitt pmr after before since years
        year last past like similar differ difference differences""".split())

    def _topical_text(self, query: str) -> str:
        """Query with faculty names and filler words removed; '' if nothing topical is left."""
        t = query
        for f in FACULTY:
            for part in f["name"].replace(".", "").split():
                if len(part) > 1:
                    t = re.sub(r"\b" + re.escape(part) + r"(?:'s)?\b", " ", t, flags=re.I)
        t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", t)
                 if re.sub(r"'s$", "", w.lower()) not in self._META_WORDS]
        return " ".join(words)

    # ---------------------------------------------------------------- retrieval
    def retrieve(self, query: str, faculty: list, years: tuple, topics: list, recent: bool,
                 overview: bool) -> tuple:
        """Return (publications, faculty_profile_names)."""
        y0, y1 = years
        year_cond = []
        if y0:
            year_cond.append({"year": {"$gte": y0}})
        if y1:
            year_cond.append({"year": {"$lte": y1}})
        topic_cond = None
        if topics:
            tc = [{f"topic_{t}": True} for t in topics]
            topic_cond = tc[0] if len(tc) == 1 else {"$or": tc}
        pub_type = {"doc_type": "publication"}

        pmids: list = []

        def add(ids):
            for p in ids:
                if p not in pmids:
                    pmids.append(p)

        # Topics mentioned in the question ("machine learning", "wheelchair") get a boosted
        # second search restricted to papers tagged with that topic.
        query_topics = [] if topics else tag_topics(query)
        boost_cond = None
        if query_topics:
            bc = [{f"topic_{t}": True} for t in query_topics]
            boost_cond = bc[0] if len(bc) == 1 else {"$or": bc}

        if faculty:
            per = max(4, config.TOP_K_RESULTS // len(faculty)) if len(faculty) > 1 else config.TOP_K_RESULTS
            topical = self._topical_text(query)
            for name in faculty:
                if recent and not topics and not query_topics:
                    sql = "SELECT pmid FROM publications WHERE faculty_authors LIKE ?"
                    args = [f'%"{name}"%']
                    if y0:
                        sql += " AND year >= ?"; args.append(y0)
                    if y1:
                        sql += " AND year <= ?"; args.append(y1)
                    sql += " ORDER BY year DESC, pmid DESC LIMIT ?"
                    args.append(per)
                    add([r["pmid"] for r in self.conn.execute(sql, args)])
                else:
                    # A question with no real topic ("compare X and Y") searches with the
                    # person's own research profile so we get representative papers.
                    text = topical or (self.faculty_rows.get(name, {}).get("research_interests") or query)
                    fac_cond = {f"fac_{slug(name)}": True}
                    hits = self._vector_search(text, per * 2, self._where(pub_type, fac_cond, *year_cond, topic_cond))
                    boosted = self._vector_search(text, per, self._where(pub_type, fac_cond, *year_cond, boost_cond)) \
                        if boost_cond else []
                    add(self._merge(hits, boosted, per))
            profiles = list(faculty)
        else:
            n = config.TOP_K_RESULTS + (5 if recent else 0)
            hits = self._vector_search(query, n * 2, self._where(pub_type, *year_cond, topic_cond))
            boosted = self._vector_search(query, n, self._where(pub_type, *year_cond, boost_cond)) if boost_cond else []
            add(self._merge(hits, boosted, n))
            if overview:
                profiles = list(self.faculty_rows)
            else:
                # Faculty whose profiles match the question, plus those who appear most often on
                # the matching papers (so the field's main people are never left out).
                fac_hits = self._vector_search(query, 4, {"doc_type": "faculty"})
                profiles = [h["meta"]["faculty_authors"] for h in fac_hits]
                on_papers = Counter(n for p in self._pubs_by_pmid(pmids).values() for n in p["faculty_authors"])
                for name, _ in on_papers.most_common(4):
                    if name not in profiles:
                        profiles.append(name)

        pubs_map = self._pubs_by_pmid(pmids)
        pubs = [pubs_map[p] for p in pmids if p in pubs_map]
        if recent:
            pubs.sort(key=lambda p: -(p["year"] or 0))
        return pubs, profiles

    # ------------------------------------------------------------------ context
    def faculty_profile_text(self, name: str) -> str:
        f = self.faculty_rows.get(name)
        if not f:
            return ""
        top = json.loads(f["top_topics"] or "[]")
        co = json.loads(f["top_coauthors"] or "[]")
        recent = self.conn.execute(
            "SELECT title, year FROM publications WHERE faculty_authors LIKE ? ORDER BY year DESC LIMIT 3",
            (f'%"{name}"%',)).fetchall()
        lines = [f"- {name}" + (f", {f['title']}" if f["title"] else "") +
                 f": {f['publication_count']} publications in database ({f['first_year']}-{f['last_year']}). "
                 f"Key topics: {', '.join(t for t, _ in top[:10]) or 'n/a'}."]
        if co:
            lines.append(f"  Frequent department co-authors: {', '.join(f'{n} ({c})' for n, c in co[:4])}.")
        if recent:
            lines.append("  Most recent: " + "; ".join(f"{r['title']} ({r['year']})" for r in recent))
        return "\n".join(lines)

    def department_overview_text(self) -> str:
        topic_counts = Counter()
        for r in self.conn.execute("SELECT topics FROM publications"):
            topic_counts.update(json.loads(r["topics"] or "[]"))
        total = self.meta.get("publication_count", "?")
        yr = self.conn.execute("SELECT MIN(year), MAX(year) FROM publications WHERE year > 0").fetchone()
        parts = [f"Department-wide statistics: {total} publications ({yr[0]}-{yr[1]}) "
                 f"by {len(self.faculty_rows)} faculty."]
        parts.append("Publications per research area (a paper can be in several): " + ", ".join(
            f"{config.TOPICS[k]['label']} {c}" for k, c in topic_counts.most_common()))
        return "\n".join(parts)

    def build_context(self, pubs: list, profiles: list, overview: bool) -> str:
        blocks = []
        if overview:
            blocks.append(self.department_overview_text())
        if profiles:
            blocks.append("FACULTY PROFILES:\n" + "\n".join(self.faculty_profile_text(n) for n in profiles))
        if pubs:
            pub_lines = []
            for i, p in enumerate(pubs, 1):
                abstract = (p["abstract"] or "(no abstract available)")
                if len(abstract) > 1400:
                    abstract = abstract[:1400].rsplit(" ", 1)[0] + "..."
                pub_lines.append(
                    f"[{i}] {p['title']}\n"
                    f"Authors: {short_authors(p['authors'], 8)}\n"
                    f"Department faculty on paper: {', '.join(p['faculty_authors']) or 'n/a'}\n"
                    f"Journal: {p['journal']} ({p['year']}); PMID {p['pmid']}\n"
                    f"Topics: {', '.join((p['mesh_terms'] + p['keywords'])[:12])}\n"
                    f"Abstract: {abstract}")
            blocks.append("PUBLICATIONS:\n\n" + "\n\n".join(pub_lines))
        return "\n\n".join(blocks) if blocks else "(no matching publications found)"

    # --------------------------------------------------------------- generation
    def llm_available(self) -> bool:
        if config.LLM_PROVIDER == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))

    def generate(self, system: str, history: list, user_msg: str) -> str:
        msgs = [m for m in history[-config.HISTORY_TURNS:] if m.get("content")]
        while msgs and msgs[0]["role"] != "user":  # both APIs require starting with a user turn
            msgs.pop(0)
        msgs.append({"role": "user", "content": user_msg})

        if config.LLM_PROVIDER == "anthropic":
            import anthropic
            if self._llm is None:
                self._llm = anthropic.Anthropic()
            resp = self._llm.messages.create(
                model=config.ANTHROPIC_MODEL, max_tokens=config.LLM_MAX_OUTPUT_TOKENS, system=system,
                messages=[{"role": "assistant" if m["role"] == "assistant" else "user", "content": m["content"]}
                          for m in msgs])
            return "".join(b.text for b in resp.content if b.type == "text")

        from google import genai
        from google.genai import types
        if self._llm is None:
            # Fail fast on an overloaded model (one quick retry, 45s timeout) so the fallback
            # chain below kicks in instead of the SDK retrying the same busy model for minutes.
            self._llm = genai.Client(
                api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
                http_options=types.HttpOptions(
                    timeout=45_000,
                    retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=3.0)))
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                  parts=[types.Part(text=m["content"])]) for m in msgs]
        gen_config = types.GenerateContentConfig(system_instruction=system, temperature=config.LLM_TEMPERATURE,
                                                 max_output_tokens=config.LLM_MAX_OUTPUT_TOKENS)
        models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
        last_err = None
        for model in models:
            try:
                resp = self._llm.models.generate_content(model=model, contents=contents, config=gen_config)
                self.last_model = model
                text = resp.text or ""
                finish = str(getattr(resp.candidates[0], "finish_reason", "")) if resp.candidates else ""
                if "MAX_TOKENS" in finish:
                    text += "\n\n_(Answer was cut short. Try asking a narrower question.)_"
                return text
            except Exception as e:
                msg = str(e)
                # Overloaded / rate-limited / retired model: try the next one. Anything else is a real error.
                if not any(code in msg.upper() for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404",
                                                            "TIMEOUT", "TIMED OUT")):
                    raise
                print(f"Gemini model {model} unavailable ({msg[:60]}...); trying next")
                last_err = e
        raise last_err

    @staticmethod
    def fallback_answer(pubs: list, profiles: list, reason: str) -> str:
        lines = [f"_{reason} Here is what the publication search found:_", ""]
        counts = Counter(n for p in pubs for n in p["faculty_authors"])
        if counts:
            lines.append("**Faculty most represented in these results:** " +
                         ", ".join(f"{n} ({c})" for n, c in counts.most_common(6)))
            lines.append("")
        for i, p in enumerate(pubs[:8], 1):
            lines.append(f"- {p['title']} — {short_authors(p['authors'], 3)} ({p['year']}, {p['journal']}) [{i}]")
        if not pubs and profiles:
            lines.append("Relevant faculty: " + ", ".join(profiles))
        return "\n".join(lines)

    # --------------------------------------------------------------------- main
    def answer_question(self, user_query: str, history: list | None = None, topics: list | None = None) -> dict:
        history = history or []
        topics = [t for t in (topics or []) if t in config.TOPICS]
        cache_key = None
        if not history:
            cache_key = (user_query.strip().lower(), tuple(sorted(topics)))
            hit = self.cache.get(cache_key)
            if hit:
                return {**hit, "cached": True}

        # Follow-ups ("what about her recent work?") inherit faculty from the previous user turn.
        faculty = self.detect_faculty(user_query)
        retrieval_text = user_query
        prev_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        if not faculty and prev_user and FOLLOWUP_RE.search(user_query) and len(user_query.split()) < 15:
            faculty = self.detect_faculty(prev_user)
            prev_bot = next((m["content"] for m in reversed(history) if m.get("role") == "assistant"), "")
            if not faculty and len(self.detect_faculty(prev_bot)) == 1:
                faculty = self.detect_faculty(prev_bot)
            retrieval_text = f"{prev_user} {user_query}"

        years = self.detect_years(user_query)
        recent = bool(RECENT_RE.search(user_query))
        overview = bool(OVERVIEW_RE.search(user_query)) and not faculty

        pubs, profiles = self.retrieve(retrieval_text, faculty, years, topics, recent, overview)
        context = self.build_context(pubs, profiles, overview)
        filters_note = []
        if topics:
            filters_note.append("restricted to topics: " + ", ".join(config.TOPICS[t]["label"] for t in topics))
        if any(years):
            filters_note.append(f"years {years[0] or '...'}-{years[1] or 'present'}")
        system = SYSTEM_PROMPT.format(today=date.today().isoformat(), retrieved_chunks=context)
        if filters_note:
            system += "\n\n(Search was " + "; ".join(filters_note) + ".)"

        error = None
        if not self.llm_available():
            key = "ANTHROPIC_API_KEY" if config.LLM_PROVIDER == "anthropic" else "GOOGLE_API_KEY"
            answer = self.fallback_answer(pubs, profiles, f"AI summaries are off because {key} is not set.")
            error = "no_api_key"
        else:
            try:
                answer = self.generate(system, history, user_query).strip()
                if not answer:
                    raise RuntimeError("empty response")
            except Exception as e:  # rate limits, network errors, safety blocks...
                print(f"LLM error: {e!r}")
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate" in msg.lower():
                    reason = "The AI model's free-tier rate limit was reached; please wait a minute and try again."
                elif "API_KEY_INVALID" in msg or "authentication" in msg.lower() or "401" in msg:
                    reason = "The AI model rejected the API key."
                else:
                    reason = "The AI model could not be reached."
                answer = self.fallback_answer(pubs, profiles, reason)
                error = "llm_error"

        cited = []
        for m in re.finditer(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]", answer):
            for n in re.findall(r"\d+", m.group(1)):
                n = int(n)
                if 1 <= n <= len(pubs) and n not in cited:
                    cited.append(n)
        refs = cited or list(range(1, min(len(pubs), 5) + 1))
        sources = []
        for n in refs[:config.MAX_SOURCES_RETURNED]:
            p = pubs[n - 1]
            sources.append({
                "ref": n, "pmid": p["pmid"], "title": p["title"], "authors": short_authors(p["authors"], 6),
                "year": p["year"], "journal": p["journal"], "doi": p["doi"], "pubmed_url": p["pubmed_url"],
                "faculty_authors": p["faculty_authors"],
            })

        result = {
            "answer": answer,
            "sources": sources,
            "all_refs": {str(i): p["pubmed_url"] for i, p in enumerate(pubs, 1)},
            "faculty_mentioned": self.detect_faculty(answer, strict_case=True),
            "filters": {"faculty": faculty, "years": list(years), "topics": topics, "recent": recent},
            "model": None if error else getattr(self, "last_model", config.ANTHROPIC_MODEL),
        }
        if error:
            result["error"] = error
        elif cache_key:
            self.cache.set(cache_key, result)
        return result

    # ------------------------------------------------------------ data for API
    def faculty_list(self) -> list:
        return sorted(({"name": f["name"], "slug": f["slug"], "title": f["title"],
                        "publication_count": f["publication_count"],
                        "research_interests": f["research_interests"]}
                       for f in self.faculty_rows.values()), key=lambda f: last_name(f["name"]))

    def faculty_profile(self, name_or_slug: str) -> dict | None:
        f = next((f for f in self.faculty_rows.values()
                  if f["slug"] == name_or_slug or f["name"].lower() == name_or_slug.lower()), None)
        if not f:
            return None
        rows = self.conn.execute(
            "SELECT * FROM publications WHERE faculty_authors LIKE ? ORDER BY year DESC, pmid DESC",
            (f'%"{f["name"]}"%',)).fetchall()
        pubs = [row_to_pub(r) for r in rows]
        by_year = Counter(p["year"] for p in pubs if p["year"])
        topic_counts = json.loads(f["topic_counts"] or "{}")
        return {
            "name": f["name"], "slug": f["slug"], "title": f["title"],
            "research_interests": f["research_interests"],
            "publication_count": f["publication_count"],
            "first_year": f["first_year"], "last_year": f["last_year"],
            "top_topics": json.loads(f["top_topics"] or "[]")[:10],
            "research_areas": [{"key": k, "label": config.TOPICS[k]["label"], "count": c}
                               for k, c in topic_counts.items() if k in config.TOPICS][:6],
            "top_coauthors": json.loads(f["top_coauthors"] or "[]"),
            "pubs_by_year": dict(sorted(by_year.items())),
            "recent_publications": [{"pmid": p["pmid"], "title": p["title"], "year": p["year"],
                                     "journal": p["journal"], "pubmed_url": p["pubmed_url"]} for p in pubs[:8]],
            "pubmed_query": f["pubmed_query"],
        }

    def topics(self) -> list:
        counts = Counter()
        for r in self.conn.execute("SELECT topics FROM publications"):
            counts.update(json.loads(r["topics"] or "[]"))
        return [{"key": k, "label": t["label"], "count": counts.get(k, 0)} for k, t in config.TOPICS.items()]

    def stats(self) -> dict:
        yr = self.conn.execute("SELECT MIN(year), MAX(year) FROM publications WHERE year > 0").fetchone()
        mesh = Counter()
        journals = Counter()
        for r in self.conn.execute("SELECT mesh_terms, journal FROM publications"):
            mesh.update(json.loads(r["mesh_terms"] or "[]"))
            journals[r["journal"]] += 1
        from ingest import GENERIC_TERMS
        top_mesh = [(t, c) for t, c in mesh.most_common(60) if t.lower() not in GENERIC_TERMS][:15]
        return {
            "total_publications": int(self.meta.get("publication_count", 0)),
            "faculty_count": len(self.faculty_rows),
            "year_range": [yr[0], yr[1]],
            "last_updated": self.meta.get("last_updated"),
            "top_mesh_terms": top_mesh,
            "top_journals": journals.most_common(10),
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": config.ANTHROPIC_MODEL if config.LLM_PROVIDER == "anthropic" else config.GEMINI_MODEL,
            "llm_enabled": self.llm_available(),
        }


_engine = None
_engine_lock = threading.Lock()


def get_engine() -> RAGEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = RAGEngine()
    return _engine


def answer_question(user_query: str) -> dict:
    """Convenience wrapper matching the outline's signature."""
    return get_engine().answer_question(user_query)


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Who in the department works on brain-computer interfaces?"
    r = answer_question(q)
    print(r["answer"])
    print("\nSources:")
    for s in r["sources"]:
        print(f"  [{s['ref']}] {s['title']} ({s['year']}) {s['pubmed_url']}")
    print("Filters:", r["filters"])
