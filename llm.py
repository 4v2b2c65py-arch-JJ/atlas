"""Local-model (Ollama) enhancement layer for ATLAS classification."""

import json
import urllib.request
from pathlib import Path

OLLAMA = "http://localhost:11434"
TIMEOUT = 180


def list_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _chat(model: str, prompt: str, timeout: int = TIMEOUT) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 400},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"]


def pick_model(profile: dict) -> str | None:
    models = list_models()
    if not models:
        return None
    preferred = profile.get("observer_model")
    return preferred if preferred in models else models[0]


def refine_classification(obs: dict, heuristic: dict, profile: dict) -> dict | None:
    """Ask the local LLM to review the heuristic sort. Returns overrides
    {categories, quality_score, reasoning} or None if unavailable."""
    model = pick_model(profile)
    if not model:
        return None

    prompt = (
        "You are ATLAS, a local video observer classifying observed footage.\n"
        f"Allowed categories: {json.dumps(profile['categories'])}\n"
        f"Observed features: {json.dumps(obs, default=str)[:1500]}\n"
        f"Heuristic verdict: categories={heuristic['categories']} "
        f"score={heuristic['quality_score']}\n"
        "Decide the best categories and a 0-100 quality score. "
        "Reply ONLY with JSON: "
        '{"categories": ["..."], "quality_score": 0-100, "reasoning": "<=25 words"}'
    )
    try:
        raw = _chat(model, prompt)
        verdict = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception:
        return {"error": "llm_unavailable_or_unparseable", "model": model}

    allowed = set(profile["categories"])
    cats = [c for c in verdict.get("categories", []) if c in allowed]
    if not cats:
        cats = heuristic["categories"]
    try:
        score = max(0, min(100, int(verdict.get("quality_score", heuristic["quality_score"]))))
    except (TypeError, ValueError):
        score = heuristic["quality_score"]

    return {
        "categories": cats,
        "quality_score": score,
        "reasoning": str(verdict.get("reasoning", ""))[:200],
        "model": model,
    }


EXPAND_CACHE = Path(__file__).parent / "data" / "query_cache.json"


def expand_query(query: str, profile: dict | None = None) -> dict | None:
    """Use the local model to expand a search query into match terms,
    a category hint, and an intent label. JSON in, JSON out.
    Cached on disk — repeat queries are instant."""
    try:
        cache = json.loads(EXPAND_CACHE.read_text())
    except Exception:
        cache = {}
    if query in cache:
        return cache[query]
    model = pick_model(profile or {})
    if not model:
        return None
    prompt = (
        "You are ATLAS, a video outlet search engine. Expand the user's "
        "query into keywords for matching video titles/tags, pick the "
        "closest category, and name the intent.\n"
        f"Query: {query!r}\n"
        "Reply ONLY with JSON: "
        '{"keywords": ["..."], "category_hint": "...", "intent": "..."}'
    )
    try:
        raw = _chat(model, prompt, timeout=45)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        kws = [str(k).lower() for k in data.get("keywords", [])][:8]
        result = {
            "keywords": kws or [query.lower()],
            "category_hint": str(data.get("category_hint", "")).lower(),
            "intent": str(data.get("intent", "search")).lower(),
            "model": model,
        }
    except Exception:
        result = {"keywords": [query.lower()], "category_hint": "",
                  "intent": "search", "model": model, "fallback": True}
    try:
        cache[query] = result
        EXPAND_CACHE.parent.mkdir(exist_ok=True)
        EXPAND_CACHE.write_text(json.dumps(cache, indent=1))
    except Exception:
        pass
    return result


def match_catalogue(query: str, expanded: dict, catalogue: list) -> dict | None:
    """Ask the local model to pick the best catalogue entry for the query,
    or null if nothing fits. Returns the entry dict or None."""
    model = pick_model({})
    if not model:
        return None
    listing = json.dumps(
        [{"title": e["title"], "tags": e["tags"], "size_mb": e["size_mb"]}
         for e in catalogue])
    prompt = (
        "Pick the single best video for the query from this catalogue, "
        "or reply {\"title\": null} if nothing fits.\n"
        f"Query: {query!r}\nExpanded keywords: {json.dumps(expanded.get('keywords', []))}\n"
        f"Catalogue: {listing}\n"
        "Reply ONLY with JSON: {\"title\": \"exact title or null\", "
        "\"reason\": \"<=15 words\"}"
    )
    try:
        raw = _chat(model, prompt)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        title = data.get("title")
        if not title:
            return None
        for e in catalogue:
            if e["title"].lower() == str(title).lower():
                return {**e, "reason": data.get("reason", "")}
        # fuzzy: substring match either direction
        for e in catalogue:
            if str(title).lower() in e["title"].lower() or \
                    e["title"].lower() in str(title).lower():
                return {**e, "reason": data.get("reason", "")}
        return None
    except Exception:
        return None


def suggest_queries(topic: str, n: int = 5,
                    profile: dict | None = None) -> list[str]:
    """Ask the local model for diverse search queries around a topic —
    used to grow the catalogue without dead-ending on one phrasing."""
    model = pick_model(profile or {})
    if not model:
        return [topic]
    prompt = (
        f"Give {n} short, diverse video-search queries related to: {topic!r}\n"
        "Vary phrasing (synonyms, specifics, related concepts). "
        "Reply ONLY with a JSON array of strings, no commentary."
    )
    try:
        raw = _chat(model, prompt, timeout=45)
        arr = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
        qs = [str(q).strip() for q in arr if str(q).strip()][:n]
        return qs or [topic]
    except Exception:
        return [topic]
