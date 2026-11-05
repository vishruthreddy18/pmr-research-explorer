"""Flask app: serves the chat UI and JSON API."""
import os

from flask import Flask, abort, jsonify, render_template, request

import config
from query import get_engine

app = Flask(__name__)


def engine():
    if not os.path.exists(config.SQLITE_PATH) or not os.path.isdir(config.CHROMA_PERSIST_DIR):
        abort(503, description="Data not built yet. Run: python ingest.py && python embed.py")
    return get_engine()


@app.errorhandler(503)
@app.errorhandler(400)
@app.errorhandler(404)
def json_error(e):
    return jsonify({"error": e.description}), e.code


@app.route("/")
def index():
    return render_template("index.html", topics=config.TOPICS)


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        abort(400, description="message is required")
    if len(message) > 2000:
        abort(400, description="message is too long (2000 characters max)")
    history = [{"role": m.get("role"), "content": str(m.get("content", ""))[:6000]}
               for m in (data.get("history") or []) if m.get("role") in ("user", "assistant")]
    topics = data.get("topics") or []
    return jsonify(engine().answer_question(message, history=history, topics=topics))


@app.route("/api/faculty")
def faculty_list():
    return jsonify(engine().faculty_list())


@app.route("/api/faculty/<name>")
def faculty_profile(name):
    profile = engine().faculty_profile(name)
    if not profile:
        abort(404, description=f"No faculty member '{name}'")
    return jsonify(profile)


@app.route("/api/topics")
def topics():
    return jsonify(engine().topics())


@app.route("/api/stats")
def stats():
    return jsonify(engine().stats())


@app.route("/healthz")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
