"""Headless Chromium layer — guest sessions + model automation.

Playwright chromium with a persistent profile (data/browser_profile)
so cookies/storage survive restarts = 'guest session' state. Used for:
  - rendering JS/Cloudflare-gated pages plain urllib can't reach
    (scan sites, social walls)
  - model automation: the LLM picks a target/search URL, the browser
    renders it, and structured content (links, media, text) comes back

Playwright's sync API is single-threaded, so all browser work runs on
one dedicated thread; callers submit jobs over a queue.
"""

import base64
import json
import queue
import re
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent
PROFILE = ROOT / "data" / "browser_profile"
SESSIONS_PATH = ROOT / "data" / "guest_sessions.json"
_jobs: queue.Queue = queue.Queue()
_ready = threading.Event()
_state = {"ok": False, "err": None}
_sessions: dict = {}  # in-memory guest session registry


def _loop() -> None:
    from playwright.sync_api import sync_playwright
    pw = browser = ctx = None
    try:
        pw = sync_playwright().start()
        PROFILE.mkdir(parents=True, exist_ok=True)
        # persistent context = guest session cookies/storage survive
        # restarts (real 'stay logged in as guest' behaviour)
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
            locale="en-US")
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',"
            "{get:()=>undefined})")
        browser = None  # context owns the browser in persistent mode
        _state["ok"] = True
    except Exception as e:
        _state["err"] = str(e)
        _ready.set()
        return
    _ready.set()
    while True:
        job = _jobs.get()
        if job is None:
            break
        kind, payload, reply = job
        try:
            if kind == "render":
                reply.put(_render(ctx, *payload))
            elif kind == "close":
                try:
                    if ctx:
                        ctx.close()
                    if browser:
                        browser.close()
                except Exception:
                    pass
                pw.stop()
                reply.put({"closed": True})
                return
            elif kind == "login":
                reply.put(_guest_login(ctx, *payload))
            elif kind == "auto_login":
                reply.put(_auto_login(ctx, *payload))
            elif kind == "forms":
                reply.put(_extract_forms(ctx, *payload))
            elif kind == "cookie":
                name, value, domain = payload
                ctx.add_cookies([{
                    "name": name, "value": value,
                    "domain": domain, "path": "/"}])
                reply.put({"set": name, "domain": domain})
        except Exception as e:
            reply.put({"error": str(e)})


def _render(ctx, url: str, wait_ms: int = 4000) -> dict:
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=wait_ms)
        except Exception:
            pass
        time.sleep(min(wait_ms, 8000) / 1000)
        title = page.title()
        text = re.sub(r"\s+", " ",
                      page.inner_text("body")
                      if page.query_selector("body") else "")[:4000]
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href).slice(0, 80)")
        media = page.evaluate("""() => {
          const out = new Set();
          const add = u => { if (u && /^https?:/.test(u)) out.add(u); };
          const firstSrcset = s => s && s.split(',')[0].trim().split(/\\s+/)[0];
          document.querySelectorAll(
            'img,video,source,picture').forEach(e => {
            ['src','data-src','data-lazy-src','data-original',
             'data-srcset','poster'].forEach(a => {
              const v = e.getAttribute(a);
              if (a.endsWith('srcset')) add(firstSrcset(v));
              else add(v);
            });
            add(firstSrcset(e.getAttribute('srcset')));
            add(e.currentSrc);
          });
          return [...out].slice(0, 120);
        }""")
        shot = page.screenshot(type="jpeg", quality=60)
        return {"url": page.url, "title": title, "text": text,
                "links": links, "media": media,
                "screenshot": base64.b64encode(shot).decode(),
                "html_len": len(page.content())}
    finally:
        page.close()


def _submit(kind: str, payload=(), timeout: int = 90):
    if not _state["ok"]:
        _start()
    reply: queue.Queue = queue.Queue()
    _jobs.put((kind, payload, reply))
    try:
        return reply.get(timeout=timeout)
    except queue.Empty:
        return {"error": "browser job timed out"}


_thread: threading.Thread | None = None


def _start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    _ready.wait(timeout=30)


def render(url: str, wait_ms: int = 4000) -> dict:
    """Load a page in the guest session; return title, text excerpt,
    links, media URLs, and a screenshot."""
    return _submit("render", (url, wait_ms), timeout=120)


def set_cookie(name: str, value: str, domain: str) -> dict:
    """Set a cookie in the persistent guest context — used to pass
    passkey/session identity into the bot's browser path."""
    return _submit("cookie", (name, value, domain), timeout=30)


USER_SEL = ("input[type=email],input[name*=user i],input[name*=mail i],"
            "input[name*=login i],input[id*=user i],input[id*=mail i],"
            "input[type=text]")
PASS_SEL = "input[type=password]"
SUBMIT_SEL = ("button[type=submit],input[type=submit],"
              "button:has-text('Log in'),button:has-text('Sign in'),"
              "button:has-text('Login'),button:has-text('Continue')")


def _guest_login(ctx, url: str, username: str,
                 password: str) -> dict:
    """Drive a login form as a guest: fill the first plausible
    user/pass pair, submit, report outcome. Session persists in the
    profile for later renders/fetches."""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        u = page.query_selector(USER_SEL)
        p = page.query_selector(PASS_SEL)
        if not u or not p:
            return {"error": "no login form found",
                    "url": page.url, "title": page.title()}
        u.fill(username)
        p.fill(password)
        btn = page.query_selector(SUBMIT_SEL)
        if btn:
            btn.click()
        else:
            p.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(2)
        cookies = ctx.cookies()
        return {"ok": True, "url": page.url, "title": page.title(),
                "cookies": len(cookies),
                "text": re.sub(r"\s+", " ",
                               page.inner_text("body"))[:500]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        page.close()


def _extract_forms(ctx, url: str) -> dict:
    """Render a page and return every form's fields (selector, type,
    name, placeholder, label) — the model uses this to map creds."""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        forms = page.eval_on_selector_all(
            "form", """forms => forms.map(f => ({
                action: f.action || '',
                fields: [...f.querySelectorAll(
                    'input,select,textarea')].map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    aria: el.getAttribute('aria-label') || '',
                    autocomplete: el.autocomplete || ''
                })).filter(x => x.type !== 'hidden')
            }))""")
        return {"url": page.url, "title": page.title(),
                "forms": forms}
    finally:
        page.close()


def _remember(site: str, info: dict) -> None:
    """Store a guest session in memory + data/guest_sessions.json."""
    _sessions[site] = {**info, "ts": time.time()}
    try:
        data = {}
        if SESSIONS_PATH.exists():
            data = json.loads(SESSIONS_PATH.read_text())
        data[site] = _sessions[site]
        SESSIONS_PATH.write_text(json.dumps(data, indent=1))
    except Exception:
        pass


def sessions() -> dict:
    """In-memory + persisted guest session registry."""
    try:
        if SESSIONS_PATH.exists():
            for k, v in json.loads(SESSIONS_PATH.read_text()).items():
                _sessions.setdefault(k, v)
    except Exception:
        pass
    return {"sessions": _sessions}


def _auto_login(ctx, url: str, username: str, password: str,
                llm_fn=None, site: str = "") -> dict:
    """Model-filled login: extract the form, let the LLM map fields
    to credentials (or fall back to heuristics), submit, and store
    the resulting guest session in memory."""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        info = page.eval_on_selector_all(
            "form", """forms => forms.map(f => ({
                action: f.action || '',
                fields: [...f.querySelectorAll(
                    'input,select,textarea')].map((el,i) => ({
                    i, tag: el.tagName.toLowerCase(),
                    type: el.type || '', name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    aria: el.getAttribute('aria-label') || ''
                })).filter(x => x.type !== 'hidden')
            }))""")
        forms = info or []
        if not forms or not forms[0].get("fields"):
            return {"error": "no login form found", "url": page.url}
        fields = forms[0]["fields"]
        # model maps fields -> which gets username/password/submit
        mapping = None
        if llm_fn:
            try:
                raw = llm_fn(
                    "Map login form fields to credentials. Fields: "
                    + json.dumps(fields) +
                    f"\nCredentials: username={username!r} "
                    f"password={password!r}\n"
                    'Reply ONLY JSON: {"user": <field index>, '
                    '"pass": <field index>, "submit": <field index or -1>}')
                m = re.search(r"\{.*\}", raw, re.S)
                mapping = json.loads(m.group(0)) if m else None
            except Exception:
                mapping = None
        # heuristic fallback: type/name/placeholder matching
        def kind(f):
            s = " ".join(str(f.get(k, "")) for k in
                         ("type", "name", "id", "placeholder", "aria")
                         ).lower()
            if f.get("type") == "password":
                return "pass"
            if any(w in s for w in ("user", "mail", "login", "name",
                                    "phone", "account")):
                return "user"
            if f.get("type") in ("submit", "button") or any(
                    w in s for w in ("submit", "log in", "sign in",
                                     "continue", "login")):
                return "submit"
            return "other"
        by_kind = {}
        for f in fields:
            by_kind.setdefault(kind(f), f)
        u = (mapping or {}).get("user")
        p = (mapping or {}).get("pass")
        s_idx = (mapping or {}).get("submit", -1)
        uf = fields[u] if isinstance(u, int) and u < len(fields) \
            else by_kind.get("user")
        pf = fields[p] if isinstance(p, int) and p < len(fields) \
            else by_kind.get("pass")
        if not uf or not pf:
            return {"error": "could not map user/pass fields",
                    "fields": fields}
        sel = lambda f: (f"form >> nth=0 >> "
                         f"{f['tag']}[name='{f.get('name','')}' i]") \
            if f.get("name") else None
        try:
            page.locator(sel(uf) or "form >> nth=0 >> input").first\
                .fill(username)
            page.locator(sel(pf) or "form >> nth=0 >> input[type=password]")\
                .first.fill(password)
        except Exception:
            # positional fallback: fill by index among text inputs
            inputs = page.query_selector_all(
                "form input:not([type=hidden])")
            try:
                inputs[fields.index(uf)].fill(username)
                inputs[fields.index(pf)].fill(password)
            except Exception as e:
                return {"error": f"fill failed: {e}"}
        if isinstance(s_idx, int) and 0 <= s_idx < len(fields):
            sf = fields[s_idx]
            try:
                page.locator(
                    f"form >> nth=0 >> {sf['tag']}").nth(s_idx).click()
            except Exception:
                page.keyboard.press("Enter")
        else:
            page.keyboard.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(2)
        key = site or urllib.parse.urlsplit(page.url).netloc
        sess = {"ok": True, "site": key, "url": page.url,
                "title": page.title(),
                "cookies": len(ctx.cookies()),
                "mapped_by": "llm" if mapping else "heuristic"}
        _remember(key, sess)
        return sess
    except Exception as e:
        return {"error": str(e)}
    finally:
        page.close()


def model_automate(task: str, llm_fn=None) -> dict:
    """Model-driven browsing: ask the LLM for a target URL + extraction
    hint, render it, return structured results. llm_fn(prompt)->str is
    injectable (server passes its Ollama wrapper)."""
    if llm_fn is None:
        return {"error": "no llm_fn provided"}
    prompt = (
        "You are a web navigation model for a media discovery hub. "
        f"Task: {task}\n"
        "Reply with ONLY a JSON object: "
        '{"url": "<best URL to visit>", "extract": "<css selector or '
        'description of what to pull>", "reason": "<short>"}')
    try:
        raw = llm_fn(prompt)
        m = re.search(r"\{.*\}", raw, re.S)
        plan = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return {"error": f"llm plan failed: {e}"}
    url = plan.get("url")
    if not url:
        return {"error": "model returned no url", "raw": raw[:300]}
    r = render(url)
    r["plan"] = plan
    return r


def status() -> dict:
    _start()
    return {"chromium": _state["ok"], "error": _state["err"],
            "profile": str(PROFILE), "headless": True}


def close() -> None:
    return _submit("close", timeout=15)


def guest_login(url: str, username: str, password: str) -> dict:
    """Bot/guest route: attempt form login in the persistent session."""
    return _submit("login", (url, username, password), timeout=120)


def auto_login(url: str, username: str, password: str,
               llm_fn=None, site: str = "") -> dict:
    """Model-filled login: LLM maps form fields to credentials,
    session stored in memory + data/guest_sessions.json."""
    return _submit("auto_login",
                   (url, username, password, llm_fn, site),
                   timeout=150)


def extract_forms(url: str) -> dict:
    return _submit("forms", (url,), timeout=90)
