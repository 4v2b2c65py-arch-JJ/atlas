"""Spatial prediction menu — classify spatial objects by material
class using NASA imagery, WordPress/open datasets, and gaster-
style decode analysis, then post verdicts to comms per class.

Data sources:
  images-api.nasa.gov      — NASA image archive (no key)
  public-api.wordpress.com — WP read search (uranium/plutonium/
                             asteroid/comet posts)
  wikipedia opensearch     — material reference titles

Analysis: gaster-style — fingerprint the record, extract the
printable token layer, score material classes, emit a prediction
menu (ACT-style) ranked by confidence. Verdicts post to /api/comms
grouped by material class.
"""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "spatial"
PREDS = OUT / "predictions.jsonl"
UA = {"User-Agent": "Mozilla/5.0"}

# material class -> detection keywords (gaster decode token table)
MATERIAL_CLASSES = {
    "uranium": ("uranium", "u-235", "u-238", "yellowcake",
                "enriched uranium", "depleted uranium"),
    "plutonium": ("plutonium", "pu-239", "pu-240", "breeder",
                  "reprocessing"),
    "actinide": ("actinide", "americium", "curium", "berkelium",
                 "californium", "einsteinium", "fermium",
                 "mendelevium", "nobelium", "lawrencium"),
    "lanthanide": ("lanthanide", "cerium", "neodymium",
                   "europium", "gadolinium", "ytterbium"),
    "asteroid": ("asteroid", "neo", "near-earth", "belt",
                 "trojan", "kuiper", "planetesimal", "eros",
                 "ceres", "vesta", "apophis", " Bennu".strip()),
    "comet": ("comet", "halley", "hale-bopp", "wild 2",
              "67p", "churyumov", "outburst", "coma", "tail"),
    "lunar": ("moon", "lunar", "apollo", "regolith", "mare",
              "crater", "tranquility"),
    "martian": ("mars", "martian", "rover", "perseverance",
                "curiosity", "olympus", "valles"),
    "ice_giant": ("uranus", "neptune", "triton", "titania",
                  "oberon", "ariel", "miranda"),
    "gas_giant": ("jupiter", "saturn", "great red", "rings",
                  "galilean", "titan", "enceladus", "europa"),
    "radiometric": ("radio", "isotope", "decay", "half-life",
                    "gamma", "alpha", "beta", "dosimeter",
                    "geiger", "spectrometry"),
    "volcanic": ("volcano", "eruption", "lava", "magma", "ash",
                 "pyroclastic", "caldera"),
    "impact": ("impact", "crater", "collision", "meteor",
               "bolide", "fireball", "tektite"),
    "solar": ("sun", "solar", "corona", "flare", "sunspot",
              "prominence", "eclipse"),
    "exoplanet": ("exoplanet", "kepler", "trappist", "proxima",
                  "tess", "habitable zone"),
}


def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def nasa_search(q: str, limit: int = 5) -> list[dict]:
    """NASA image archive — titles, ids, descriptions."""
    try:
        d = _get_json(
            "https://images-api.nasa.gov/search?media_type=image"
            f"&q={urllib.parse.quote(q)}&page_size={limit}")
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for it in d.get("collection", {}).get("items", []):
        data = it.get("data", [{}])[0]
        out.append({"title": data.get("title", ""),
                    "nasa_id": data.get("nasa_id"),
                    "description": (data.get("description")
                                    or "")[:400],
                    "keywords": data.get("keywords") or [],
                    "date": data.get("date_created")})
    return out


def wp_search(q: str, limit: int = 5) -> list[dict]:
    """WordPress open data — public posts matching the query."""
    try:
        d = _get_json(
            "https://public-api.wordpress.com/rest/v1.1/read/search/"
            f"?q={urllib.parse.quote(q)}")
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for p in d.get("posts", [])[:limit]:
        out.append({"title": p.get("title", ""),
                    "url": p.get("URL", ""),
                    "site": p.get("site_URL", ""),
                    "excerpt": re.sub(r"<[^>]+>", "",
                                      p.get("excerpt", ""))[:300]})
    return out


def wiki_search(q: str, limit: int = 5) -> list[dict]:
    """Wikipedia opensearch — material reference titles."""
    try:
        req = urllib.request.Request(
            "https://en.wikipedia.org/w/api.php?action=opensearch"
            f"&search={urllib.parse.quote(q)}&limit={limit}"
            "&namespace=0&format=json", headers=UA)
        titles, _, _, urls = json.loads(
            urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        return [{"error": str(e)[:120]}]
    return [{"title": t, "url": u} for t, u in
            zip(titles, urls)]


def gaster_identify(texts: list[str]) -> list[dict]:
    """Gaster-style decode: tokenize the gathered records, score
    each material class by keyword evidence, fingerprint the
    analysis."""
    blob = " ".join(texts).lower()
    fp = hashlib.sha256(blob.encode()).hexdigest()[:16]
    scores = {}
    for cls, kws in MATERIAL_CLASSES.items():
        hits = [k for k in kws if k in blob]
        if hits:
            # weight: distinct keyword hits + total occurrences
            occ = sum(blob.count(k) for k in hits)
            scores[cls] = {"score": len(hits) * 2 + min(occ, 6),
                           "hits": hits}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1]["score"])
    menu = []
    for cls, info in ranked[:6]:
        menu.append({"class": cls, "confidence": info["score"],
                     "evidence": info["hits"],
                     "verdict": "material class identified"})
    return {"fingerprint": fp, "menu": menu,
            "analysis": "gaster.decode.token_score"}


def predict_menu(query: str) -> dict:
    """The prediction menu: gather NASA + WP + wiki records for
    the query, run gaster identification, rank material classes."""
    t0 = time.time()
    nasa = nasa_search(query)
    wp = wp_search(query)
    wiki = wiki_search(query)
    texts = []
    for coll in (nasa, wp, wiki):
        for r in coll:
            if "error" in r:
                continue
            texts.append(" ".join(str(v) for v in r.values()
                                  if isinstance(v, str)))
    ident = gaster_identify(texts)
    rec = {"ts": time.time(), "query": query,
           "sources": {"nasa": len([x for x in nasa
                                    if "error" not in x]),
                       "wp": len([x for x in wp
                                  if "error" not in x]),
                       "wiki": len([x for x in wiki
                                    if "error" not in x])},
           "nasa": nasa[:3], "wp": wp[:3], "wiki": wiki[:3],
           "menu": ident["menu"],
           "fingerprint": ident["fingerprint"],
           "elapsed_s": round(time.time() - t0, 2)}
    OUT.mkdir(parents=True, exist_ok=True)
    with PREDS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def post_comms(menu_rec: dict) -> dict:
    """Post the prediction menu to comms — one message per
    material class, clearly labeled."""
    query = menu_rec.get("query", "?")
    posted = []
    for item in menu_rec.get("menu", []):
        cls = item["class"]
        text = (f"[spatial-predict] {query} -> {cls} "
                f"(conf {item['confidence']}) "
                f"evidence: {', '.join(item['evidence'][:4])}")
        body = {"from": "spatial-predict", "to": f"class:{cls}",
                "text": text}
        try:
            req = urllib.request.Request(
                "http://localhost:8917/api/comms",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                posted.append({"class": cls, "status": r.status})
        except Exception as e:
            posted.append({"class": cls,
                           "status": "offline",
                           "err": str(e)[:80]})
    return {"query": query, "posted": posted}


def run(query: str) -> dict:
    rec = predict_menu(query)
    comms = post_comms(rec)
    return {"prediction": rec, "comms": comms}


def status() -> dict:
    n = 0
    if PREDS.exists():
        n = sum(1 for _ in PREDS.open())
    return {"predictions": n,
            "material_classes": len(MATERIAL_CLASSES),
            "out": str(PREDS)}
