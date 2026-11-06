#!/usr/bin/env python3
"""
Relance l'enrichissement (prompt v4) des tables bronze, N tours, avec EXACTEMENT les mêmes
échantillons que l'ancienne exécution (relus dans les anciens logs). Ne valide jamais rien.

  python3 generer_enrichissements.py --tours 3
"""
import argparse, glob, json, os, re, sys, time, urllib.request, urllib.error

ap = argparse.ArgumentParser()
ap.add_argument("--lk", default="http://localhost:8382")
ap.add_argument("--wh", default="18a49556-cfa8-11f1-a958-dfb9aa4f25df")
ap.add_argument("--anciens-logs", default=os.path.expanduser("~/implementation/dglh-evaluation/dglh-logs_ancien"))
ap.add_argument("--noms", default=os.path.expanduser("~/implementation/dglh-evaluation/resultats_recherche/noms.json"))
ap.add_argument("--tours", type=int, default=3)
ap.add_argument("--pause", type=float, default=65)  # Groq gratuit : 8000 tokens/min ≈ 1 table/min
ap.add_argument("--test", action="store_true", help="une seule table, un seul tour")
ap.add_argument("--tables", nargs="*", help="limiter à ces tables")
ap.add_argument("--nouveaux-logs", default=os.path.expanduser("~/implementation/dglh-evaluation/dglh-logs"))
ap.add_argument("--refaire", action="store_true", help="refaire aussi les tables déjà réussies avec le prompt v3")
a = ap.parse_args()


def parse_prompt(p):
    cols = []
    m = re.search(r"Columns:\n(.*?)\n\n", p, re.S)
    for line in (m.group(1).splitlines() if m else []):
        mm = re.match(r"- (\S+) \(", line.strip())
        if mm: cols.append(mm.group(1))
    m = re.search(r"Sample data \(primary evidence\):\n(.*?)\nRespond STRICTLY", p, re.S)
    rows = []
    for line in (m.group(1).splitlines() if m else []):
        if not line.startswith("Row "): continue
        body = line.split(": ", 1)[1]
        vals, pos = [], 0
        for i, c in enumerate(cols):
            pre = f"{c}="
            if not body.startswith(pre, pos): vals = None; break
            start = pos + len(pre)
            if i + 1 < len(cols):
                nxt = body.find(f" | {cols[i + 1]}=", start)
                if nxt < 0: vals = None; break
                vals.append(body[start:nxt]); pos = nxt + 3
            else:
                vals.append(body[start:])
        if vals: rows.append(vals)
    return cols, rows


def latest_logs(d):
    best = {}
    for f in glob.glob(os.path.join(d, "*.json")):
        try: e = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        k = (e.get("namespace"), e.get("table"))
        if k not in best or e.get("timestamp", "") > best[k].get("timestamp", ""): best[k] = e
    return best


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"{}")


logs = latest_logs(a.anciens_logs)
if not logs: sys.exit(f"Aucun ancien log trouvé dans {a.anciens_logs}")
ids = {v: k for k, v in json.load(open(a.noms, encoding="utf-8")).items()}   # nom -> table_id

jobs = []
for (ns, t), e in sorted(logs.items()):
    if ns != "bronze": continue
    cols, rows = parse_prompt(e["request"]["messages"][0]["content"])
    if t not in ids:   # identifiant Lakekeeper = table-uuid des métadonnées Iceberg
        try:
            with urllib.request.urlopen(f"{a.lk}/catalog/v1/{a.wh}/namespaces/bronze/tables/{t}", timeout=30) as r:
                ids[t] = json.loads(r.read())["metadata"]["table-uuid"]
        except Exception as ex:
            print(f"  ! {t} : identifiant introuvable ({ex}), ignorée"); continue
    if not rows: print(f"  ! {t} : échantillon illisible dans l'ancien log, ignorée"); continue
    jobs.append((t, ids[t], cols, rows))
print(f"{len(jobs)} tables prêtes (échantillons de {min(len(j[3]) for j in jobs)}-{max(len(j[3]) for j in jobs)} lignes)\n")

if a.tables: jobs = [j for j in jobs if j[0] in a.tables]
if not a.refaire:   # saute les tables qui ont déjà une réponse v3 valide
    faites = set()
    for f in glob.glob(os.path.join(a.nouveaux_logs, "*.json")):
        try: e = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        if "whoever the account holder is" in json.dumps(e.get("request", "")) and '"fields"' in str(e.get("raw_response", "")):
            faites.add(e.get("table"))
    if faites: print(f"Déjà réussies (sautées) : {', '.join(sorted(faites))}\n")
    jobs = [j for j in jobs if j[0] not in faites]
print(f"Tables à traiter : {len(jobs)}\n")
tours = 1 if a.test else a.tours
if a.test: jobs = jobs[:1]
ok = err = 0
for tour in range(1, tours + 1):
    print(f"===== Tour {tour}/{tours} =====")
    for t, tid, cols, rows in jobs:
        url = f"{a.lk}/management/v1/warehouse/{a.wh}/table/{tid}/enrichment"
        n_rows = len(rows)
        for essai in range(8):
            body = {"sample_columns": cols, "sample_rows": rows[:n_rows], "regenerate": True}
            try:
                r = post(url, body)
                print(f"  ✓ {t:28s} {len(r.get('fields', [])):3d} colonnes  échantillon={n_rows}  prompt_version={r.get('prompt_version')}")
                ok += 1; break
            except urllib.error.HTTPError as ex:
                msg = ex.read().decode(errors="ignore")
                if "413" in msg or "too large" in msg.lower():
                    if n_rows > 5:
                        n_rows -= 5; print(f"    requête trop grosse : échantillon réduit à {n_rows} lignes"); time.sleep(65); continue
                if "429" in msg or "rate limit" in msg.lower():
                    print(f"    limite Groq atteinte, pause 65 s (essai {essai + 1})…"); time.sleep(65); continue
                print(f"  ✗ {t}: HTTP {ex.code} {msg[:200]}"); err += 1; break
            except Exception as ex:
                print(f"  ✗ {t}: {ex}"); err += 1; break
        else:
            print(f"  ✗ {t}: abandon après plusieurs essais"); err += 1
        print(f"    (pause {a.pause:.0f} s)", flush=True)
        time.sleep(a.pause)
print(f"\nTerminé : {ok} réussis, {err} erreurs.")