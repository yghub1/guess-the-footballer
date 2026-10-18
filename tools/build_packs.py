#!/usr/bin/env python3
"""Build league and club player packs for the game from Wikidata and Wikipedia.

For each pack it picks the most famous current and former players (fame = number of
Wikipedia language editions with an article about the player), with their English and
Arabic names, nicknames, a photo, and whether they are at that club / in that league now.
Wikidata gives the club history; English Wikipedia's infobox gives the current club,
because it is kept more up to date.

Run it again each season to refresh who plays where:

    python3 tools/build_packs.py

It writes packs.js next to index.html.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "GuessTheFootballer/1.0 (https://github.com/yghub1/guess-the-footballer)"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "packs.js")

CURRENT_PER_PACK = 35   # most famous players at the club / in the league now
FORMER_PER_PACK = 45    # most famous players who used to be there

# key: (label, Wikidata id, "club" or "league", mic language)
PACKS = {
    "premier-league": ("🏴 Premier League", "Q9448", "league", "en-US"),
    "la-liga": ("🇪🇸 La Liga", "Q324867", "league", "en-US"),
    "bundesliga": ("🇩🇪 Bundesliga", "Q82595", "league", "en-US"),
    "serie-a": ("🇮🇹 Serie A", "Q15804", "league", "en-US"),
    "saudi-pro-league": ("🇸🇦 Saudi Pro League", "Q255633", "league", "en-US"),
    "egyptian-premier-league": ("🇪🇬 Egyptian Premier League", "Q680619", "league", "ar-EG"),
    "real-madrid": ("⚪ Real Madrid", "Q8682", "club", "en-US"),
    "barcelona": ("🔵🔴 Barcelona", "Q7156", "club", "en-US"),
    "manchester-united": ("🔴 Manchester United", "Q18656", "club", "en-US"),
    "manchester-city": ("🩵 Manchester City", "Q50602", "club", "en-US"),
    "liverpool": ("🔴 Liverpool", "Q1130849", "club", "en-US"),
    "arsenal": ("🔴 Arsenal", "Q9617", "club", "en-US"),
    "chelsea": ("🔵 Chelsea", "Q9616", "club", "en-US"),
    "bayern": ("🔴 Bayern Munich", "Q15789", "club", "en-US"),
    "juventus": ("⚫⚪ Juventus", "Q1422", "club", "en-US"),
    "al-ahly": ("🦅 Al Ahly", "Q223566", "club", "ar-EG"),
    "zamalek": ("🏹 Zamalek", "Q286504", "club", "ar-EG"),
}


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def sparql(query):
    """Run a Wikidata query, keeping answers in tools/.cache so a re-run is quick (delete it to refresh)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(query.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    rows = _sparql(query)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return rows


def _sparql(query):
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.load(r)["results"]["bindings"]
        except Exception as e:  # rate limit or timeout: wait and retry
            print("  retrying after", e)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Wikidata query kept failing")


def val(row, key):
    return row[key]["value"] if key in row else None


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def year(date):
    """Year from a Wikidata date, or None for missing or 'unknown value' dates."""
    try:
        return int(date[:4]) if date else None
    except ValueError:
        return None


# ---------- 1. Find the players for each pack ----------

def pack_members(target, kind):
    """Every male footballer who played for the club (or a club in the league), with fame."""
    club_match = f"?st ps:P54 wd:{target}." if kind == "club" else f"?st ps:P54 ?club. ?club wdt:P118 wd:{target}."
    min_links = 6 if kind == "club" else 12
    rows = sparql(f"""
      SELECT ?p ?links WHERE {{
        ?p p:P54 ?st. {club_match}
        ?p wdt:P106 wd:Q937857; wdt:P21 wd:Q6581097; wikibase:sitelinks ?links.
        FILTER(?links >= {min_links})
      }}""")
    return {qid(val(r, "p")): int(val(r, "links")) for r in rows}


def club_set(target, kind):
    """The clubs that count as 'in' the pack (one club, or every club in the league)."""
    if kind == "club":
        return {target}
    rows = sparql(f"""SELECT DISTINCT ?club WHERE {{
        ?club p:P118 ?s. ?s ps:P118 wd:{target}. FILTER NOT EXISTS {{ ?s pq:P582 ?end }}
        ?club wdt:P31/wdt:P279* wd:Q476028.   # association football club
      }}""")
    return {qid(val(r, "club")) for r in rows}


# ---------- 2. Details for the chosen players ----------

def chunks(items, n):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def player_details(qids):
    info = {}
    for part in chunks(qids, 150):
        values = " ".join("wd:" + q for q in part)
        rows = sparql(f"""
          SELECT ?p ?en ?ar ?img ?enwiki ?arwiki ?death WHERE {{
            VALUES ?p {{ {values} }}
            OPTIONAL {{ ?p rdfs:label ?en FILTER(lang(?en) = "en") }}
            OPTIONAL {{ ?p rdfs:label ?ar FILTER(lang(?ar) = "ar") }}
            OPTIONAL {{ ?p wdt:P18 ?img }}
            OPTIONAL {{ ?p wdt:P570 ?death }}
            OPTIONAL {{ ?enwiki schema:about ?p; schema:isPartOf <https://en.wikipedia.org/> }}
            OPTIONAL {{ ?arwiki schema:about ?p; schema:isPartOf <https://ar.wikipedia.org/> }}
          }}""")
        for r in rows:
            q = qid(val(r, "p"))
            d = info.setdefault(q, {"also": set(), "nick": set(), "clubs": []})
            d["en"] = d.get("en") or val(r, "en")
            d["ar"] = d.get("ar") or val(r, "ar")
            d["img"] = d.get("img") or val(r, "img")
            d["dead"] = d.get("dead") or bool(val(r, "death"))
            for key in ("enwiki", "arwiki"):
                if val(r, key):
                    d[key] = urllib.parse.unquote(val(r, key).rsplit("/wiki/", 1)[-1]).replace("_", " ")
        time.sleep(1)

        rows = sparql(f"""
          SELECT ?p ?alias ?nick WHERE {{
            VALUES ?p {{ {values} }}
            {{ ?p skos:altLabel ?alias FILTER(lang(?alias) IN ("en", "ar")) }}
            UNION {{ ?p wdt:P1449 ?nick FILTER(lang(?nick) IN ("en", "ar", "es", "pt", "it", "fr", "de")) }}
          }}""")
        for r in rows:
            d = info.setdefault(qid(val(r, "p")), {"also": set(), "nick": set(), "clubs": []})
            if val(r, "alias"):
                d["also"].add(val(r, "alias"))
            if val(r, "nick"):
                d["nick"].add(val(r, "nick"))
        time.sleep(1)

        rows = sparql(f"""
          SELECT ?p ?club ?clubLabel ?clubAr ?start ?end WHERE {{
            VALUES ?p {{ {values} }}
            ?p p:P54 ?st. ?st ps:P54 ?club.
            FILTER NOT EXISTS {{ ?club wdt:P31/wdt:P279* wd:Q6979593 }}   # skip national teams
            OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
            OPTIONAL {{ ?club rdfs:label ?clubAr FILTER(lang(?clubAr) = "ar") }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
          }}""")
        for r in rows:
            d = info.setdefault(qid(val(r, "p")), {"also": set(), "nick": set(), "clubs": []})
            d["clubs"].append({
                "q": qid(val(r, "club")), "name": val(r, "clubLabel"), "ar": val(r, "clubAr"),
                "start": year(val(r, "start")), "end": year(val(r, "end")),
            })
        time.sleep(1)
    return info


def current_club(clubs):
    """The club a player is at now: an open spell that started most recently of all spells."""
    if not clubs:
        return None
    latest_start = max((c["start"] or 0) for c in clubs)
    open_spells = [c for c in clubs if c["end"] is None and (c["start"] or 0) >= latest_start and (c["start"] or 0) > 0]
    if not open_spells:
        return None
    # A player whose newest spell started long ago with no end date is probably missing data
    if open_spells[0]["start"] < 2008:
        return None
    return open_spells[0]


def wiki_api(lang, params):
    """Call the Wikipedia API (cached in tools/.cache like the Wikidata queries)."""
    params = dict(params, action="query", format="json", redirects=1)
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                data = json.load(r).get("query", {})
            break
        except Exception as e:
            print("  retrying after", e)
            time.sleep(15 * (attempt + 1))
    else:
        return {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    time.sleep(1.5)
    return data


def by_requested_title(data, requested):
    """Map every title we asked for to its page in an API answer (through redirects).
    Several requested titles can lead to the same page, e.g. "Real Madrid CF" and "Real Madrid C.F."."""
    hops = {x["from"]: x["to"] for x in data.get("normalized", []) + data.get("redirects", [])}
    pages = {pg["title"]: pg for pg in data.get("pages", {}).values()}
    out = {}
    for t in requested:
        final = t
        for _ in range(3):
            final = hops.get(final, final)
        if final in pages:
            out[t] = pages[final]
    return out


LINK = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")
NOISE = re.compile(r"<ref.*?(?:</ref>|/>)|\{\{.*?\}\}|'{2,}")


def current_club_from_infobox(wikitext):
    """(link target, shown text) of '| currentclub =' in a footballer infobox, or (None, None) if the
    player has no club now. Managers, coaches and directors don't count: their 'current club' is
    the club they work for, not one they play for."""
    m = re.search(r"\|\s*current_?club\s*=([^\n]*)", wikitext or "")
    if not m:
        return None, None
    value = NOISE.sub("", m.group(1)).strip()
    if not value or re.search(r"retired|free agent|unattached|none|manag|coach|director|president|chairman|board|staff|ambassador", value, re.I):
        return None, None
    # Still playing only if a playing spell is open-ended ("| years9 = 2023–")
    if not re.search(r"\|\s*years\d+\s*=\s*\d{4}\s*[–-]\s*(?:\n|\||$)", wikitext):
        return None, None
    # An open managerial spell ("| manageryears3 = 2024–") means they manage now rather than play
    if re.search(r"\|\s*manageryears\d*\s*=\s*\d{4}\s*[–-]\s*(?:\n|\||$)", wikitext):
        return None, None
    link = LINK.search(value)
    if link:
        return link.group(1).strip(), (link.group(2) or link.group(1)).strip()
    return None, None


def english_wikipedia(titles):
    """Short description, photo and current club (from the infobox) for each article."""
    out = {t: {} for t in titles}
    for part in chunks(titles, 50):
        data = by_requested_title(wiki_api("en", {"prop": "pageimages|description", "pithumbsize": 500, "titles": "|".join(part)}), part)
        for t, pg in data.items():
            if t in out:
                out[t]["desc"] = pg.get("description", "")
                out[t]["img"] = pg.get("thumbnail", {}).get("source")
                out[t]["title"] = pg.get("title", t)
        data = by_requested_title(wiki_api("en", {"prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(part)}), part)
        for t, pg in data.items():
            if t in out and pg.get("revisions"):
                text = pg["revisions"][0]["slots"]["main"].get("*", "")
                out[t]["club_link"], out[t]["club"] = current_club_from_infobox(text)
                out[t]["has_infobox"] = "nfobox football biography" in text
    return out


def wikidata_ids(titles):
    """Wikidata id for each English Wikipedia title (following redirects)."""
    out = {}
    for part in chunks(sorted(set(titles)), 50):
        data = by_requested_title(wiki_api("en", {"prop": "pageprops", "ppprop": "wikibase_item", "titles": "|".join(part)}), part)
        for t, pg in data.items():
            if pg.get("pageprops", {}).get("wikibase_item"):
                out[t] = pg["pageprops"]["wikibase_item"]
    return out


SQUAD_PLAYER = re.compile(r"\{\{\s*(?:fs|football squad) player[^}]*?name\s*=\s*\[\[([^\]|#]+)", re.I)


def club_squads(club_titles):
    """Players listed in the 'Current squad' tables of each club's English Wikipedia page."""
    out = {}
    for part in chunks(club_titles, 10):
        data = by_requested_title(wiki_api("en", {"prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(part)}), part)
        for t, pg in data.items():
            if pg.get("revisions"):
                text = pg["revisions"][0]["slots"]["main"].get("*", "")
                out[t] = [m.strip() for m in SQUAD_PLAYER.findall(text)]
    return out


def enwiki_titles(qids):
    """English Wikipedia title for each Wikidata id."""
    out = {}
    for part in chunks(qids, 150):
        rows = sparql(f"""SELECT ?x ?t WHERE {{ VALUES ?x {{ {" ".join("wd:" + q for q in part)} }}
            ?a schema:about ?x; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?t. }}""")
        out.update({qid(val(r, "x")): val(r, "t") for r in rows})
    return out


def fame(qids):
    """Number of Wikipedia editions with an article about each player."""
    out = {}
    for part in chunks(qids, 150):
        rows = sparql(f"""SELECT ?p ?links WHERE {{ VALUES ?p {{ {" ".join("wd:" + q for q in part)} }}
            ?p wikibase:sitelinks ?links. }}""")
        out.update({qid(val(r, "p")): int(val(r, "links")) for r in rows})
    return out


def arabic_photos(titles):
    """Photos from Arabic Wikipedia, for players with no photo anywhere else."""
    out = {}
    for part in chunks(titles, 50):
        for t, pg in by_requested_title(wiki_api("ar", {"prop": "pageimages", "pithumbsize": 500, "titles": "|".join(part)}), part).items():
            if "thumbnail" in pg:
                out[t] = pg["thumbnail"]["source"]
    return out


def commons_url(file_uri):
    name = urllib.parse.unquote(file_uri.rsplit("/", 1)[-1])
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(name) + "?width=500"


# ---------- 3. Put it together ----------

def main():
    members, scopes = {}, {}
    for key, (label, target, kind, _) in PACKS.items():
        print("Finding players for", label)
        members[key] = pack_members(target, kind)
        scopes[key] = club_set(target, kind)
        print(f"  {len(members[key])} players, {len(scopes[key])} clubs")
        time.sleep(2)

    # Today's squads, from the "Current squad" tables on the clubs' Wikipedia pages.
    # Wikidata often misses recent signings (especially at Egyptian clubs), so add them in.
    print("Reading current squads")
    club_titles = enwiki_titles(set().union(*scopes.values()))
    squad_lists = club_squads(sorted(set(club_titles.values())))
    squad_ids = wikidata_ids([t for ts in squad_lists.values() for t in ts])
    squad_fame = fame(set(squad_ids.values()))
    squads = {}
    for key in PACKS:
        squads[key] = {squad_ids[t] for club in scopes[key] for t in squad_lists.get(club_titles.get(club), [])
                       if t in squad_ids and squad_ids[t] in squad_fame}
        members[key].update({q: squad_fame[q] for q in squads[key]})
        print(f"  {PACKS[key][0]}: {len(squads[key])} in current squads")

    # Details for everyone famous enough to possibly be picked, plus every current squad player
    candidates = set().union(*squads.values())
    for key in PACKS:
        ranked = sorted(members[key].items(), key=lambda kv: -kv[1])
        candidates.update(q for q, _ in ranked[:300])
    print("Fetching details for", len(candidates), "players")
    info = player_details(candidates)

    # English Wikipedia is better kept up to date than Wikidata: use it for the name, the current
    # club (infobox) and to drop people who aren't really footballers (description).
    print("Reading English Wikipedia articles")
    enwiki = english_wikipedia([d["enwiki"] for d in info.values() if d.get("enwiki")])
    club_ids = wikidata_ids([w["club_link"] for w in enwiki.values() if w.get("club_link")])
    ar_imgs = arabic_photos([d["arwiki"] for d in info.values() if d.get("arwiki") and not d.get("img")
                             and not enwiki.get(d.get("enwiki"), {}).get("img")])

    players, packs = {}, {}
    for key, (label, target, kind, lang) in PACKS.items():
        current, former = [], []
        for q, links in sorted(members[key].items(), key=lambda kv: -kv[1]):
            d = info.get(q)
            if not d or not d.get("en"):
                continue
            w = enwiki.get(d.get("enwiki"), {})
            if w and not w.get("has_infobox") and not re.search(r"football|soccer", w.get("desc", ""), re.I):
                continue   # not a footballer (singers, actors with a youth-team spell…)
            img = commons_url(d["img"]) if d.get("img") else w.get("img") or ar_imgs.get(d.get("arwiki"))
            if not img:
                continue
            if w.get("has_infobox"):
                club_q, club_name = club_ids.get(w.get("club_link")), w.get("club")
            else:   # no English infobox: trust Wikidata only for recent moves
                now = current_club(d["clubs"])
                recent = now and (now["start"] or 0) >= 2023
                club_q, club_name = (now["q"], now["name"]) if recent else (None, None)
            if d.get("dead"):
                club_q = club_name = None
            # The squad table is the best source; fall back to the player's own page if it's missing
            here_now = q in squads[key] if len(squads[key]) >= 11 else club_q in scopes[key]
            if w.get("has_infobox") and not club_q:
                here_now = False   # their own page says retired / no club; the squad table is stale
            if here_now and club_q not in scopes[key]:
                club_name = None   # player page out of date; don't show a stale club
            bucket = current if here_now else former
            if len(bucket) < (CURRENT_PER_PACK if here_now else FORMER_PER_PACK):
                bucket.append(q)
            name = re.sub(r"\s*\(.*\)$", "", w.get("title") or d["en"])
            players[q] = {
                "title": d.get("enwiki"), "name": name, "ar": d.get("ar"),
                "also": sorted({a for a in d["also"] if len(a) < 40 and a != name}),
                "nick": sorted(d["nick"]),
                "img": img, "club": club_name, "dead": d.get("dead") or None,
            }
        packs[key] = {
            "label": label, "kind": kind, "lang": lang,
            "name": label.split(" ", 1)[1],
            "players": current + former,
            "now": current,
        }
        print(f"{label}: {len(current)} current, {len(former)} former")

    used = {q for p in packs.values() for q in p["players"]}
    players = {q: {k: v for k, v in p.items() if v not in (None, [], "")} for q, p in players.items() if q in used}
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// Generated by tools/build_packs.py from Wikidata on " + time.strftime("%Y-%m-%d") + ". Do not edit by hand.\n")
        f.write("const PACK_PLAYERS = " + json.dumps(players, ensure_ascii=False, separators=(",", ":")) + ";\n")
        f.write("const LEAGUE_PACKS = " + json.dumps(packs, ensure_ascii=False, indent=1) + ";\n")
    print("Wrote", OUT, "with", len(players), "players")


if __name__ == "__main__":
    main()
