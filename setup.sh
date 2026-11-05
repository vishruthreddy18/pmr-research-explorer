#!/bin/bash
# One-command setup: venv -> dependencies -> PubMed ingest -> embeddings.
set -e
cd "$(dirname "$0")"
echo "=== PMR Research Intelligence Bot Setup ==="

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
    echo ""
    echo "WARNING: GOOGLE_API_KEY is not set."
    echo "  Get a free key at https://aistudio.google.com/apikey"
    echo "  Then add it to the .env file: GOOGLE_API_KEY=your-key-here"
    echo "  (Continuing: the bot will still search publications, but without AI-written answers.)"
    echo ""
fi

# 4. Ingest publications from PubMed
echo "Fetching publications from PubMed (about 1 minute; longer without an NCBI_API_KEY on a slow connection)..."
python ingest.py

# 5. Embed publications (downloads the ~80MB all-MiniLM-L6-v2 model on first run)
echo "Embedding publications into the vector database..."
python embed.py

# 6. Done
echo ""
echo "=== Setup complete! ==="
echo "Review data/ingest_report.txt to sanity-check each faculty member's PubMed search."
echo "Run:  source venv/bin/activate && python app.py"
echo "Then open: http://localhost:5001"
