# Technical details

Maintainer reference. Overview: [README](../README.md).

## Retrieval

`query.py` adjusts retrieval based on the question:

| Question contains | Retrieval behavior |
|---|---|
| A faculty name ("Dr. Collinger", "Amy Wagner") | Filters to that person's papers; multiple names split results evenly |
| "recent", "latest" + a person | Newest papers by date |
| A year phrase ("after 2020", "2018–2021", "last 5 years") | Adds a year filter |
| A known topic ("machine learning", "wheelchair") | Half the results reserved for papers tagged with that topic |
| A department-wide question ("biggest themes") | Department statistics and all faculty profiles added to context |
| A follow-up ("what about her recent work?") | Faculty from the previous turn reused |

Topic checkboxes in the sidebar act as hard filters. Identical questions are cached for 6 hours.

Papers without abstracts (editorials, meeting notes) get a small ranking penalty; their short text over-matches generic questions.

## Faculty search tuning

PubMed indexes authors as "LastName Initials" (e.g. "Boninger ML"); full-name searches miss most papers.
Each entry in `faculty.py` sets:

| Field | Meaning |
|---|---|
| `pubmed_author` | The author term, e.g. `Boninger M` |
| `scope` | `pitt` (default) adds `Pittsburgh[Affiliation]`. `pmr` also requires "Physical Medicine" or "Rehabilitation", for common names. `none` searches by name only, for distinctive names |
| `query` | Optional raw PubMed query; overrides the fields above |

Hit counts and generated research areas are written to `data/ingest_report.txt`.
Dry-run one person: `python ingest.py --only "Chou"`.

**Limitation:** before 2014, PubMed recorded only the first author's affiliation, so older co-authored
papers may be missing for `pitt`/`pmr` scope.

## Refreshing data

```bash
python ingest.py && python embed.py   # then restart the app
```

Schedule with cron for daily updates. `NCBI_API_KEY` and `NCBI_EMAIL` raise PubMed's rate limit from 3 to
10 requests/sec.

## Using Anthropic instead of Gemini

```bash
pip install anthropic
# in .env:
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5-5   # optional (default)
```

## Deploying to Render (free tier)

1. Commit `data/publications.db` and `data/chroma/` (the free tier has no persistent disk).
2. Render → **New → Blueprint** → select this repo (`render.yaml`).
3. Set `GOOGLE_API_KEY` in the Render dashboard.

Embeddings use ChromaDB's ONNX build of all-MiniLM-L6-v2 instead of PyTorch `sentence-transformers`:
same vectors, ~1 GB smaller install, ~320 MB RAM at runtime (free-tier limit: 512 MB). Idle services sleep;
the first request after a pause takes 30–60 s.

## Configuration

Set in `.env` locally or in the Render dashboard.

| Variable | Default | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | – | Gemini key (`GEMINI_API_KEY` also works) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Main model |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.1-flash-lite,gemini-flash-latest` | Tried in order when the main model is busy or retired |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `NCBI_API_KEY`, `NCBI_EMAIL` | – | Optional; raise PubMed's rate limit |
| `EMBEDDING_BACKEND` | `onnx` | or `sentence-transformers` (re-run `embed.py` after switching) |
| `MODEL_CACHE_DIR` | `~/.cache/chroma` | Set to `.model_cache` on Render |
| `PORT` | `5001` | macOS uses 5000 for AirPlay Receiver |

## API

| Route | Returns |
|---|---|
| `POST /api/chat` | `{answer, sources[], faculty_mentioned[], filters, model}`. Body: `{message, history?, topics?}` |
| `GET /api/faculty` | The faculty list with publication counts |
| `GET /api/faculty/<slug>` | One profile: topics, collaborators, papers per year, recent papers |
| `GET /api/topics` | Research topics with paper counts |
| `GET /api/stats` | Department totals, date range, active model |
| `GET /healthz` | Liveness check |

## Logo

Place the official logo at `static/pitt-logo.png`; a text badge is shown if it's missing.
