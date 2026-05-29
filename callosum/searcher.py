#!/usr/bin/env python3
"""
searcher.py - Find anything. Exact words.

Hybrid search: vector similarity (60%) + BM25 keyword matching (40%).
Returns verbatim text, the actual words, never summaries.

Upstream improvements from Callosum v3.3.0:
  - BM25 hybrid search catches exact names, codes, and messages that
    embeddings miss. Real Okapi-BM25 with Lucene-style IDF.
  - Drawer-grep: returns the best-matching chunk + neighbors, not the
    entire drawer. Massive token savings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import chromadb


# -- BM25 implementation -----------------------------------------------
# Okapi BM25 with Lucene-style IDF (Callosum v3.3.0)

_BM25_K1 = 1.2
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"[a-zA-Z0-9_#.]+", text.lower())


class _BM25Index:
    """In-memory BM25 index for a set of documents."""

    def __init__(self, documents: list[str]):
        self.docs = documents
        self.doc_count = len(documents)
        self.doc_tokens = [_tokenize(d) for d in documents]
        self.doc_lens = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.doc_lens) / max(self.doc_count, 1)

        # Document frequency
        self.df: dict[str, int] = Counter()
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] += 1

    def score(self, query: str) -> list[float]:
        """Score each document against the query. Returns list of BM25 scores."""
        query_tokens = _tokenize(query)
        scores = [0.0] * self.doc_count

        for term in query_tokens:
            if term not in self.df:
                continue
            # Lucene-style IDF: log(1 + (N - df + 0.5) / (df + 0.5))
            idf = math.log(1.0 + (self.doc_count - self.df[term] + 0.5) / (self.df[term] + 0.5))

            for i, tokens in enumerate(self.doc_tokens):
                tf = tokens.count(term)
                if tf == 0:
                    continue
                dl = self.doc_lens[i]
                numerator = tf * (_BM25_K1 + 1)
                denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(self.avgdl, 1))
                scores[i] += idf * numerator / denominator

        return scores


# -- Drawer-grep --------------------------------------------------------
# Return the best-matching chunk + neighbors instead of entire drawer.

_GREP_WINDOW = 5  # lines of context around best match


def _grep_best_chunk(document: str, query: str, window: int = _GREP_WINDOW) -> str:
    """Extract the best-matching chunk from a document.

    Scores each line by query term overlap, then returns
    the best line +/- window lines of context.
    """
    lines = document.split("\n")
    if len(lines) <= window * 2 + 1:
        return document  # Small doc, return as-is

    query_terms = set(_tokenize(query))
    if not query_terms:
        return document

    # Score each line
    line_scores = []
    for i, line in enumerate(lines):
        line_terms = set(_tokenize(line))
        overlap = len(query_terms & line_terms)
        line_scores.append((overlap, i))

    # Find best line
    best_score, best_idx = max(line_scores, key=lambda x: x[0])
    if best_score == 0:
        # No term overlap, return first chunk
        return "\n".join(lines[: window * 2 + 1])

    start = max(0, best_idx - window)
    end = min(len(lines), best_idx + window + 1)
    chunk = "\n".join(lines[start:end])

    if start > 0:
        chunk = "...\n" + chunk
    if end < len(lines):
        chunk = chunk + "\n..."

    return chunk


# -- Hybrid search ------------------------------------------------------

_VECTOR_WEIGHT = 0.6
_BM25_WEIGHT = 0.4


def _hybrid_rerank(
    docs: list[str],
    metas: list[dict],
    dists: list[float],
    query: str,
) -> list[tuple[str, dict, float]]:
    """Re-rank results using vector + BM25 hybrid scoring.

    Vector scores are normalized from ChromaDB distances.
    BM25 scores are computed in-memory on the result set.
    Final score = 0.6 * vector + 0.4 * bm25 (both min-max normalized).
    """
    if not docs:
        return []

    # Normalize vector similarities to [0, 1]
    vector_sims = [1.0 - d for d in dists]
    v_min = min(vector_sims) if vector_sims else 0
    v_max = max(vector_sims) if vector_sims else 1
    v_range = v_max - v_min if v_max != v_min else 1.0
    vector_norm = [(s - v_min) / v_range for s in vector_sims]

    # BM25 scores
    bm25 = _BM25Index(docs)
    bm25_scores = bm25.score(query)
    b_max = max(bm25_scores) if bm25_scores else 1
    b_max = b_max if b_max > 0 else 1.0
    bm25_norm = [s / b_max for s in bm25_scores]

    # Combine
    combined = []
    for i in range(len(docs)):
        hybrid_score = _VECTOR_WEIGHT * vector_norm[i] + _BM25_WEIGHT * bm25_norm[i]
        combined.append((docs[i], metas[i], hybrid_score))

    combined.sort(key=lambda x: x[2], reverse=True)
    return combined


# -- Public API ---------------------------------------------------------


def search(query: str, palace_path: str, wing: str = None, room: str = None, n_results: int = 5):
    """
    Search the palace. Returns verbatim drawer content.
    Optionally filter by wing (project) or room (aspect).
    Uses hybrid vector + BM25 scoring.
    """
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception as e:
        raise RuntimeError(
            f"No palace found at {palace_path}.\n"
            f"Run: callosum init <dir> then callosum mine <dir>\n"
            f"Original Error: {e}"
        )

    # Build where filter
    where = {}
    if wing and room:
        where = {"$and": [{"wing": wing}, {"room": room}]}
    elif wing:
        where = {"wing": wing}
    elif room:
        where = {"room": room}

    try:
        fetch_n = min(n_results * 3, col.count() or n_results)
        fetch_n = max(fetch_n, n_results)
        kwargs = {
            "query_texts": [query],
            "n_results": fetch_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

    except Exception as e:
        raise RuntimeError(f"Search error: {e}")

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    if not docs:
        print(f'\n  No results found for: "{query}"')
        return

    # Hybrid re-rank
    ranked = _hybrid_rerank(docs, metas, dists, query)[:n_results]

    sep = "=" * 60
    print(f"\n{sep}")
    vw = f"{_VECTOR_WEIGHT:.0%}"
    bw = f"{_BM25_WEIGHT:.0%}"
    print(f'  Results for: "{query}"  [hybrid: vector {vw} + BM25 {bw}]')
    if wing:
        print(f"  Wing: {wing}")
    if room:
        print(f"  Room: {room}")
    print(f"{sep}\n")

    for i, (doc, meta, score) in enumerate(ranked, 1):
        source = Path(meta.get("source_file", "?")).name
        wing_name = meta.get("wing", "?")
        room_name = meta.get("room", "?")

        print(f"  [{i}] {wing_name} / {room_name}")
        print(f"      Source: {source}")
        print(f"      Score:  {round(score, 3)}")
        print()
        chunk = _grep_best_chunk(doc, query)
        for line in chunk.strip().split("\n"):
            print(f"      {line}")
        print()
        dash = "-" * 56
        print(f"  {dash}")

    print()


def search_memories(
    query: str, palace_path: str, wing: str = None, room: str = None, n_results: int = 5
) -> dict:
    """
    Programmatic search, returns a dict instead of printing.
    Used by the MCP server and other callers that need data.

    Search order:
      1. Closet-first: fast topic-pointer scan (if closets exist)
      2. Fallback: hybrid vector + BM25 scoring with drawer-grep
    """
    # Try closet-first search
    try:
        from .closet import closet_search

        closet_hits = closet_search(
            query,
            palace_path,
            wing=wing,
            room=room,
            n_results=n_results,
        )
        if closet_hits:
            # Apply drawer-grep to closet results
            for hit in closet_hits:
                hit["text"] = _grep_best_chunk(hit["text"], query)
            return {
                "query": query,
                "filters": {"wing": wing, "room": room},
                "search_mode": "closet_first",
                "results": closet_hits,
            }
    except Exception:
        pass  # Closet search failed, fall through to hybrid

    # Fallback: hybrid vector + BM25
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception as e:
        return {"error": f"No palace found at {palace_path}: {e}"}

    where = {}
    if wing and room:
        where = {"$and": [{"wing": wing}, {"room": room}]}
    elif wing:
        where = {"wing": wing}
    elif room:
        where = {"room": room}

    try:
        fetch_n = min(n_results * 3, col.count() or n_results)
        fetch_n = max(fetch_n, n_results)
        kwargs = {
            "query_texts": [query],
            "n_results": fetch_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)
    except Exception as e:
        return {"error": f"Search error: {e}"}

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    ranked = _hybrid_rerank(docs, metas, dists, query)[:n_results]

    hits = []
    for doc, meta, score in ranked:
        chunk = _grep_best_chunk(doc, query)
        hits.append(
            {
                "text": chunk,
                "full_text": doc,
                "wing": meta.get("wing", "unknown"),
                "room": meta.get("room", "unknown"),
                "source_file": Path(meta.get("source_file", "?")).name,
                "similarity": round(score, 3),
                "matched_via": "drawer",
            }
        )

    return {
        "query": query,
        "filters": {"wing": wing, "room": room},
        "search_mode": "hybrid_bm25",
        "results": hits,
    }
