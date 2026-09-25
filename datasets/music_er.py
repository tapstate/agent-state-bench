"""Entity resolution for DAB music_brainz_20k: cluster the tracks of five sources into songs.

A realistic resolver, written from inspecting the data only (no generator, no ground truth):
  1. per-record cleanup of each source's title convention — a leading track number ("022-"), an
     "Artist - " prefix, a " - Album" suffix, an "(Album)" suffix;
  2. normalization: accents folded, lower-cased, non-alphanumerics dropped (absorbs "GetMe Bodied");
  3. candidate pairs by blocking on title prefix / suffix and artist prefix;
  4. a pair matches when core titles are near-identical (fuzzy ratio >= 90) and the artists or
     albums agree where both are known (>= 80);
  5. union-find, never joining two clusters that already hold a record of the same source (each
     song has at most one record per source).

Writes to Postgres `musicsrc`:
  song_xwalk(track_id PK, song_id, song_title, song_artist, song_album)
  songs(song_id PK, title, artist, album, year, n_records)
song_id is the smallest track_id in the cluster; the canonical title/artist/album are the most
common cleaned values in it.

Usage: python music_er.py
"""
import re
import unicodedata
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import execute_values
from rapidfuzz import fuzz

DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=musicsrc"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def clean_album(a):
    return re.sub(r"\s*\(\d{4}\)\s*$", "", (a or "").strip())


def core_title(title, artist, album):
    t = (title or "").strip()
    t = re.sub(r"^\d{2,4}-", "", t)                                       # "022-Title"
    na, nb = norm(artist), norm(album)
    if " - " in t:
        head, tail = t.split(" - ", 1)
        if na and fuzz.ratio(norm(head), na) >= 80:                       # "Artist - Title"
            t = tail
        elif nb and fuzz.ratio(norm(tail), nb) >= 80:                     # "Title - Album"
            t = head
        elif not na:                                                      # artist unknown: prefix form
            t = tail
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", t)
    if m and nb and fuzz.ratio(norm(m[2]), nb) >= 80:                     # "Title (Album)"
        t = m[1]
    return t.strip()


def readings(title):
    """Every plausible core-title reading of a raw title: the source conventions overlap, and a
    record may lack the artist or album that would say which one applies."""
    t = re.sub(r"^\d{2,4}-", "", (title or "").strip())
    out = {t}
    if " - " in t:
        head, tail = t.split(" - ", 1)
        out |= {head, tail}
    for x in list(out):
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", x)
        if m:
            out.add(m[1])
    return {norm(x) for x in out if len(norm(x)) >= 2}


def main():
    con = psycopg2.connect(DSN)
    con.autocommit = True
    cur = con.cursor()
    cur.execute("select track_id, source_id, title, artist, album, year from tracks")
    recs = []
    for tid, src, title, artist, album, year in cur.fetchall():
        alb = clean_album(album)
        # an "Artist - Title" record may carry no artist; take it from the title prefix
        art = (artist or "").strip()
        if not art and " - " in (title or ""):
            art = title.split(" - ", 1)[0].strip()
        core = core_title(title, art, alb)
        recs.append(dict(id=tid, src=src, core=core, t=norm(core), a=norm(art), b=norm(alb),
                         artist=art, album=alb, year=year, reads=readings(title) | {norm(core)}))
    by_id = {r["id"]: r for r in recs}

    blocks = defaultdict(list)
    for r in recs:
        for t in r["reads"]:
            if len(t) >= 3:
                blocks["p" + t[:4]].append(r["id"])
                blocks["s" + t[-4:]].append(r["id"])
        if len(r["a"]) >= 3:
            blocks["a" + r["a"][:5] + r["t"][:2]].append(r["id"])

    parent = {r["id"]: r["id"] for r in recs}
    sources = {r["id"]: {r["src"]} for r in recs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = []
    seen = set()
    for ids in blocks.values():
        if len(ids) > 400:  # an over-general block: its pairs arrive through a narrower one
            continue
        ids = list(dict.fromkeys(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                x, y = by_id[ids[i]], by_id[ids[j]]
                if x["src"] == y["src"] or (x["id"], y["id"]) in seen:
                    continue
                seen.add((x["id"], y["id"]))
                ts = max(fuzz.ratio(u, v) for u in x["reads"] for v in y["reads"])
                if ts < 90:
                    continue
                side = []
                if x["a"] and y["a"]:
                    side.append(fuzz.ratio(x["a"], y["a"]))
                if x["b"] and y["b"]:
                    side.append(fuzz.ratio(x["b"], y["b"]))
                if side and max(side) < 80:
                    continue
                pairs.append((ts + (max(side) if side else 80), x["id"], y["id"]))
    for _, x, y in sorted(pairs, reverse=True):  # strongest evidence first
        rx, ry = find(x), find(y)
        if rx != ry and not (sources[rx] & sources[ry]):
            parent[ry] = rx
            sources[rx] |= sources[ry]

    clusters = defaultdict(list)
    for r in recs:
        clusters[find(r["id"])].append(r)

    def pick(values):
        vals = [v for v in values if v]
        return Counter(vals).most_common(1)[0][0] if vals else None

    xw, songs = [], []
    for members in clusters.values():
        sid = min(m["id"] for m in members)
        title = pick(m["core"] for m in members if " " in m["core"]) or pick(m["core"] for m in members)
        artist = pick(m["artist"] for m in members)
        album = pick(m["album"] for m in members)
        year = pick(y for y in (m["year"] for m in members) if y and re.fullmatch(r"(19|20)\d\d", str(y)))
        songs.append((sid, title, artist, album, year, len(members)))
        xw += [(m["id"], sid, title, artist, album) for m in members]

    for name, cols, pk, rows in (
        ("song_xwalk", "track_id BIGINT, song_id BIGINT, song_title TEXT, song_artist TEXT, song_album TEXT",
         "track_id", xw),
        ("songs", "song_id BIGINT, title TEXT, artist TEXT, album TEXT, year TEXT, n_records BIGINT", "song_id", songs)):
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        cur.execute(f"CREATE TABLE {name} ({cols}, PRIMARY KEY ({pk}))")
        execute_values(cur, f"INSERT INTO {name} VALUES %s", rows, page_size=5000)
        cur.execute(f"ALTER TABLE {name} REPLICA IDENTITY FULL")
    sizes = Counter(len(m) for m in clusters.values())
    print(f"{len(recs)} tracks -> {len(clusters)} songs; cluster sizes {dict(sorted(sizes.items()))}")


if __name__ == "__main__":
    main()
