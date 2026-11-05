"""Central configuration. Every value can be overridden with an environment variable."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load secrets from .env (if present). Real environment variables take precedence.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- PubMed -----------------------------------------------------------------
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
PUBMED_AFFILIATION = "Pittsburgh"
PUBMED_DEPARTMENT_TERMS = ["Physical Medicine", "Rehabilitation"]
# Optional but recommended: a free NCBI API key raises the limit from 3 to 10 req/sec.
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "")
NCBI_TOOL = "pmr-research-bot"
PUBMED_REQUEST_DELAY = 0.12 if NCBI_API_KEY else 0.4  # seconds between requests
PUBMED_FETCH_BATCH = 200          # efetch batch size
MAX_PUBS_PER_FACULTY = 1000       # hard cap per person
REVIEW_THRESHOLD = 200            # flag searches returning more than this for manual review

# --- Storage ----------------------------------------------------------------
SQLITE_PATH = os.path.join(DATA_DIR, "publications.db")
RAW_XML_DIR = os.path.join(DATA_DIR, "raw")
CHROMA_PERSIST_DIR = os.path.join(DATA_DIR, "chroma")
COLLECTION_NAME = "pmr_publications"

# --- Embeddings -------------------------------------------------------------
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
# "onnx": ChromaDB's bundled ONNX build of all-MiniLM-L6-v2 (no PyTorch, small, fits Render free tier)
# "sentence-transformers": the PyTorch version (pip install sentence-transformers)
# Both produce the same embeddings, but you must re-run embed.py after switching.
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "onnx")
# Where the ONNX model is cached. Default: ~/.cache/chroma. On Render, set MODEL_CACHE_DIR=.model_cache
# so the model downloaded during the build is kept inside the project directory.
MODEL_CACHE_DIR = os.environ.get("MODEL_CACHE_DIR", "")

# --- LLM --------------------------------------------------------------------
# "gemini" (free tier) or "anthropic" (upgrade path). Nothing else needs to change.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# Tried in order if the main model is overloaded (503), rate-limited (429) or retired (404).
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-flash-latest").split(",") if m.strip()]
# Get a free key at https://aistudio.google.com/apikey and set GOOGLE_API_KEY
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# Set ANTHROPIC_API_KEY when using the anthropic provider
LLM_TEMPERATURE = 0.3
LLM_MAX_OUTPUT_TOKENS = 8192  # generous: newer Gemini models spend part of this budget on internal reasoning

# --- Retrieval / app --------------------------------------------------------
FLASK_PORT = int(os.environ.get("PORT", 5001))  # 5000 is taken by macOS AirPlay Receiver
TOP_K_RESULTS = 10          # publications retrieved per query
MAX_SOURCES_RETURNED = 10   # citation cards shown in the UI
HISTORY_TURNS = 6           # previous chat messages sent to the LLM for follow-ups
CACHE_SIZE = 256            # cached answers (identical question + filters)
CACHE_TTL_SECONDS = 6 * 3600

# Research topics used for sidebar filters and tagging. A paper is tagged with a topic
# when any keyword appears in its title, abstract, keywords or MeSH terms.
TOPICS = {
    "bci": {"label": "Brain-Computer Interfaces",
            "keywords": ["brain-computer interface", "brain computer interface", "brain-machine interface",
                         "intracortical", "neuroprosthe", "microelectrode array", "bci"]},
    "sci": {"label": "Spinal Cord Injury",
            "keywords": ["spinal cord injur", "spinal cord injuries", "tetraplegi", "paraplegi", "sci"]},
    "wheelchair": {"label": "Wheelchairs & Mobility",
                   "keywords": ["wheelchair", "mobility device", "power mobility", "scooter"]},
    "tbi": {"label": "Traumatic Brain Injury",
            "keywords": ["traumatic brain injur", "brain injuries, traumatic", "concussion", "tbi",
                         "cortical impact", "acquired brain injur"]},
    "pain": {"label": "Pain & Spine",
             "keywords": ["pain", "low back", "intervertebral disc", "spine", "spinal stenosis", "analges"]},
    "stroke": {"label": "Stroke",
               "keywords": ["stroke", "hemipare", "cerebrovascular"]},
    "neuromod": {"label": "Neuromodulation & Stimulation",
                 "keywords": ["spinal cord stimulation", "electrical stimulation", "neuromodulation",
                              "somatosensory feedback", "microstimulation", "nerve stimulation", "dorsal root"]},
    "pediatric": {"label": "Pediatric Rehabilitation",
                  "keywords": ["child", "pediatric", "paediatric", "adolescen", "infant", "spina bifida",
                               "cerebral palsy"]},
    "assistive": {"label": "Assistive & Rehab Technology",
                  "keywords": ["assistive technolog", "robot", "exoskeleton", "prosthe", "wearable",
                               "rehabilitation engineering", "smart home"]},
    "musculoskeletal": {"label": "Muscle & Musculoskeletal",
                        "keywords": ["skeletal muscle", "sarcopenia", "musculoskeletal", "tendon", "osteoarthritis",
                                     "muscle regeneration", "myosteatosis"]},
    "ai": {"label": "AI & Machine Learning",
           "keywords": ["machine learning", "deep learning", "artificial intelligence", "neural network",
                        "natural language processing", "large language model", "classifier", "ai"]},
    "outcomes": {"label": "Outcomes & Measurement",
                 "keywords": ["outcome measure", "psychometric", "patient-reported", "quality of life",
                              "item response theory", "validity", "reliability"]},
}
