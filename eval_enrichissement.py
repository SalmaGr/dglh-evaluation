#!/usr/bin/env python3
"""
DGLH - Évaluation de l'enrichissement sémantique et de la classification de sensibilité.

Compare, pour chaque table validée, la PROPOSITION brute du LLM (logs ENRICHMENT_LOG_DIR)
à la version VALIDÉE par l'humain (TBLPROPERTIES, lues via l'API Lakekeeper).

Sorties (dans --out) :
  champs.csv        une ligne par (table, champ) : proposé, validé, décision
  sensibilite.csv   une ligne par (table, colonne) : sensibilité proposée vs validée
  couts.csv         une ligne par appel LLM : durée, tokens
  resume.md         tableaux prêts pour le mémoire (aussi affiché à l'écran)

Usage :
  python3 eval_enrichissement.py --lakekeeper http://localhost:8382 \
      --warehouse 18a49556-cfa8-11f1-a958-dfb9aa4f25df \
      --logs ~/implementation/dglh-logs --out ~/implementation/resultats_enrichissement
Uniquement la bibliothèque standard Python (aucune installation).
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

LEVELS = ["Public", "Internal", "Confidential", "Restricted"]
TEXT_FIELDS = ["detected_sector", "data_domain", "business_description", "typical_consumers",
               "confidentiality_level"]
LIST_FIELDS = ["suggested_tags", "keywords"]
LABELS = {
    "detected_sector": "Secteur détecté", "data_domain": "Domaine de données",
    "business_description": "Description métier", "typical_consumers": "Consommateurs types",
    "confidentiality_level": "Confidentialité (table)", "suggested_tags": "Tags",
    "keywords": "Mots-clés", "column_description": "Description de colonne",
    "column_sensitivity": "Sensibilité de colonne",
}


def norm_level(v):
    if not v:
        return ""
    s = str(v).strip().lower()
    for lvl, alts in {"Public": ["public", "publique"], "Internal": ["internal", "interne"],
                      "Confidential": ["confidential", "confidentiel", "confidentielle"],
                      "Restricted": ["restricted", "restreint", "restreinte", "sensible", "sensitive"]}.items():
        if s in alts:
            return lvl
    return "Confidential"  # même repli que le serveur


def norm_tag(t):
    out, last = [], False
    for c in t.strip().lower():
        if c.isalnum():
            out.append(c); last = False
        elif not last and out:
            out.append("_"); last = True
    return "".join(out).strip("_")


def norm_text(v):
    return " ".join(str(v or "").split())


def norm_list(vals, tags=False):
    vals = [norm_tag(x) if tags else norm_text(x) for x in (vals or [])]
    return sorted({x.lower() for x in vals if x})


def http_json(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def list_tables(args):
    """(table_id, namespace, name) depuis Postgres (même source que l'index sémantique)."""
    sql = ("SELECT t.tabular_id, array_to_string(n.namespace_name, '.'), t.name FROM tabular t "
           "JOIN namespace n ON n.namespace_id = t.namespace_id "
           f"WHERE t.typ = 'table' AND t.deleted_at IS NULL AND t.warehouse_id = '{args.warehouse}';")
    cmd = ["docker", "exec", args.pg_container, "psql", "-U", args.pg_user, "-d", args.pg_db,
           "-At", "-F", "\t", "-c", sql]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        sys.exit(f"Erreur psql : {e.stderr}")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(tuple(parts))
    return rows


def extract_json(text):
    text = (text or "").strip()
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1] if a != -1 and b > a else text)


def load_logs(log_dir):
    """{(namespace, table): [entrée triée par date]} ; entrée = dict avec proposition parsée."""
    logs = defaultdict(list)
    for path in glob.glob(os.path.join(os.path.expanduser(log_dir), "*.json")):
        try:
            entry = json.load(open(path, encoding="utf-8"))
            raw = json.loads(entry.get("raw_response") or "{}")
            content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                proposal = extract_json(content)
            except Exception:
                proposal = None
            usage = raw.get("usage") or {}
            logs[(entry.get("namespace"), entry.get("table"))].append({
                "path": os.path.basename(path),
                "ts": datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")),
                "duration_ms": entry.get("duration_ms"),
                "attempts": entry.get("attempts"),
                "model": (entry.get("request") or {}).get("model"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "proposal": proposal,
            })
        except Exception as e:
            print(f"  [ignoré] {path}: {e}", file=sys.stderr)
    for k in logs:
        logs[k].sort(key=lambda x: x["ts"])
    return logs


def decision(proposed, validated):
    if validated in ("", [], None):
        return "rejected"
    return "accepted" if proposed == validated else "edited"


def cohen_kappa(pairs):
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[l] * cb[l] for l in LEVELS) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def pct(x, n):
    return f"{100 * x / n:.1f} %" if n else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lakekeeper", default="http://localhost:8382")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--logs", default="~/implementation/dglh-logs")
    ap.add_argument("--out", default="~/implementation/resultats_enrichissement")
    ap.add_argument("--pg-container", default="dglh-postgres")
    ap.add_argument("--pg-user", default="lakekeeper")
    ap.add_argument("--pg-db", default="lakekeeper_dglh_fork")
    args = ap.parse_args()
    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)

    logs = load_logs(args.logs)
    tables = list_tables(args)
    print(f"{len(tables)} tables dans le catalogue, {sum(len(v) for v in logs.values())} fichiers de log.")

    champs, sens, sectors, missing_log = [], [], [], []
    for table_id, ns, name in sorted(tables, key=lambda r: (r[1], r[2])):
        url = f"{args.lakekeeper}/management/v1/warehouse/{args.warehouse}/table/{table_id}/enrichment"
        val = http_json(url, {"check_only": True})
        if not val or val.get("status") != "validated":
            continue
        vinfo = val.get("validation") or {}
        vt = datetime.fromisoformat(vinfo["validated_at"].replace("Z", "+00:00")) if vinfo.get("validated_at") else None
        # proposition = dernier log parsable AVANT la validation
        cands = [l for l in logs.get((ns, name), []) if l["proposal"] and (vt is None or l["ts"] <= vt)]
        if not cands:
            missing_log.append(f"{ns}.{name}")
            continue
        prop = cands[-1]["proposal"]

        for f in TEXT_FIELDS:
            p, v = norm_text(prop.get(f)), norm_text(val.get(f))
            if f == "confidentiality_level":
                p, v = norm_level(p), norm_level(v) if v else ""
            champs.append({"table": f"{ns}.{name}", "champ": f, "propose": p, "valide": v,
                           "decision": decision(p, v)})
        for f in LIST_FIELDS:
            p, v = norm_list(prop.get(f), tags=f == "suggested_tags"), norm_list(val.get(f), tags=f == "suggested_tags")
            champs.append({"table": f"{ns}.{name}", "champ": f, "propose": " | ".join(p),
                           "valide": " | ".join(v), "decision": decision(p, v)})
        sectors.append((f"{ns}.{name}", norm_text(prop.get("detected_sector")), norm_text(val.get("detected_sector"))))

        vcols = {c["name"].lower(): c for c in val.get("fields") or []}
        for c in prop.get("fields") or []:
            cname = c.get("name", "")
            vc = vcols.get(cname.lower(), {})
            pd, vd = norm_text(c.get("description")), norm_text(vc.get("description"))
            ps, vs = norm_level(c.get("sensitivity")), norm_level(vc.get("sensitivity")) if vc.get("sensitivity") else ""
            champs.append({"table": f"{ns}.{name}", "champ": "column_description", "propose": pd,
                           "valide": vd, "decision": decision(pd, vd)})
            champs.append({"table": f"{ns}.{name}", "champ": "column_sensitivity", "propose": ps,
                           "valide": vs, "decision": decision(ps, vs)})
            sens.append({"table": f"{ns}.{name}", "colonne": cname, "propose": ps, "valide": vs})

    # ----------------------------------------------------------------- CSV
    def write_csv(fname, rows, cols):
        with open(os.path.join(out, fname), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    write_csv("champs.csv", champs, ["table", "champ", "propose", "valide", "decision"])
    write_csv("sensibilite.csv", sens, ["table", "colonne", "propose", "valide"])
    couts = [{"table": f"{k[0]}.{k[1]}", "fichier": l["path"], "modele": l["model"],
              "duree_ms": l["duration_ms"], "tentatives": l["attempts"], "tokens_prompt": l["prompt_tokens"],
              "tokens_reponse": l["completion_tokens"], "tokens_total": l["total_tokens"]}
             for k, v in logs.items() for l in v]
    write_csv("couts.csv", couts, ["table", "fichier", "modele", "duree_ms", "tentatives",
                                   "tokens_prompt", "tokens_reponse", "tokens_total"])

    # ----------------------------------------------------------------- RÉSUMÉ
    md = []
    n_tables = len({c["table"] for c in champs})
    md.append(f"# Résultats DGLH : enrichissement et sensibilité\n")
    md.append(f"{n_tables} tables validées comparées à la proposition brute du LLM ; "
              f"{len(sens)} colonnes classées.\n")
    if missing_log:
        md.append(f"Tables validées sans log de proposition (exclues) : {', '.join(missing_log)}\n")

    md.append("## 1. Décisions humaines par type de champ\n")
    md.append("| Champ | N | Accepté | Édité | Rejeté |\n| --- | ---: | ---: | ---: | ---: |")
    tot = Counter()
    for f in TEXT_FIELDS + LIST_FIELDS + ["column_description", "column_sensitivity"]:
        rows = [c for c in champs if c["champ"] == f]
        cnt = Counter(c["decision"] for c in rows)
        tot.update(cnt)
        md.append(f"| {LABELS[f]} | {len(rows)} | {pct(cnt['accepted'], len(rows))} | "
                  f"{pct(cnt['edited'], len(rows))} | {pct(cnt['rejected'], len(rows))} |")
    n = sum(tot.values())
    md.append(f"| **Total** | **{n}** | **{pct(tot['accepted'], n)}** | **{pct(tot['edited'], n)}** | "
              f"**{pct(tot['rejected'], n)}** |\n")

    md.append("## 2. Classification de sensibilité (LLM vs validation humaine)\n")
    pairs = [(s["propose"], s["valide"]) for s in sens if s["valide"]]
    rejected_s = sum(1 for s in sens if not s["valide"])
    md.append(f"Colonnes comparées : {len(pairs)} (sensibilité rejetée et non persistée : {rejected_s}).\n")
    md.append("Matrice de confusion (lignes = proposé par le LLM, colonnes = validé par l'humain) :\n")
    md.append("| LLM \\ Humain | " + " | ".join(LEVELS) + " | Total |\n| --- |" + " ---: |" * (len(LEVELS) + 1))
    cm = Counter(pairs)
    for a in LEVELS:
        md.append(f"| **{a}** | " + " | ".join(str(cm[(a, b)]) for b in LEVELS) +
                  f" | {sum(cm[(a, b)] for b in LEVELS)} |")
    md.append("| **Total** | " + " | ".join(str(sum(cm[(a, b)] for a in LEVELS)) for b in LEVELS) +
              f" | {len(pairs)} |\n")
    acc = sum(1 for a, b in pairs if a == b)
    md.append(f"- **Accord (exactitude)** : {pct(acc, len(pairs))} ({acc}/{len(pairs)})")
    md.append(f"- **Kappa de Cohen** : {cohen_kappa(pairs):.3f}")
    rank = {l: i for i, l in enumerate(LEVELS)}
    under = sum(1 for a, b in pairs if rank[a] < rank[b])
    over = sum(1 for a, b in pairs if rank[a] > rank[b])
    md.append(f"- **Sous-classement** (LLM moins sévère que l'humain, risque d'exposition) : {pct(under, len(pairs))} ({under})")
    md.append(f"- **Sur-classement** (LLM plus sévère, excès de prudence) : {pct(over, len(pairs))} ({over})\n")
    md.append("| Niveau | Précision | Rappel | F1 | Support (humain) |\n| --- | ---: | ---: | ---: | ---: |")
    for l in LEVELS:
        tp = cm[(l, l)]
        pp = sum(cm[(l, b)] for b in LEVELS)
        sup = sum(cm[(a, l)] for a in LEVELS)
        p = tp / pp if pp else 0
        r = tp / sup if sup else 0
        f1 = 2 * p * r / (p + r) if p + r else 0
        md.append(f"| {l} | {p:.2f} | {r:.2f} | {f1:.2f} | {sup} |")
    md.append("")

    md.append("## 3. Secteur détecté par table\n")
    md.append("| Table | Secteur proposé | Secteur validé |\n| --- | --- | --- |")
    for t, p, v in sectors:
        md.append(f"| {t} | {p} | {v if v != p else '= (accepté)'} |")
    md.append("")

    md.append("## 4. Coût des appels LLM\n")
    d = [c["duree_ms"] for c in couts if isinstance(c["duree_ms"], (int, float))]
    tk = [c["tokens_total"] for c in couts if isinstance(c["tokens_total"], (int, float))]
    tp_ = [c["tokens_prompt"] for c in couts if isinstance(c["tokens_prompt"], (int, float))]
    models = Counter(c["modele"] for c in couts)
    md.append(f"- Appels journalisés : {len(couts)} ; modèle(s) : {', '.join(f'{m} ({k})' for m, k in models.items())}")
    if d:
        ds = sorted(d)
        md.append(f"- Latence : moyenne {sum(d) / len(d) / 1000:.1f} s, médiane {ds[len(ds) // 2] / 1000:.1f} s, "
                  f"max {max(d) / 1000:.1f} s")
    if tk:
        md.append(f"- Tokens par appel : moyenne {sum(tk) / len(tk):.0f} (dont prompt {sum(tp_) / max(len(tp_), 1):.0f}), "
                  f"max {max(tk)}")
    md.append("")

    text = "\n".join(md)
    open(os.path.join(out, "resume.md"), "w", encoding="utf-8").write(text)
    print(text)
    print(f"\nFichiers écrits dans {out} : champs.csv, sensibilite.csv, couts.csv, resume.md")


if __name__ == "__main__":
    main()