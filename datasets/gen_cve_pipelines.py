"""Generate the Tapstate workspace that consolidates DAB cve into resolved MongoDB collections.

Every pipeline: source table JOIN its crosswalk (xw_<table>: scrambled key -> canonical CVE id,
from the entity-resolution step) [JOIN a lookup table] -> a `js` step that normalizes values
-> a view keyed by the source row. Collections (all carry the canonical `cve`):

  cve_record       cves rows (duplicates kept: some CVEs have conflicting rows)
  cvss             score parsed to a number; severity from the standard CVSS v3 bands
  cpe_match        vendor resolved from its alias, product, version decoded, vulnerable as bool
  kev_entry        vendor canonical, products split into an array, ransomware use as bool
  cve_description  descriptions parsed; english text (or null); severity decoded from its phrase

One source per table (a pipeline reads every table its sources list). Lookup maps too large to
join on a computed value (CPE vendor aliases) are embedded in the js step.

Usage: python gen_cve_pipelines.py <workspace-dir>
"""
import json
import sys
from pathlib import Path

import psycopg2

TABLES = ["cves", "cvss_metadata", "cpe_matches", "cpe_version_details", "vendor_aliases", "kev_entries",
          "kev_vendor_aliases", "cve_documents", "cve_xwalk", "xw_cves", "xw_cpe_matches", "xw_kev_entries",
          "xw_cve_documents"]

# Severity phrases planted in English descriptions (the literal words never appear).
SEVERITY_PHRASES = {
    "Risk-level: 4-of-4": "CRITICAL", "Threat tier: T4 (top)": "CRITICAL", "Impact band: red-zone": "CRITICAL",
    "Risk-level: 3-of-4": "HIGH", "Threat tier: T3": "HIGH", "Impact band: orange-zone": "HIGH",
    "Risk-level: 2-of-4": "MEDIUM", "Threat tier: T2": "MEDIUM", "Impact band: yellow-zone": "MEDIUM",
    "Risk-level: 1-of-4": "LOW", "Threat tier: T1": "LOW", "Impact band: green-zone": "LOW",
}
TRUTHY = ["yes", "y", "true", "1", "V", "affected", "vulnerable", "T"]


def js(body):
    return "function process(record, ctx) {\n  var r = record.after;\n  if (r != null) {\n" + body + \
           "\n  }\n  return record;\n}\n"


def step_js(step_id, upstream, script):
    lines = script.rstrip("\n").split("\n")
    return (f"  - id: {step_id}\n    from: [ {upstream} ]\n    type: js\n    script: |\n"
            + "".join(f"      {l}\n" for l in lines))


def pipeline(pid, sources, join_from, sql, script, view, key="row_id"):
    y = (f"version: tapstate/v1\nkind: pipeline\nid: {pid}\nsource: [ {', '.join(sources)} ]\n"
         f"settings: {{ read_mode: snapshot_and_cdc }}\ntransforms:\n"
         f"  - id: resolve\n    type: join\n    from: {{ {join_from} }}\n    engine: builtin\n    sql: |\n")
    y += "".join(f"      {l}\n" for l in sql.strip().split("\n"))
    y += step_js("normalize", "resolve", script)
    y += f"view: {{ id: {view}, from: normalize, primary_key: {key} }}\n"
    return y


def main():
    ws = Path(sys.argv[1])
    (ws / "source").mkdir(parents=True, exist_ok=True)
    (ws / "pipeline").mkdir(parents=True, exist_ok=True)
    for t in TABLES:
        (ws / "source" / f"src_{t}.tap.yml").write_text(
            f"version: tapstate/v1\nkind: source\nid: src_{t}\nconnector: postgres\n"
            "config: { host: postgres, port: 5432, database: cvesrc, schema: public, user: postgres, "
            f"password: secret }}\nmode: cdc\ntables: [ {t} ]\n")

    pg = psycopg2.connect("host=127.0.0.1 port=55432 user=postgres password=secret dbname=cvesrc").cursor()
    pg.execute("select alias, canonical_vendor from vendor_aliases")
    aliases = dict(pg.fetchall())

    P = ws / "pipeline"
    P.joinpath("cve_record_state.tap.yml").write_text(pipeline(
        "cve_record_state", ["src_cves", "src_xw_cves"], "c: cves, x: xw_cves",
        """SELECT c.row_id AS row_id, x.cve AS cve, c.published AS published, c.last_modified AS last_modified,
       c.vuln_status AS vuln_status, c.cvss3_attack_vector AS cvss3_attack_vector
FROM c LEFT JOIN x ON c.cve_id = x.surface_key""",
        js("    // rows pass through; the join already resolved the CVE id"), "cve_record"))

    P.joinpath("cpe_match_state.tap.yml").write_text(pipeline(
        "cpe_match_state", ["src_cpe_matches", "src_xw_cpe_matches", "src_cpe_version_details"],
        "m: cpe_matches, x: xw_cpe_matches, v: cpe_version_details",
        """SELECT m.row_id AS row_id, x.cve AS cve, m.criteria AS criteria, m.vulnerable_flag AS vulnerable_flag,
       v.version_text AS version_text, v.version_start_inc AS version_start_including,
       v.version_start_exc AS version_start_excluding, v.version_end_inc AS version_end_including,
       v.version_end_exc AS version_end_excluding
FROM m LEFT JOIN x ON m.cve_id = x.surface_key LEFT JOIN v ON m.row_id = v.row_id""",
        js(f"""    var ALIAS = {json.dumps(aliases, separators=(',', ':'))};
    var TRUTHY = {json.dumps(TRUTHY)};
    var c = String(r.criteria || ''), alias = null, product = null, m;
    if (c.indexOf('cpe:2.3:') === 0) {{ var p = c.split(':'); alias = p[3]; product = p[4]; }}
    else if ((m = c.match(/^([^\\/\\s]+)\\/(.+)@([^@]*)$/))) {{ alias = m[1]; product = m[2]; }}
    else if ((m = c.match(/^(\\S+) (.+) (\\S+)$/))) {{ alias = m[1]; product = m[2].toLowerCase().replace(/ /g, '_'); }}
    r.vendor = alias == null ? null : (ALIAS[alias] || alias.toLowerCase());
    r.product = product == null ? null : product.toLowerCase();
    r.vulnerable = TRUTHY.indexOf(String(r.vulnerable_flag)) >= 0;
    var t = r.version_text, ver = t;
    if (t != null && t !== '*') {{
      if ((m = String(t).match(/^build-(\\d+)$/)) && m[1].length % 3 === 0) {{
        var parts = []; for (var i = 0; i < m[1].length; i += 3) parts.push(String(parseInt(m[1].substr(i, 3), 10)));
        ver = parts.join('.');
      }} else if ((m = String(t).match(/^v(.+)$/)) && String(t).indexOf('_') >= 0) {{ ver = m[1].replace(/_/g, '.'); }}
      else {{ ver = String(t).replace(/,/g, '.'); }}
    }}
    r.version = ver;
    delete r.version_text; delete r.vulnerable_flag;"""), "cpe_match"))

    P.joinpath("kev_entry_state.tap.yml").write_text(pipeline(
        "kev_entry_state", ["src_kev_entries", "src_xw_kev_entries", "src_kev_vendor_aliases"],
        "k: kev_entries, x: xw_kev_entries, a: kev_vendor_aliases",
        """SELECT k.row_id AS row_id, x.cve AS cve, a.canonical_vendor AS vendor, k.vendor_project AS vendor_project,
       k.products_csv AS products_csv, k.vulnerability_name AS vulnerability_name, k.date_added AS date_added,
       k.short_description AS short_description, k.required_action AS required_action, k.due_date AS due_date,
       k.known_ransomware_use AS known_ransomware_use, k.notes AS notes
FROM k LEFT JOIN x ON k.cve_ref = x.surface_key LEFT JOIN a ON k.vendor_project = a.vendor_project""",
        js("""    r.products = r.products_csv == null ? [] : String(r.products_csv).split(',').map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
    r.ransomware_use = String(r.known_ransomware_use || '').toLowerCase() === 'known';
    delete r.products_csv;"""), "kev_entry"))

    P.joinpath("cve_description_state.tap.yml").write_text(pipeline(
        "cve_description_state", ["src_cve_documents", "src_xw_cve_documents"], "d: cve_documents, x: xw_cve_documents",
        """SELECT d.row_id AS row_id, x.cve AS cve, d.descriptions_json AS descriptions_json,
       d.references_json AS references_json
FROM d LEFT JOIN x ON d.cve_key = x.surface_key""",
        js(f"""    var PHRASES = {json.dumps(SEVERITY_PHRASES)};
    var ds = JSON.parse(r.descriptions_json || '[]'), refs = JSON.parse(r.references_json || '[]');
    var en = null, langs = [], sev = null;
    for (var i = 0; i < ds.length; i++) {{
      langs.push(ds[i].lang);
      if (ds[i].lang === 'en' && en == null) en = ds[i].value;
    }}
    if (en != null) {{
      for (var ph in PHRASES) {{ if (en.indexOf(ph) >= 0) {{ sev = PHRASES[ph]; en = en.replace(ph, '').trim(); break; }} }}
    }}
    r.english_description = en;
    r.has_english = en != null;
    r.languages = langs;
    r.severity = sev;
    r.descriptions = ds;
    r.references = refs;
    delete r.descriptions_json; delete r.references_json;"""), "cve_description"))
    print(f"wrote {len(TABLES)} sources and 4 pipelines to {ws} (cvss_state is written by hand)")


if __name__ == "__main__":
    main()
