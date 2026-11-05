#!/bin/bash
# One-command setup: venv -> dependencies -> PubMed ingest -> embeddings.
set -e
cd "$(dirname "$0")"
echo "PMR Research Explorer: setup"

# 1. Virtual environment
if [ ! -d venv ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 2. Python dependencies
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 3. API key check (only needed for AI-written answers; ingest/embed work without it)
if [ -z "$GOOGLE_API_KEY" ] && [ -z "$GEMINI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ] \
   && ! grep -qE "^(GOOGLE|GEMINI|ANTHROPIC)_API_KEY=.+" .env 2>/dev/null; then
    echo "Warning: no GOOGLE_API_KEY in environment or .env (https://aistudio.google.com/apikey)."
    echo "Continuing; the app will return search results without AI answers."
fi

# 4. Ingest publications from PubMed
echo "Fetching publications from PubMed..."
python ingest.py

# 5. Embed publications (downloads the ~80MB all-MiniLM-L6-v2 model on first run)
echo "Building embeddings..."
python embed.py

# 6. Done
echo "Done. Search report: data/ingest_report.txt"
echo "Start: source venv/bin/activate && python app.py  ->  http://localhost:5001"
