"""Generate the Tapstate workspaces that consolidate DAB yelp and googlelocal.

yelp (database yelpsrc) -> views
  business  one document per business: address / city / state / state_name parsed from the
            description, categories matched against Yelp's public category list, attributes
            decoded (booleans, numbers, nested objects instead of Python-literal strings),
            hours as zero-padded HH:MM ranges, reviews and tips embedded with ISO dates
  user      one document per user: yelping_since as an ISO date, elite as a list of years
  checkin   one document per business: check-in times as an ISO list
googlelocal (database googlesrc) -> view
  place     one document per business: city / state / zip parsed from the description, hours per
            weekday as 24-hour open / close times, misc decoded into an object, reviews embedded
            with ISO dates

The yelp category list is Yelp's published taxonomy (developer documentation page), fetched at
build time and embedded in the js step: a dictionary match, longest name first, case-sensitive.

Usage: python gen_yelp_google_pipelines.py <tapstate-dir> [--categories yelp_category_titles.json]
Writes <tapstate-dir>/yelpws and <tapstate-dir>/googlews.
"""
import argparse
import html
import json
import re
import urllib.request
from pathlib import Path

CATEGORY_PAGE = "https://docs.developer.yelp.com/docs/resources-categories"

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "AB": "Alberta", "BC": "British Columbia",
}

# One date parser for every text format seen in these sources: "29 Dec 2020, 13:25",
# "April 17, 2016 at 12:00 AM", "2021-04-12 17:07:52", "15 Jan 2009, 16:40".
DATE_JS = r"""
var MONTHS = {jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
function pad(n) { return (n < 10 ? '0' : '') + n; }
function isoTime(s) {
  if (s == null) return null;
  s = String(s).trim(); var m, y, mo, d, h = 0, mi = 0;
  if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/))) {
    y = +m[1]; mo = +m[2]; d = +m[3]; if (m[4]) { h = +m[4]; mi = +m[5]; }
  } else if ((m = s.match(/^(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})(?:, (\d{1,2}):(\d{2}))?/))) {
    d = +m[1]; mo = MONTHS[m[2].toLowerCase()]; y = +m[3]; if (m[4]) { h = +m[4]; mi = +m[5]; }
  } else if ((m = s.match(/^([A-Za-z]{3})[a-z]* (\d{1,2}), (\d{4})(?: at (\d{1,2}):(\d{2}) ?([AP]M))?/))) {
    mo = MONTHS[m[1].toLowerCase()]; d = +m[2]; y = +m[3];
    if (m[4]) { h = +m[4] % 12 + (m[6] === 'PM' ? 12 : 0); mi = +m[5]; }
  } else return null;
  if (!mo) return null;
  return y + '-' + pad(mo) + '-' + pad(d) + 'T' + pad(h) + ':' + pad(mi);
}
"""


def fetch_categories():
    req = urllib.request.Request(CATEGORY_PAGE, headers={"User-Agent": "Mozilla/5.0"})
    page = html.unescape(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore"))
    # Each entry reads "Title (alias, countries)"; a title may itself contain parentheses,
    # as in "American (New) (newamerican, [US])", so cut at the alias group, not the first "(".
    return sorted(set(m.strip() for m in re.findall(r"<li>\s*([^<]+?)\s*\([a-z0-9_]+\s*,", page)))


def js_step(step_id, table, body, prelude=""):
    script = prelude + "function process(record, ctx) {\n  var r = record.after;\n  if (r != null) {\n" + body + \
        "\n  }\n  return record;\n}\n"
    return (f"  - id: {step_id}\n    from: [ {table} ]\n    type: js\n    script: |\n"
            + "".join(f"      {l}\n" for l in script.splitlines()))


def source(sid, database, table):
    return (f"version: tapstate/v1\nkind: source\nid: {sid}\nconnector: postgres\n"
            f"config: {{ host: postgres, port: 5432, database: {database}, schema: public, user: postgres, "
            f"password: secret }}\nmode: cdc\ntables: [ {table} ]\n")


def pipeline(pid, sources, steps, tail):
    return (f"version: tapstate/v1\nkind: pipeline\nid: {pid}\nsource: [ {', '.join(sources)} ]\n"
            f"settings: {{ read_mode: snapshot_and_cdc }}\ntransforms:\n{steps}{tail}")


def yelp(ws, categories):
    (ws / "source").mkdir(parents=True, exist_ok=True)
    (ws / "pipeline").mkdir(parents=True, exist_ok=True)
    for t in ("business", "review", "tip", "users", "checkin"):
        (ws / "source" / f"src_y{t}.tap.yml").write_text(source(f"src_y{t}", "yelpsrc", t))
    cats = sorted(categories, key=len, reverse=True)
    business_js = js_step("t_business", "business", r"""
    var d = String(r.description || ''), m;
    r.address = null; r.city = null; r.state = null;
    if ((m = d.match(/\bat (.+?) in ([^,]+?), ([A-Z]{2})\b/))) { r.address = m[1]; r.city = m[2]; r.state = m[3]; }
    else if ((m = d.match(/(?:\bin|^This|^Located in)\s+([^,]+?),\s+([A-Z]{2})\b/))) { r.city = m[1]; r.state = m[2]; }
    r.state_name = r.state && STATES[r.state] ? STATES[r.state] : null;
    var found = [];
    for (var i = 0; i < CATS.length; i++) {
      var c = CATS[i], at = d.indexOf(c);
      while (at >= 0) {
        var before = at === 0 ? ' ' : d.charAt(at - 1), after = d.charAt(at + c.length) || ' ';
        if (!/[A-Za-z0-9&\/]/.test(before) && !/[A-Za-z0-9&\/]/.test(after)) {
          var inside = false;
          for (var j = 0; j < found.length; j++) if (found[j].indexOf(c) >= 0) inside = true;
          if (!inside) found.push(c);
          break;
        }
        at = d.indexOf(c, at + 1);
      }
    }
    r.categories = found;
    var a = r.attributes ? JSON.parse(r.attributes) : {}, out = {};
    for (var k in a) out[k] = decode(a[k]);
    r.attributes = out;
    var h = r.hours ? JSON.parse(r.hours) : null, hours = null;
    if (h) { hours = {}; for (var day in h) hours[day] = String(h[day]).replace(/(\d+):(\d+)/g, function (x, hh, mm) { return pad(+hh) + ':' + pad(+mm); }); }
    r.hours = hours;
    r.is_open = String(r.is_open) === '1';""",
        prelude=DATE_JS + "var CATS = " + json.dumps(cats) + ";\nvar STATES = " + json.dumps(US_STATES) + ";\n" + r"""
function decode(v) {
  if (v == null) return null;
  var s = String(v).trim();
  if (s === 'True') return true; if (s === 'False') return false; if (s === 'None' || s === '') return null;
  if (/^-?\d+(\.\d+)?$/.test(s)) return +s;
  var m = s.match(/^u?'(.*)'$/); if (m) return m[1];
  if (s.charAt(0) === '{') {
    var j = s.replace(/\bu'/g, "'").replace(/'/g, '"').replace(/\bTrue\b/g, 'true').replace(/\bFalse\b/g, 'false').replace(/\bNone\b/g, 'null');
    try { return JSON.parse(j); } catch (e) { return s; }
  }
  return s;
}
""")
    review_js = js_step("t_review", "review", r"""
    r.business_id = String(r.business_ref || '').replace(/^businessref_/, 'businessid_');
    r.time = isoTime(r.date); r.date = r.time ? r.time.substr(0, 10) : null;""", prelude=DATE_JS)
    tip_js = js_step("t_tip", "tip", r"""
    r.business_id = String(r.business_ref || '').replace(/^businessref_/, 'businessid_');
    r.time = isoTime(r.date); r.date = r.time ? r.time.substr(0, 10) : null;""", prelude=DATE_JS)
    nest = """  - id: assemble
    type: nest
    from: { b: t_business, v: t_review, t: t_tip }
    root:
      from: b
      key: [ business_id ]
      embed:
        - { from: v, on: { business_id: business_id }, as: array, path: reviews, arrayKey: [ review_id ] }
        - { from: t, on: { business_id: business_id }, as: array, path: tips, arrayKey: [ row_id ] }
"""
    (ws / "pipeline" / "yelp_business_state.tap.yml").write_text(pipeline(
        "yelp_business_state", ["src_ybusiness", "src_yreview", "src_ytip"], business_js + review_js + tip_js + nest,
        "view: { id: business, from: assemble, primary_key: business_id }\n"))
    user_js = js_step("t_user", "users", r"""
    var t = isoTime(r.yelping_since); r.yelping_since = t ? t.substr(0, 10) : null;
    r.elite = r.elite ? String(r.elite).split(',').map(function (x) { return +x.trim(); }).filter(function (x) { return x > 0; }) : [];""",
        prelude=DATE_JS)
    (ws / "pipeline" / "yelp_user_state.tap.yml").write_text(pipeline(
        "yelp_user_state", ["src_yusers"], user_js, "view: { id: user, from: t_user, primary_key: user_id }\n"))
    checkin_js = js_step("t_checkin", "checkin", r"""
    r.times = r.date ? String(r.date).split(',').map(function (x) { return isoTime(x); }).filter(function (x) { return x; }) : [];
    delete r.date;""", prelude=DATE_JS)
    (ws / "pipeline" / "yelp_checkin_state.tap.yml").write_text(pipeline(
        "yelp_checkin_state", ["src_ycheckin"], checkin_js,
        "view: { id: checkin, from: t_checkin, primary_key: business_id }\n"))


def google(ws):
    (ws / "source").mkdir(parents=True, exist_ok=True)
    (ws / "pipeline").mkdir(parents=True, exist_ok=True)
    (ws / "source" / "src_gbusiness.tap.yml").write_text(source("src_gbusiness", "googlesrc", "business_description"))
    (ws / "source" / "src_greview.tap.yml").write_text(source("src_greview", "googlesrc", "review"))
    place_js = js_step("t_place", "business_description", r"""
    var d = String(r.description || '').trim(), m;
    r.status_text = r.state == null ? null : String(r.state);
    r.description = d; r.city = null; r.state = null; r.zip = null;
    if ((m = d.match(/([A-Z][A-Za-z .'-]+?),\s+([A-Z]{2})\s+(\d{5})\b/))) {
      r.city = m[1].replace(/^(?:in|at|of|near|to|from|the)\s+/i, '').replace(/^.*\b(?:in|at)\s+/, '');
      r.state = m[2]; r.zip = m[3];
    }
    var h = null;
    if (r.hours) {
      try {
        var rows = JSON.parse(r.hours); h = {};
        for (var i = 0; i < rows.length; i++) h[rows[i][0]] = parseSpan(rows[i][1]);
      } catch (e) { h = null; }
    }
    r.hours = h;
    if (r.misc) { try { r.misc = JSON.parse(r.misc); } catch (e) { } }""",
        prelude=DATE_JS + r"""
function to24(t) {
  var m = String(t).trim().match(/^(\d{1,2})(?::(\d{2}))?\s*([AP]M)?$/i);
  if (!m) return null;
  var h = +m[1] % 12, mi = m[2] ? +m[2] : 0;
  if (m[3] && m[3].toUpperCase() === 'PM') h += 12;
  if (!m[3] && +m[1] === 12) h = 12;
  return pad(h) + ':' + pad(mi);
}
function parseSpan(s) {
  s = String(s).trim();
  if (/^closed$/i.test(s)) return { closed: true, text: s };
  if (/24 hours/i.test(s)) return { open: '00:00', close: '24:00', text: s };
  var p = s.split(/\s*[–-]\s*/);
  if (p.length !== 2) return { text: s };
  var end = p[1].match(/[AP]M$/i), start = p[0];
  if (!/[AP]M$/i.test(start) && end) start = start + end[0];
  return { open: to24(start), close: to24(p[1]), text: s };
}
""")
    review_js = js_step("t_greview", "review", r"""
    r.review_time = isoTime(r.time); r.review_date = r.review_time ? r.review_time.substr(0, 10) : null;
    r.author = r.name; delete r.name; delete r.time;""", prelude=DATE_JS)
    nest = """  - id: assemble
    type: nest
    from: { p: t_place, v: t_greview }
    root:
      from: p
      key: [ gmap_id ]
      embed:
        - { from: v, on: { gmap_id: gmap_id }, as: array, path: reviews, arrayKey: [ row_id ] }
"""
    (ws / "pipeline" / "google_place_state.tap.yml").write_text(pipeline(
        "google_place_state", ["src_gbusiness", "src_greview"], place_js + review_js + nest,
        "view: { id: place, from: assemble, primary_key: gmap_id }\n"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tapstate_dir", type=Path)
    ap.add_argument("--categories", type=Path, help="a saved copy of the category list (JSON array)")
    a = ap.parse_args()
    cats = json.loads(a.categories.read_text()) if a.categories else fetch_categories()
    assert len(cats) > 1000, f"category list looks wrong: {len(cats)} names"
    yelp(a.tapstate_dir / "yelpws", cats)
    google(a.tapstate_dir / "googlews")
    print(f"wrote yelpws ({len(cats)} categories) and googlews under {a.tapstate_dir}")


if __name__ == "__main__":
    main()
