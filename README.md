# PMR Research Intelligence Bot

A RAG chatbot for Pitt's Department of Physical Medicine & Rehabilitation. Prospective students,
faculty candidates and collaborators can ask questions in plain English and get answers that cite
the department's actual PubMed publications.

Everything is free: PubMed E-utilities for data, `all-MiniLM-L6-v2` embeddings that run locally,
ChromaDB for vectors, the Google Gemini free tier for answers, and Flask for the app.

## Quick start

Requirements: Python 3.10+, about 500 MB of disk space, and a free Gemini API key from
https://aistudio.google.com/apikey.

```bash
export GOOGLE_API_KEY=your-key-here
./setup.sh                          # venv + deps + PubMed ingest (~1 min) + embeddings (~1 min)
source venv/bin/activate
python app.py                       # open http://localhost:5001
```

Without an API key the app still works. It shows the matching publications and the faculty who
appear most in them, but no AI-written summary.

## How it works

```
PubMed ─► ingest.py ─► SQLite (data/publications.db) ─► embed.py ─► ChromaDB (data/chroma)
                                                                        │
User ─► app.py ─► query.py: detect faculty / years / topics ─► retrieve ─► Gemini ─► answer + sources
```

| File | Purpose |
|------|---------|
| `config.py` | All settings: models, paths, retrieval sizes, research-topic keywords |
| `faculty.py` | The 27 faculty, with the PubMed author term and affiliation scope used for each |
| `ingest.py` | Searches PubMed per faculty member, fetches records in batches of 200, deduplicates, stores in SQLite, builds research profiles from MeSH and keyword frequencies |
| `embed.py` | One chunk per publication plus one summary chunk per faculty member, stored in ChromaDB with filterable metadata |
| `query.py` | The RAG engine: query preprocessing, hybrid retrieval, prompt building, Gemini/Claude generation, citations |
| `app.py` | Flask routes: `/`, `/api/chat`, `/api/faculty`, `/api/faculty/<slug>`, `/api/topics`, `/api/stats` |
| `templates/`, `static/` | The Pitt-branded chat UI (plain HTML/CSS/JS) |

### Retrieval details
- **Faculty mentions** ("Dr. Collinger", "Amy Wagner") restrict the search to that person's papers.
  For comparisons, results are split evenly between the people named.
- **"Recent" / "latest"** questions about a person return their newest papers by date.
- **Year phrases** ("after 2020", "between 2018 and 2021", "last 5 years") become metadata filters.
- **Topics named in the question** (e.g. "machine learning", "wheelchair") reserve half the result
  slots for papers tagged with that topic. The sidebar topic checkboxes act as hard filters.
- **Department-wide questions** ("biggest themes", "strengths") add department statistics and every
  faculty profile to the context.
- **Follow-ups** ("what about her recent work?") reuse the faculty member from the previous turn.
- Identical questions are cached for 6 hours.

## Faculty search tuning

PubMed lists most authors as "LastName Initials" (for example "Boninger ML"), so searching on
full first names misses most papers. Each entry in `faculty.py` sets:

- `pubmed_author`: the author term, e.g. `"Boninger M"`.
- `scope`: the affiliation filter.
  - `pitt` (default) adds `Pittsburgh[Affiliation]`.
  - `pmr` also requires "Physical Medicine" or "Rehabilitation" in the affiliation. Use it for
    common names.
  - `none` searches by author name only, for distinctive names.
- `query`: an optional raw PubMed query that overrides both of the above.

After each ingest, check `data/ingest_report.txt` for hit counts and auto-generated research areas.
`python ingest.py --only "Chou"` dry-runs one person's search so you can tune it.

Limitation: before 2014, PubMed recorded only the first author's affiliation. Older papers where a
faculty member was not first author may therefore be missing for faculty with `pitt`/`pmr` scope.

## Refreshing data

```bash
python ingest.py && python embed.py
```

The app reads the database once at startup, so restart it afterwards. For a daily refresh, run
those two commands from cron. Set `NCBI_API_KEY` (free) and `NCBI_EMAIL` to raise PubMed's rate
limit.

## Switching to Claude

```bash
pip install anthropic
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
# optional: export ANTHROPIC_MODEL=claude-sonnet-5-5   (the default)
```

No code changes are needed. `query.py` routes to whichever provider is configured.

## Deploying to Render (free tier)

1. Run `setup.sh` locally, then commit `data/publications.db` and `data/chroma/`. The free tier has
   no persistent disk, so the app serves the prebuilt database from the repo.
2. Push to GitHub. In Render, choose **New → Blueprint** and select the repo (it uses `render.yaml`).
3. Set `GOOGLE_API_KEY` in the Render dashboard.

The app uses ChromaDB's ONNX build of all-MiniLM-L6-v2 instead of PyTorch `sentence-transformers`.
The model and embeddings are the same, but the install is about 1 GB smaller and the app fits in
the free tier's 512 MB of RAM. To use PyTorch instead, `pip install sentence-transformers`, set
`EMBEDDING_BACKEND=sentence-transformers`, and re-run `embed.py`. Free Render services sleep when
idle, so the first request after a pause takes about 30–60 seconds.

## Configuration (environment variables)

| Variable | Default | Notes |
|----------|---------|-------|
| `GOOGLE_API_KEY` | – | Gemini key (`GEMINI_API_KEY` also works) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Any Gemini model id |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.1-flash-lite,gemini-flash-latest` | Tried in order when the main model is busy or retired |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `NCBI_API_KEY`, `NCBI_EMAIL` | – | Optional, raise PubMed's rate limit to 10 requests/sec |
| `EMBEDDING_BACKEND` | `onnx` | or `sentence-transformers` |
| `MODEL_CACHE_DIR` | `~/.cache/chroma` | Set to `.model_cache` on Render |
| `PORT` | `5001` | macOS uses 5000 for AirPlay Receiver |

## Logo

The header looks for `static/pitt-logo.png` and shows a text badge if the file is missing. Add the
official logo file from University Marketing Communications to use it.
