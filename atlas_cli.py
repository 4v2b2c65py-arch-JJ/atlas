#!/usr/bin/env python3
"""ATLAS CLI — drive every hub capability from the command line.

    atlas feed [--limit N]
    atlas search <q> [--kind manga|anime|web]
    atlas manga search <q> | chapters <id> <source> | watch <id> <source>
    atlas anime search <q> | episodes <id> | watch <query> [ep] [secs]
    atlas fetch <url> [title]
    atlas ingest '<json>'            # {"url"|"magnet"|"m3u8": ...}
    atlas catalogue [kind]
    atlas discover <topic>
    atlas social fetch <url> | post <platform> <src> [caption] | status
    atlas browser render <url> [wait] [shot] | login <url> <user> <pw>
           | auto <task> | forms <url> | sessions | status
    atlas passkeys harvest <url> [max] | analyze <url> [keyphrase] [secs]
           | flow <url> [keyphrase] [secs] | viz <url> [secs]
           | correlate <a> <b> | bind <site> | list [site] | hash <url>
    atlas twitch ensure <channel> [viewers] | release <channel> | status
    atlas tunnel status | login | named | cert <hostname> | quick
    atlas comms send <to> <text> | inbox [node]
    atlas engage like|comment|repost <video> [text]
    atlas timeline [since] [until] [kind] | replay <unix_ts>
    atlas auto | node | auth signup|login <user> <pass>

Global flags: --api URL (default http://localhost:8917)
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "http://localhost:8917"


def call(method: str, path: str, body: dict | None = None) -> dict:
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def get(path: str, **qs) -> dict:
    if qs:
        path += "?" + urllib.parse.urlencode(
            {k: v for k, v in qs.items() if v is not None})
    return call("GET", path)


def post(path: str, body: dict) -> dict:
    return call("POST", path, body)


def out(d) -> None:
    print(json.dumps(d, indent=1, default=str))


def main() -> None:
    global API
    p = argparse.ArgumentParser(prog="atlas", description=__doc__,
                                formatter_class=
                                argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=API, help="hub base URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("feed"); s.add_argument("--limit", type=int)
    s = sub.add_parser("search"); s.add_argument("q")
    s.add_argument("--kind", default="")
    s = sub.add_parser("fetch"); s.add_argument("url")
    s.add_argument("title", nargs="?")
    s = sub.add_parser("ingest"); s.add_argument("json")
    s = sub.add_parser("catalogue"); s.add_argument("kind", nargs="?")
    s = sub.add_parser("discover"); s.add_argument("topic")
    s = sub.add_parser("auto")
    s = sub.add_parser("node")

    m = sub.add_parser("manga")
    m.add_argument("action", choices=["search", "chapters", "watch"])
    m.add_argument("a1"); m.add_argument("a2", nargs="?")
    m.add_argument("a3", nargs="?")

    a = sub.add_parser("anime")
    a.add_argument("action", choices=["search", "episodes", "watch"])
    a.add_argument("a1"); a.add_argument("a2", nargs="?")
    a.add_argument("a3", nargs="?"); a.add_argument("a4", nargs="?")

    so = sub.add_parser("social")
    so.add_argument("action",
                    choices=["fetch", "post", "status", "token"])
    so.add_argument("a1", nargs="?"); so.add_argument("a2", nargs="?")
    so.add_argument("a3", nargs="?"); so.add_argument("a4", nargs="?")

    b = sub.add_parser("browser")
    b.add_argument("action",
                   choices=["render", "login", "auto", "forms",
                            "sessions", "status", "close"])
    b.add_argument("args", nargs="*")

    pk = sub.add_parser("passkeys")
    pk.add_argument("action",
                    choices=["harvest", "analyze", "flow", "viz",
                             "correlate", "bind", "list", "hash",
                             "register"])
    pk.add_argument("args", nargs="*")

    t = sub.add_parser("twitch")
    t.add_argument("action", choices=["ensure", "release", "status"])
    t.add_argument("channel", nargs="?")
    t.add_argument("viewers", nargs="?", type=int)

    st = sub.add_parser("sites")
    st.add_argument("action",
                    choices=["list", "add", "remove", "login"])
    st.add_argument("name", nargs="?")
    st.add_argument("args", nargs="*")

    tu = sub.add_parser("tunnel")
    tu.add_argument("action",
                    choices=["status", "login", "named", "cert",
                             "discover", "configure", "domain",
                             "buy", "domainsearch",
                             "quick", "start", "stop", "restart"])
    tu.add_argument("hostname", nargs="?")
    tu.add_argument("token", nargs="?")

    c = sub.add_parser("comms")
    c.add_argument("action", choices=["send", "inbox"])
    c.add_argument("a1", nargs="?"); c.add_argument("a2", nargs="?")

    e = sub.add_parser("engage")
    e.add_argument("action", choices=["like", "unlike", "repost",
                                      "comment"])
    e.add_argument("video"); e.add_argument("text", nargs="?")

    tl = sub.add_parser("timeline")
    tl.add_argument("since", nargs="?"); tl.add_argument("until", nargs="?")
    tl.add_argument("--kind", default="")
    rp = sub.add_parser("replay"); rp.add_argument("at", type=float)
    tx = sub.add_parser("tax")
    tx.add_argument("action",
                    choices=["statement", "export", "file", "config",
                             "compliance"])
    tx.add_argument("a1", nargs="?"); tx.add_argument("a2", nargs="?")
    tx.add_argument("--node", default=""); tx.add_argument("--since", type=float, default=0)
    tx.add_argument("--until", type=float, default=0)

    pr = sub.add_parser("prime")
    pr.add_argument("action",
                    choices=["status", "wallets", "generate", "query"])
    pr.add_argument("a1", nargs="?"); pr.add_argument("a2", nargs="*")

    ea = sub.add_parser("earn")
    ea.add_argument("action",
                    choices=["status", "revenue", "payout", "settle",
                             "refund", "reconcile"])
    ea.add_argument("a1", nargs="?"); ea.add_argument("a2", nargs="?")
    ea.add_argument("a3", nargs="?"); ea.add_argument("a4", nargs="?")

    bk = sub.add_parser("book")
    bk.add_argument("action",
                    choices=["status", "open", "session", "scrape",
                             "engrave"])
    bk.add_argument("a1", nargs="?"); bk.add_argument("a2", nargs="?")
    bk.add_argument("a3", nargs="?"); bk.add_argument("a4", nargs="?")

    cs = sub.add_parser("cast")
    cs.add_argument("action",
                    choices=["status", "cast", "mirror", "queue",
                             "gguf", "tiers"])
    cs.add_argument("a1", nargs="?"); cs.add_argument("a2", nargs="?")
    cs.add_argument("a3", nargs="?"); cs.add_argument("a4", nargs="?")

    gs = sub.add_parser("gaster")
    gs.add_argument("action",
                    choices=["status", "enter", "repeater", "probe",
                             "strings", "gguf"])
    gs.add_argument("a1", nargs="?"); gs.add_argument("a2", nargs="?")

    nm = sub.add_parser("names")
    nm.add_argument("query", nargs="*",
                    help="name to look up; empty = dataset status")

    un = sub.add_parser("unlock")
    un.add_argument("title", nargs="?")
    un.add_argument("profile", nargs="?")
    un.add_argument("--nodesc", action="store_true",
                    help="skip download (candidates only)")

    lr = sub.add_parser("lore")
    lr.add_argument("key", nargs="?")

    pt = sub.add_parser("partition")
    pt.add_argument("profile", nargs="?")
    pt.add_argument("--run", action="store_true")

    md = sub.add_parser("mods")
    md.add_argument("action", nargs="?",
                    choices=["list", "enrich", "discover"])

    gr = sub.add_parser("gamerefs")
    gr.add_argument("title", nargs="?")
    gr.add_argument("--batch", action="store_true")
    gr.add_argument("--nexus", action="store_true")

    sp = sub.add_parser("spectrum")
    sp.add_argument("action", nargs="?",
                    choices=["status", "analyze", "run", "repeat",
                             "gguf"], default="status")
    sp.add_argument("target", nargs="?",
                    help="text to analyze or dataset path")
    sp.add_argument("--label", default="")
    sp.add_argument("--interval", type=float, default=60)
    sp.add_argument("--cycles", type=int, default=1)

    rs = sub.add_parser("resets")
    rs.add_argument("action", nargs="?",
                    choices=["status", "record", "calendar"],
                    default="status")
    rs.add_argument("node", nargs="?")
    rs.add_argument("to", nargs="?")
    rs.add_argument("frm", nargs="?")
    rs.add_argument("profile", nargs="?")

    gn = sub.add_parser("genesis")
    gn.add_argument("action", nargs="?",
                    choices=["status", "observe", "set"],
                    default="status")
    gn.add_argument("node", nargs="?")
    gn.add_argument("profile", nargs="?")
    gn.add_argument("--ticks", type=int, default=100)

    tl2 = sub.add_parser("tierlayers")
    tl2.add_argument("action", nargs="?",
                     choices=["status", "range", "game",
                              "target"], default="status")
    tl2.add_argument("node", nargs="?")
    tl2.add_argument("skill", nargs="?")
    tl2.add_argument("profile", nargs="?")

    sk = sub.add_parser("stack")
    sk.add_argument("action", nargs="?",
                    choices=["status", "build", "warp", "engage"],
                    default="status")
    sk.add_argument("node", nargs="?")
    sk.add_argument("node2", nargs="?")
    sk.add_argument("plane", nargs="?")
    sk.add_argument("profile", nargs="?")
    sk.add_argument("--env", default="{}")

    mc = sub.add_parser("mechanics")
    mc.add_argument("action", nargs="?",
                    choices=["status", "combos", "expand",
                             "assign", "fire"],
                    default="status")
    mc.add_argument("profile", nargs="?")
    mc.add_argument("node", nargs="?")
    mc.add_argument("--combo", default="")
    mc.add_argument("--trigger", default="")
    mc.add_argument("--env", default="{}")
    mc.add_argument("--limit", type=int, default=0)

    tc = sub.add_parser("timelinecombos")
    tc.add_argument("action", nargs="?",
                    choices=["status", "combos", "expand"],
                    default="status")
    tc.add_argument("profile", nargs="?")

    tl = sub.add_parser("termlib")
    tl.add_argument("action", nargs="?",
                    choices=["status", "ingest", "terms",
                             "apply", "apply_branch"],
                    default="status")
    tl.add_argument("target", nargs="?")
    tl.add_argument("--terms", default="")
    tl.add_argument("--cat", default="")
    tl.add_argument("--limit", type=int, default=0)

    vr = sub.add_parser("variants")
    vr.add_argument("action", nargs="?",
                    choices=["status", "register", "expand", "config"],
                    default="status")
    vr.add_argument("cfg", nargs="?")

    br = sub.add_parser("branches")
    br.add_argument("action", nargs="?",
                    choices=["status", "map"], default="status")
    br.add_argument("center", nargs="?")

    tn = sub.add_parser("truename")
    tn.add_argument("action", nargs="?",
                    choices=["status", "discover"],
                    default="status")
    tn.add_argument("center", nargs="?")

    px = sub.add_parser("proximity")
    px.add_argument("action", nargs="?",
                    choices=["status", "encounter"],
                    default="status")
    px.add_argument("center", nargs="?")

    ft = sub.add_parser("familytree")
    ft.add_argument("action", nargs="?",
                    choices=["status", "render", "summon"],
                    default="status")
    ft.add_argument("node", nargs="?")

    dn = sub.add_parser("dna")
    dn.add_argument("action", nargs="?",
                    choices=["status", "fetch", "expand", "gguf"],
                    default="status")
    dn.add_argument("target", nargs="?",
                    help="accession id or comma list")

    cp = sub.add_parser("charpack")
    cp.add_argument("action", nargs="?",
                    choices=["status", "unlock", "emoji", "pack",
                             "icons", "icon", "probe", "gguf"],
                    default="status")
    cp.add_argument("target", nargs="?",
                    help="phrase to unlock or character name")
    cp.add_argument("icon_src", nargs="?",
                    help="image file to install as character icon")
    gr.add_argument("--limit", type=int, default=25)

    au = sub.add_parser("auth")
    au.add_argument("action", choices=["signup", "login", "status"])
    au.add_argument("user", nargs="?"); au.add_argument("password", nargs="?")

    ns = p.parse_args()
    API = ns.api

    if ns.cmd == "feed":
        out(get("/api/feed"))
    elif ns.cmd == "search":
        out(get("/api/search", q=ns.q, kind=ns.kind or None))
    elif ns.cmd == "fetch":
        out(post("/api/fetch", {"url": ns.url, "title": ns.title}))
    elif ns.cmd == "ingest":
        try:
            body = json.loads(ns.json)
        except Exception:
            body = {"url": ns.json}
        out(post("/api/ingest", body))
    elif ns.cmd == "catalogue":
        out(get("/api/catalogue", kind=ns.kind or None))
    elif ns.cmd == "discover":
        out(post("/api/discover", {"topic": ns.topic}))
    elif ns.cmd == "auto":
        out(get("/api/auto"))
    elif ns.cmd == "node":
        out(get("/api/node"))
    elif ns.cmd == "manga":
        if ns.action == "search":
            out(get("/api/manga", q=ns.a1))
        elif ns.action == "chapters":
            out(get("/api/manga/chapters", id=ns.a1, source=ns.a2))
        else:
            out(post("/api/manga/watch",
                     {"manga_id": ns.a1, "source": ns.a2}))
    elif ns.cmd == "anime":
        if ns.action == "search":
            out(get("/api/aniwatch", q=ns.a1))
        elif ns.action == "episodes":
            out(get("/api/aniwatch/episodes", id=ns.a1))
        else:
            body = {"query": ns.a1, "episode": int(ns.a2 or 1),
                    "seconds": int(ns.a3 or 45)}
            if ns.a4:
                body["mode"] = ns.a4
            out(post("/api/aniwatch/watch", body))
    elif ns.cmd == "social":
        if ns.action == "fetch":
            out(post("/api/social/fetch", {"url": ns.a1}))
        elif ns.action == "post":
            out(post("/api/social/post",
                     {"platform": ns.a1, "src": ns.a2,
                      "caption": ns.a3 or ""}))
        elif a == "token":
            # social token <platform> <access_token> [user_or_page_id]
            out(post("/api/social/token", {
                "platform": ns.a1, "access_token": ns.a2,
                "user_id": ns.a3 or "", "open_id": ns.a4 or ""}))
        else:
            out(get("/api/social"))
    elif ns.cmd == "browser":
        a = ns.action
        if a == "status":
            out(get("/api/browser"))
        elif a == "sessions":
            out(post("/api/browser", {"action": "sessions"}))
        elif a == "close":
            out(post("/api/browser", {"action": "close"}))
        elif a == "render":
            url, wait = ns.args[0], int(ns.args[1]) if len(ns.args) > 1 else 4000
            shot = len(ns.args) > 2 and ns.args[2] == "shot"
            out(get("/api/browser", url=url, wait=wait, shot=1 if shot else 0))
        elif a == "login":
            out(post("/api/browser", {"action": "guest_login",
                                     "url": ns.args[0],
                                     "username": ns.args[1],
                                     "password": ns.args[2]}))
        elif a == "auto":
            out(post("/api/browser", {"action": "auto",
                                     "task": " ".join(ns.args)}))
        elif a == "forms":
            out(post("/api/browser", {"action": "forms",
                                     "url": ns.args[0]}))
    elif ns.cmd == "passkeys":
        a = ns.action
        args = ns.args
        if a == "list":
            out(get("/api/passkeys", site=args[0] if args else None))
        elif a == "correlate":
            out(post("/api/passkeys",
                     {"action": "correlate", "a": args[0], "b": args[1]}))
        elif a == "bind":
            out(post("/api/passkeys",
                     {"action": "bind", "site": args[0]}))
        elif a == "hash":
            out(post("/api/passkeys",
                     {"action": "hash", "url": args[0]}))
        elif a == "register":
            out(post("/api/passkeys",
                     {"action": "register", "url": args[0],
                      "via": args[1] if len(args) > 1 else "",
                      "kind": args[2] if len(args) > 2 else None}))
        elif a == "analyze":
            out(post("/api/passkeys",
                     {"action": "analyze", "url": args[0],
                      "keyphrase": args[1] if len(args) > 1 else None,
                      "seconds": int(args[2]) if len(args) > 2 else 45}))
        elif a == "flow":
            out(post("/api/passkeys",
                     {"action": "flow", "url": args[0],
                      "keyphrase": args[1] if len(args) > 1 else None,
                      "seconds": int(args[2]) if len(args) > 2 else 60}))
        elif a == "viz":
            out(post("/api/passkeys",
                     {"action": "viz", "url": args[0],
                      "seconds": int(args[1]) if len(args) > 1 else 30}))
        else:  # harvest
            out(post("/api/passkeys",
                     {"action": "harvest", "url": args[0],
                      "max_try": int(args[1]) if len(args) > 1 else 15}))
    elif ns.cmd == "twitch":
        if ns.action == "status":
            out(get("/api/twitch"))
        else:
            out(post("/api/twitch",
                     {"action": ns.action, "channel": ns.channel,
                      "viewers": ns.viewers or 3}))
    elif ns.cmd == "sites":
        a = ns.action
        if a == "list":
            out(get("/api/sites"))
        elif a == "add":
            # sites add <name> <url> [username] [password] [kind]
            args = [ns.name or ""] + list(ns.args)
            out(post("/api/sites", {
                "action": "add", "name": args[0], "url": args[1],
                "username": args[2] if len(args) > 2 else "",
                "password": args[3] if len(args) > 3 else "",
                "kind": args[4] if len(args) > 4 else ""}))
        elif a == "remove":
            out(post("/api/sites",
                     {"action": "remove", "name": ns.name}))
        else:  # login
            out(post("/api/sites",
                     {"action": "login", "name": ns.name}))
    elif ns.cmd == "tunnel":
        a = ns.action
        if a in ("status",):
            out(get("/api/tunnel"))
        elif a == "login":
            out(post("/api/tunnel", {"action": "login"}))
        elif a == "cert":
            out(post("/api/tunnel",
                     {"action": "cert", "hostname": ns.hostname}))
        elif a == "discover":
            # re-pull account/zone/hostname from the stored api_token
            out(post("/api/tunnel",
                     {"action": "discover",
                      "api_token": ns.token}))
        elif a == "configure":
            out(post("/api/tunnel",
                     {"action": "configure",
                      "hostname": ns.hostname}))
        elif a == "domainsearch":
            out(post("/api/tunnel",
                     {"action": "domainsearch", "q": ns.hostname}))
        elif a == "buy":
            # register domain + auto route tunnel — REAL PURCHASE,
            # costs the registrar price shown by domainsearch
            out(post("/api/tunnel",
                     {"action": "buy", "domain": ns.hostname}))
        elif a == "domain":
            # one-shot: discover zones -> configure host -> provision
            d = post("/api/tunnel", {"action": "discover"})
            out(d)
            if ns.hostname:
                out(post("/api/tunnel",
                         {"action": "configure",
                          "hostname": ns.hostname}))
                out(post("/api/tunnel", {"action": "named"}))
        elif a == "named":
            # hostname arg -> configure it first, then provision
            if ns.hostname:
                out(post("/api/tunnel",
                         {"action": "configure",
                          "hostname": ns.hostname}))
            out(post("/api/tunnel", {"action": "named"}))
        elif a == "quick":
            out(post("/api/tunnel", {"action": "restart"}))
        else:
            out(post("/api/tunnel", {"action": a}))
    elif ns.cmd == "comms":
        if ns.action == "send":
            out(post("/api/comms", {"to": ns.a1, "text": ns.a2 or ""}))
        else:
            out(get("/api/comms", node=ns.a1))
    elif ns.cmd == "engage":
        out(post("/api/engage", {"video": ns.video, "action": ns.action,
                                "text": ns.text or ""}))
    elif ns.cmd == "timeline":
        out(get("/api/timeline", since=ns.since, until=ns.until,
                kind=ns.kind or None))
    elif ns.cmd == "replay":
        out(get("/api/replay", at=ns.at))
    elif ns.cmd == "tax":
        a = ns.action
        if a == "config":
            out(post("/api/tax", {"action": "config",
                                  "authority": ns.a1 or "",
                                  "endpoint": ns.a2 or "",
                                  "token": ""}))
        elif a == "export":
            out(post("/api/tax", {"action": "export",
                                  "node": ns.node or None,
                                  "since": ns.since, "until": ns.until}))
        elif a == "file":
            out(post("/api/tax", {"action": "file",
                                  "node": ns.node or None,
                                  "since": ns.since, "until": ns.until}))
        elif a == "compliance":
            out(get("/api/tax", action="compliance",
                    node=ns.node or None,
                    since=ns.since or None, until=ns.until or None))
        else:
            out(get("/api/tax", node=ns.node or None,
                    since=ns.since or None, until=ns.until or None))
    elif ns.cmd == "prime":
        a = ns.action
        if a == "status":
            out(get("/api/prime"))
        elif a == "wallets":
            out(get("/api/prime", action="wallets"))
        elif a == "generate":
            out(post("/api/prime", {"action": "generate",
                                    "prompt": ns.a1 or ""}))
        else:
            out(post("/api/prime", {"action": "query",
                                    "query": " ".join(
                                        [ns.a1 or ""] +
                                        list(ns.a2 or []))}))
    elif ns.cmd == "earn":
        a = ns.action
        if a == "status":
            out(get("/api/earn", node=ns.a1))
        elif a == "revenue":
            out(post("/api/earn", {"action": "revenue",
                                   "amount": float(ns.a1 or 0),
                                   "kind": ns.a2 or "donation",
                                   "node": ns.a3 or "",
                                   "ref": ns.a4 or ""}))
        elif a == "payout":
            out(post("/api/earn", {"action": "payout",
                                   "amount": float(ns.a1 or 0),
                                   "method": ns.a2 or "btc",
                                   "address": ns.a3 or "",
                                   "node": ns.a4 or ""}))
        elif a == "reconcile":
            out(post("/api/earn", {"action": "reconcile"}))
        elif a == "refund":
            out(post("/api/earn", {"action": "refund",
                                   "id": int(ns.a1 or 0),
                                   "node": ns.a2 or ""}))
        else:
            out(post("/api/earn", {"action": "settle",
                                   "id": int(ns.a1 or 0),
                                   "node": ns.a2 or "",
                                   "tx": ns.a3 or ""}))
    elif ns.cmd == "book":
        a = ns.action
        if a == "status":
            out(get("/api/book"))
        elif a == "open":
            out(post("/api/book", {"action": "open",
                                   "name": ns.a1 or "",
                                   "token": ns.a2 or ""}))
        elif a == "session":
            out(post("/api/book", {"action": "session",
                                   "kind": ns.a1 or "note",
                                   "detail": ns.a2 or ""}))
        elif a == "scrape":
            out(post("/api/book", {"action": "scrape",
                                   "url": ns.a1 or ""}))
        elif a == "engrave":
            # atlas book engrave <src> <at> "<instruction>" [seconds]
            out(post("/api/book", {"action": "engrave",
                                   "src": ns.a1 or "",
                                   "at": float(ns.a2 or 0),
                                   "instruction": ns.a3 or "",
                                   "seconds": float(ns.a4 or 8)}))
    elif ns.cmd == "cast":
        a = ns.action
        if a == "status":
            out(get("/api/cast"))
        elif a == "tiers":
            out(get("/api/cast"))
        elif a == "cast":
            # atlas cast cast <name> <src> [at] [repeats]
            out(post("/api/cast", {"action": "cast",
                                   "name": ns.a1 or "",
                                   "src": ns.a2 or "",
                                   "at": float(ns.a3 or 0),
                                   "repeats": int(ns.a4 or 1)}))
        elif a == "queue":
            out(post("/api/cast", {"action": "queue",
                                   "triggers": [{"name": ns.a1 or "",
                                                 "src": ns.a2 or "",
                                                 "at": float(ns.a3 or 0)}]}))
        elif a == "gguf":
            out(post("/api/cast", {"action": "gguf"}))
        else:
            out(post("/api/cast", {"action": "mirror",
                                   "name": ns.a1 or "",
                                   "src": ns.a2 or "",
                                   "at": float(ns.a3 or 0)}))
    elif ns.cmd == "gaster":
        a = ns.action
        win = "data/undertale/extracted/Undertale v1.001/data.win"
        if a == "status":
            out(get("/api/gaster"))
        elif a == "enter":
            out(post("/api/gaster", {"action": "enter",
                                     "id": ns.a1 or ""}))
        elif a == "repeater":
            out(post("/api/gaster", {"action": "repeater",
                                     "trigger": ns.a1 or "",
                                     "cycles": int(ns.a2 or 3)}))
        elif a == "probe":
            out(post("/api/gaster", {"action": "probe",
                                     "win": ns.a1 or win}))
        elif a == "strings":
            out(post("/api/gaster", {"action": "strings",
                                     "win": ns.a1 or win}))
        else:
            out(post("/api/gaster", {"action": "gguf"}))
    elif ns.cmd == "names":
        q = " ".join(ns.query or [])
        if ns.query and ns.query[0] in (
                "vote", "plate", "weights", "status"):
            a = ns.query[0]
            if a == "vote":
                out(post("/api/consensus",
                         {"action": "vote",
                          "name": " ".join(ns.query[1:])}))
            elif a == "plate":
                out(post("/api/consensus",
                         {"action": "plate",
                          "plate": ns.query[1] if len(ns.query) > 1
                          else "",
                          "alias": ns.query[2] if len(ns.query) > 2
                          else ""}))
            elif a == "weights":
                out(post("/api/consensus", {"action": "weights"}))
            else:
                out(get("/api/consensus"))
        elif q:
            out(get("/api/names", q=q))
        else:
            out(get("/api/names"))
    elif ns.cmd == "unlock":
        if not ns.title:
            out(get("/api/unlock", title="", profile=ns.profile))
        else:
            out(get("/api/unlock", title=ns.title,
                    profile=ns.profile or "default",
                    download="0" if ns.nodesc else "1"))
    elif ns.cmd == "lore":
        out(get("/api/lore", key=ns.key or None))
    elif ns.cmd == "partition":
        out(get("/api/partition", profile=ns.profile or "default",
                run="1" if ns.run else None))
    elif ns.cmd == "mods":
        a = ns.action or "list"
        if a == "enrich":
            out(get("/api/mods", enrich="1"))
        elif a == "discover":
            out(get("/api/mods", discover="1"))
        else:
            out(get("/api/mods"))
    elif ns.cmd == "gamerefs":
        if ns.nexus:
            out(get("/api/gamerefs", nexus="1", limit=ns.limit))
        elif ns.batch:
            out(get("/api/gamerefs", batch="1", limit=ns.limit))
        elif ns.title:
            out(get("/api/gamerefs", title=ns.title))
        else:
            out(get("/api/gamerefs"))
    elif ns.cmd == "spectrum":
        a = ns.action or "status"
        if a == "analyze":
            out(get("/api/spectrum", action="analyze",
                    text=ns.target or ""))
        elif a == "run":
            out(get("/api/spectrum", action="run",
                    dataset=ns.target or "",
                    label=ns.label or None))
        elif a == "repeat":
            out(get("/api/spectrum", action="repeat",
                    interval=ns.interval, cycles=ns.cycles))
        elif a == "gguf":
            out(get("/api/spectrum", action="gguf"))
        else:
            out(get("/api/spectrum"))
    elif ns.cmd == "resets":
        a = ns.action or "status"
        if a == "calendar":
            out(get("/api/resets", action="calendar"))
        elif a == "record":
            out(get("/api/resets", action="record",
                    node=ns.node or "", to=ns.to or "",
                    **{"from": ns.frm or ""},
                    profile=ns.profile or "default"))
        else:
            out(get("/api/resets"))
    elif ns.cmd == "genesis":
        a = ns.action or "status"
        if a == "observe":
            out(get("/api/genesis", action="observe",
                    node=ns.node or "",
                    profile=ns.profile or "default",
                    ticks=ns.ticks))
        elif a == "set":
            out(get("/api/genesis", action="set"))
        else:
            out(get("/api/genesis"))
    elif ns.cmd == "tierlayers":
        a = ns.action or "status"
        if a == "range":
            out(get("/api/tierlayers", action="range",
                    node=ns.node or "",
                    profile=ns.profile or "default"))
        elif a == "game":
            out(get("/api/tierlayers", action="game"))
        elif a == "target":
            out(get("/api/tierlayers", action="target",
                    node=ns.node or "", skill=ns.skill or "",
                    profile=ns.profile or "default"))
        else:
            out(get("/api/tierlayers"))
    elif ns.cmd == "stack":
        a = ns.action or "status"
        if a == "build":
            out(get("/api/stack", action="build",
                    node=ns.node or "",
                    profile=ns.profile or "default"))
        elif a == "warp":
            out(get("/api/stack", action="warp",
                    node=ns.node or "", plane=ns.plane or "",
                    env=ns.env,
                    profile=ns.profile or "default"))
        elif a == "engage":
            out(get("/api/stack", action="engage",
                    a=ns.node or "", b=ns.node2 or "",
                    plane=ns.plane or "plane_3d", env=ns.env,
                    profile=ns.profile or "default"))
        else:
            out(get("/api/stack",
                    profile=ns.profile or "default"))
    elif ns.cmd == "mechanics":
        a = ns.action or "status"
        if a == "combos":
            out(get("/api/mechanics", action="combos"))
        elif a == "expand":
            out(get("/api/mechanics", action="expand",
                    profile=ns.profile or "default",
                    limit=ns.limit))
        elif a == "assign":
            out(get("/api/mechanics", action="assign",
                    node=ns.node or "", combo=ns.combo,
                    trigger=ns.trigger,
                    profile=ns.profile or "default"))
        elif a == "fire":
            out(get("/api/mechanics", action="fire",
                    trigger=ns.trigger or "",
                    env=ns.env,
                    profile=ns.profile or "default"))
        else:
            out(get("/api/mechanics",
                    profile=ns.profile or "default"))
    elif ns.cmd == "timelinecombos":
        a = ns.action or "status"
        if a == "combos":
            out(get("/api/timelinecombos", action="combos"))
        elif a == "expand":
            out(get("/api/timelinecombos", action="expand",
                    profile=ns.profile or "default"))
        else:
            out(get("/api/timelinecombos",
                    profile=ns.profile or "default"))
    elif ns.cmd == "termlib":
        a = ns.action or "status"
        if a == "ingest":
            out(get("/api/termlib", action="ingest"))
        elif a == "terms":
            out(get("/api/termlib", action="terms",
                    cat=ns.cat or None, limit=ns.limit))
        elif a == "apply":
            out(get("/api/termlib", action="apply",
                    node=ns.target or "",
                    terms=ns.terms))
        elif a == "apply_branch":
            out(get("/api/termlib", action="apply_branch",
                    branch=ns.target or "",
                    terms=ns.terms, limit=ns.limit))
        else:
            out(get("/api/termlib"))
    elif ns.cmd == "variants":
        a = ns.action or "status"
        if a == "register":
            out(get("/api/variants", action="register"))
        elif a == "expand":
            out(get("/api/variants", action="expand"))
        elif a == "config":
            out(get("/api/variants", action="config",
                    cfg=ns.cfg or ""))
        else:
            out(get("/api/variants"))
    elif ns.cmd == "branches":
        a = ns.action or "status"
        if a == "map":
            out(get("/api/branches", action="map",
                    center=ns.center or ""))
        else:
            out(get("/api/branches"))
    elif ns.cmd == "truename":
        a = ns.action or "status"
        if a == "discover":
            out(get("/api/truename", action="discover",
                    center=ns.center or "reaper_sans"))
        else:
            out(get("/api/truename"))
    elif ns.cmd == "proximity":
        a = ns.action or "status"
        if a == "encounter":
            out(get("/api/proximity", action="encounter",
                    center=ns.center or "reaper_sans"))
        else:
            out(get("/api/proximity"))
    elif ns.cmd == "familytree":
        a = ns.action or "status"
        if a == "render":
            out(get("/api/familytree", action="render"))
        elif a == "summon":
            out(get("/api/familytree", action="summon",
                    node=ns.node or ""))
        else:
            out(get("/api/familytree"))
    elif ns.cmd == "dna":
        a = ns.action or "status"
        if a == "fetch":
            out(get("/api/dna", action="fetch", id=ns.target or ""))
        elif a == "expand":
            out(get("/api/dna", action="expand",
                    ids=ns.target or ""))
        elif a == "gguf":
            out(get("/api/dna", action="gguf"))
        else:
            out(get("/api/dna"))
    elif ns.cmd == "charpack":
        a = ns.action or "status"
        if a == "unlock":
            out(get("/api/charpack", action="unlock",
                    phrase=ns.target or ""))
        elif a == "emoji":
            out(get("/api/charpack", action="emoji",
                    char=ns.target or ""))
        elif a == "pack":
            out(get("/api/charpack", action="pack"))
        elif a == "icons":
            out(get("/api/charpack", action="icons"))
        elif a == "probe":
            out(get("/api/charpack", action="probe"))
        elif a == "icon":
            import base64
            data = Path(ns.icon_src).read_bytes()
            out(post("/api/charpack", {
                "action": "install_icon", "char": ns.target or "",
                "filename": Path(ns.icon_src).name,
                "data_b64": base64.b64encode(data).decode()}))
        elif a == "gguf":
            out(get("/api/charpack", action="gguf"))
        else:
            out(get("/api/charpack"))
    elif ns.cmd == "auth":
        if ns.action == "status":
            out(get("/api/auth/status"))
        else:
            out(post(f"/api/auth/{ns.action}",
                     {"username": ns.user, "password": ns.password}))


if __name__ == "__main__":
    main()
