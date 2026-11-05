"""Embed publications + faculty summaries from SQLite into ChromaDB.

Usage: python embed.py
"""
import json
import os
import shutil
import time

import chromadb

import config
from utils import db_connect, row_to_pub, short_authors, slug


def get_embedding_function():
    if config.EMBEDDING_BACKEND == "sentence-transformers":
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        return SentenceTransformerEmbeddingFunction(model_name=config.EMBEDDING_MODEL)
    # ChromaDB's default embedder is the ONNX export of all-MiniLM-L6-v2
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    ef = ONNXMiniLM_L6_V2()
    if config.MODEL_CACHE_DIR:
        ef.DOWNLOAD_PATH = os.path.join(config.BASE_DIR, config.MODEL_CACHE_DIR, ef.MODEL_NAME)
    return ef


def get_collection(create: bool = False):
    client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
    ef = get_embedding_function()
    if create:
        return client.get_or_create_collection(config.COLLECTION_NAME, embedding_function=ef,
                                               metadata={"hnsw:space": "cosine"})
    return client.get_collection(config.COLLECTION_NAME, embedding_function=ef)


def publication_chunk(p: dict) -> str:
    return "\n".join([
        f"Title: {p['title']}",
        f"Authors: {short_authors(p['authors'], 12)}",
        f"Faculty: {', '.join(p['faculty_authors'])}",
        f"Journal: {p['journal']} ({p['year']})",
        f"Abstract: {p['abstract'] or '(no abstract available)'}",
        f"Topics: {', '.join(p['mesh_terms'] + p['keywords'])}",
    ])


def faculty_chunk(f: dict, recent: list) -> str:
    top = json.loads(f["top_topics"] or "[]")
    return "\n".join([
        f"Faculty Member: {f['name']}",
        f"Title: {f['title'] or 'Faculty, Department of Physical Medicine & Rehabilitation'}",
        f"Research Areas: {f['research_interests']}",
        f"Number of Publications: {f['publication_count']} ({f['first_year']}-{f['last_year']})",
        f"Key Topics: {', '.join(t for t, _ in top[:12])}",
        "Recent Work: " + "; ".join(f"{r['title']} ({r['year']})" for r in recent),
    ])


def main():
    conn = db_connect()
    pubs = [row_to_pub(r) for r in conn.execute("SELECT * FROM publications")]
    faculty = [dict(r) for r in conn.execute("SELECT * FROM faculty")]
    print(f"Loaded {len(pubs)} publications and {len(faculty)} faculty from SQLite")

    shutil.rmtree(config.CHROMA_PERSIST_DIR, ignore_errors=True)  # full rebuild keeps things consistent
    col = get_collection(create=True)

    ids, docs, metas = [], [], []
    for p in pubs:
        meta = {
            "doc_type": "publication",
            "pmid": p["pmid"],
            "title": p["title"][:500],
            "year": p["year"] or 0,
            "journal": p["journal"] or "",
            "doi": p["doi"] or "",
            "faculty_authors": ", ".join(p["faculty_authors"]),
            "has_abstract": bool(p["abstract"]),
        }
        for name in p["faculty_authors"]:
            meta[f"fac_{slug(name)}"] = True
        for t in p["topics"]:
            meta[f"topic_{t}"] = True
        ids.append(f"pub_{p['pmid']}")
        docs.append(publication_chunk(p))
        metas.append(meta)

    for f in faculty:
        recent = conn.execute(
            "SELECT title, year FROM publications WHERE faculty_authors LIKE ? ORDER BY year DESC LIMIT 5",
            (f'%"{f["name"]}"%',)).fetchall()
        ids.append(f"fac_{f['slug']}")
        docs.append(faculty_chunk(f, [dict(r) for r in recent]))
        metas.append({"doc_type": "faculty", "faculty_authors": f["name"], f"fac_{f['slug']}": True,
                      "year": f["last_year"] or 0})

    start = time.time()
    batch = 128
    for i in range(0, len(ids), batch):
        col.add(ids=ids[i:i + batch], documents=docs[i:i + batch], metadatas=metas[i:i + batch])
        print(f"  embedded {min(i + batch, len(ids))}/{len(ids)}", end="\r", flush=True)
    print(f"\nDone: {col.count()} chunks in {time.time() - start:.0f}s -> {config.CHROMA_PERSIST_DIR}")


if __name__ == "__main__":
    main()
