#!/usr/bin/env python3
"""
DGLH - Vérité terrain annotée à l'aveugle (sensibilité par colonne + secteur par table).

Étape 1  python3 verite_terrain.py preparer
         -> annotation_sensibilite.csv : table, colonne, type, exemples de valeurs RÉELLES
            (celles envoyées au LLM, lues dans les logs), colonne `niveau` VIDE à remplir.
            Le niveau proposé par le LLM n'y figure PAS (annotation à l'aveugle).
         -> annotation_secteur.csv : table, exemples de lignes, colonnes `secteur_reel` et
            `secteur_llm_correct` (O/N) à remplir, `langue_fr` (O/N).

Étape 2  annoter dans Excel / LibreOffice (niveau : P, I, C, R ou le mot entier),
         enregistrer au format CSV (UTF-8) sous le même nom.

Étape 3  python3 verite_terrain.py comparer
         -> resultats_verite.md (+ affichage) : exactitude, kappa, matrice de confusion,
            sous/sur-classement, P/R/F1 par niveau, liste des désaccords, justesse du secteur.

Options : --logs ~/implementation/dglh-logs  --dir ~/implementation/resultats_enrichissement
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

LEVELS = ["Public", "Internal", "Confidential", "Restricted"]
SHORT = {"p": "Public", "i": "Internal", "c": "Confidential", "r": "Restricted",
         "public": "Public", "publique": "Public", "internal": "Internal", "interne": "Internal",
         "confidential": "Confidential", "confidentiel": "Confidential", "confidentielle": "Confidential",
         "restricted": "Restricted", "restreint": "Restricted", "restreinte": "Restricted"}


def latest_logs(log_dir):
    by_table = {}
    for path in glob.glob(os.path.join(os.path.expanduser(log_dir), "*.json")):
        try:
            e = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        key = f"{e.get('namespace')}.{e.get('table')}"
        if key not in by_table or e.get("timestamp", "") > by_table[key].get("timestamp", ""):
            by_table[key] = e
    return by_table


def parse_prompt(prompt):
    """Colonnes (nom, type) et lignes d'échantillon telles qu'envoyées au LLM."""
    cols = []
    m = re.search(r"Columns:\n(.*?)\n\n", prompt, re.S)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"- (\S+) \((.*)\)(?: — .*)?$", line.strip())
            if mm:
                cols.append((mm.group(1), mm.group(2).replace(", required", "")))
    rows = []
    m = re.search(r"Sample data \(primary evidence\):\n(.*?)\nRespond STRICTLY", prompt, re.S)
    if m:
        for line in m.group(1).splitlines():
            if not line.startswith("Row "):
                continue
            body = line.split(": ", 1)[1] if ": " in line else ""
            row = {}
            for cell in body.split(" | "):
                if "=" in cell:
                    k, v = cell.split("=", 1)
                    row[k.strip()] = v.strip()
            rows.append(row)
    return cols, rows


def examples(rows, col, n=4, width=40):
    seen = []
    for r in rows:
        v = r.get(col, "")
        if v and v.lower() not in ("null", "none", "nan") and v not in seen:
            seen.append(v)
        if len(seen) >= n:
            break
    return " ; ".join(x if len(x) <= width else x[:width] + "…" for x in seen)


def write_csv(path, rows, cols):
    # utf-8-sig : Excel ouvre correctement les accents
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","  # Excel FR enregistre en ';'
        return list(csv.DictReader(fh, delimiter=delim))


def preparer(args):
    logs = latest_logs(args.logs)
    sens_rows, sect_rows = [], []
    for table in sorted(logs):
        e = logs[table]
        msgs = (e.get("request") or {}).get("messages") or [{}]
        cols, rows = parse_prompt(msgs[0].get("content", ""))
        for name, typ in cols:
            sens_rows.append({"table": table, "colonne": name, "type": typ,
                              "exemples": examples(rows, name), "niveau": ""})
        try:
            raw = json.loads(e.get("raw_response") or "{}")
            content = raw["choices"][0]["message"]["content"]
            a, b = content.find("{"), content.rfind("}")
            sector = json.loads(content[a:b + 1]).get("detected_sector", "")
        except Exception:
            sector = ""
        sect_rows.append({"table": table, "secteur_llm": sector, "secteur_reel": "",
                          "secteur_llm_correct": "", "langue_fr": ""})
    os.makedirs(args.dir, exist_ok=True)
    p1 = os.path.join(args.dir, "annotation_sensibilite.csv")
    p2 = os.path.join(args.dir, "annotation_secteur.csv")
    write_csv(p1, sens_rows, ["table", "colonne", "type", "exemples", "niveau"])
    write_csv(p2, sect_rows, ["table", "secteur_llm", "secteur_reel", "secteur_llm_correct", "langue_fr"])
    print(f"{len(sens_rows)} colonnes à annoter -> {p1}")
    print(f"{len(sect_rows)} tables (secteur)   -> {p2}")
    print("Remplis `niveau` avec P / I / C / R, puis lance : python3 verite_terrain.py comparer")


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[l] * cb[l] for l in LEVELS) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def pct(x, n):
    return f"{100 * x / n:.1f} %" if n else "—"


def comparer(args):
    ann = read_csv(os.path.join(args.dir, "annotation_sensibilite.csv"))
    llm = {(r["table"], r["colonne"].lower()): r["propose"]
           for r in read_csv(os.path.join(args.dir, "sensibilite.csv"))}
    pairs, disagreements, unknown = [], [], 0
    for r in ann:
        truth = SHORT.get((r.get("niveau") or "").strip().lower())
        if not truth:
            unknown += 1
            continue
        pred = llm.get((r["table"], r["colonne"].lower()))
        if not pred:
            continue
        pairs.append((pred, truth))
        if pred != truth:
            disagreements.append((r["table"], r["colonne"], r.get("exemples", ""), pred, truth))
    if not pairs:
        sys.exit("Aucune colonne annotée (colonne `niveau` vide ?).")

    md = ["# Classification de sensibilité : LLM vs vérité terrain (annotation à l'aveugle)\n",
          f"Colonnes annotées et comparées : {len(pairs)} (non annotées : {unknown}).\n",
          "| LLM \\ Vérité terrain | " + " | ".join(LEVELS) + " | Total |",
          "| --- |" + " ---: |" * (len(LEVELS) + 1)]
    cm = Counter(pairs)
    for a in LEVELS:
        md.append(f"| **{a}** | " + " | ".join(str(cm[(a, b)]) for b in LEVELS) + f" | {sum(cm[(a, b)] for b in LEVELS)} |")
    md.append("| **Total** | " + " | ".join(str(sum(cm[(a, b)] for a in LEVELS)) for b in LEVELS) + f" | {len(pairs)} |\n")
    acc = sum(a == b for a, b in pairs)
    rank = {l: i for i, l in enumerate(LEVELS)}
    under = sum(rank[a] < rank[b] for a, b in pairs)
    over = sum(rank[a] > rank[b] for a, b in pairs)
    sensitive = [(a, b) for a, b in pairs if b in ("Confidential", "Restricted")]
    missed = sum(a in ("Public", "Internal") for a, _ in sensitive)
    md += [f"- **Exactitude** : {pct(acc, len(pairs))} ({acc}/{len(pairs)})",
           f"- **Kappa de Cohen** : {kappa(pairs):.3f}",
           f"- **Sous-classement** (risque d'exposition) : {pct(under, len(pairs))} ({under})",
           f"- **Sur-classement** (excès de prudence) : {pct(over, len(pairs))} ({over})",
           f"- **Données personnelles manquées** (vérité Confidential/Restricted, LLM Public/Internal) : "
           f"{missed}/{len(sensitive)} ({pct(missed, len(sensitive))})\n",
           "| Niveau | Précision | Rappel | F1 | Support |", "| --- | ---: | ---: | ---: | ---: |"]
    for l in LEVELS:
        tp, pp, sup = cm[(l, l)], sum(cm[(l, b)] for b in LEVELS), sum(cm[(a, l)] for a in LEVELS)
        p, r = (tp / pp if pp else 0), (tp / sup if sup else 0)
        md.append(f"| {l} | {p:.2f} | {r:.2f} | {(2 * p * r / (p + r) if p + r else 0):.2f} | {sup} |")
    md.append("")
    if disagreements:
        md += ["## Désaccords\n", "| Table | Colonne | Exemples | LLM | Vérité |", "| --- | --- | --- | --- | --- |"]
        md += [f"| {t} | {c} | {ex} | {p} | {v} |" for t, c, ex, p, v in disagreements]
        md.append("")

    sect_path = os.path.join(args.dir, "annotation_secteur.csv")
    if os.path.exists(sect_path):
        sect = read_csv(sect_path)
        judged = [s for s in sect if (s.get("secteur_llm_correct") or "").strip()]
        ok = sum(s["secteur_llm_correct"].strip().upper().startswith("O") for s in judged)
        lang = [s for s in sect if (s.get("langue_fr") or "").strip()]
        fr = sum(s["langue_fr"].strip().upper().startswith("O") for s in lang)
        md += ["## Secteur détecté\n",
               f"- **Secteur correct** : {pct(ok, len(judged))} ({ok}/{len(judged)} tables jugées)",
               f"- **Respect de la langue (français)** : {pct(fr, len(lang))} ({fr}/{len(lang)})\n"]
        bad = [s for s in judged if not s["secteur_llm_correct"].strip().upper().startswith("O")]
        if bad:
            md += ["| Table | Secteur proposé par le LLM | Secteur réel |", "| --- | --- | --- |"]
            md += [f"| {s['table']} | {s['secteur_llm']} | {s.get('secteur_reel', '')} |" for s in bad]
            md.append("")

    text = "\n".join(md)
    out = os.path.join(args.dir, "resultats_verite.md")
    open(out, "w", encoding="utf-8").write(text)
    print(text)
    print(f"\n-> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("etape", choices=["preparer", "comparer"])
    ap.add_argument("--logs", default="~/implementation/dglh-logs")
    ap.add_argument("--dir", default="~/implementation/resultats_enrichissement")
    a = ap.parse_args()
    a.dir = os.path.expanduser(a.dir)
    {"preparer": preparer, "comparer": comparer}[a.etape](a)