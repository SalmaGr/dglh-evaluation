#!/usr/bin/env python3
"""Valide les 25 tables (corrections de la vérité terrain), indexe, lance les 15 requêtes.
Usage : python3 finaliser.py   (depuis ~/implementation/dglh-evaluation, serveur lancé sur 8382)"""
import json, glob, os, sys, time, csv, urllib.request
LK = "http://localhost:8382/management/v1/warehouse/18a49556-cfa8-11f1-a958-dfb9aa4f25df"
LOGS = "dglh-logs"
NOMS = "resultats_recherche/noms.json"
COL = {  # corrections colonne (vérité terrain)
 ("art_02","cond_vte"):"Public",("articles_07","cond_vte"):"Public",("avis_emails","_mois"):"Internal",
 ("avis_reseaux_sociaux","ligne_json"):"Public",("catalogue_05","cb"):"Public",("catalogue_05","tx_mg"):"Confidential",
 ("fournisseurs_fiches","delai_j"):"Internal",("fournisseurs_fiches","rs"):"Internal",("fournisseurs_fiches","vil"):"Internal",
 ("hist_prix","per"):"Internal",("hist_prix","tx_mg"):"Confidential",("hist_prix","_mois"):"Internal",
 ("items_06","cond_vte"):"Public",("prod_a","cond_vte"):"Public",("prod_a","ut_v"):"Public",
 ("promos","dt_deb"):"Public",("promos","dt_fin"):"Public",("ref_produit_04","cond_vte"):"Public",("ref_produit_04","ut_v"):"Public",
 ("ventes_lignes","mt_ht"):"Internal",("ventes_lignes","mt_ttc"):"Internal",
 ("ventes_paiements","auth"):"Restricted",("ventes_paiements","carte_msk"):"Restricted",
 ("ventes_tickets","mode_paiement"):"Internal",("ventes_tickets","mt_ht"):"Internal",("ventes_tickets","mt_ttc"):"Internal",
 ("ventes_tickets","mt_tva"):"Internal",("ventes_tickets","nb_art"):"Internal"}
SECT = {"clients_crm":"grande distribution - commerce de détail (clients du magasin)",
        "prod_x1":"grande distribution - supermarché multi-rayons",
        "art_02":"grande distribution - rayon boissons"}
CONF = {"ventes_paiements":"Restricted"}
RK = {"Public":0,"Internal":1,"Confidential":2,"Restricted":3}
def post(url, body, timeout=180):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read() or b"{}")
def ids():
    m = json.load(open(NOMS))
    out = {}
    for k, v in (m.items() if isinstance(m, dict) else []):
        if isinstance(v, str) and len(v) == 36: out[k.split(".")[-1]] = v
        elif isinstance(k, str) and len(k) == 36: out[str(v).split(".")[-1]] = k
    return out
best = {}
for f in sorted(glob.glob(LOGS + "/*.json")):
    e = json.load(open(f))
    if "whoever the account holder is" not in json.dumps(e.get("request", "")): continue
    c = json.loads(e["raw_response"])["choices"][0]["message"]["content"]
    o = json.loads(c[c.find("{"):c.rfind("}") + 1])
    if e["table"] not in best or e["timestamp"] > best[e["table"]][0]: best[e["table"]] = (e["timestamp"], o, e.get("table_id"))
TID = ids()
for t in best:
    if t not in TID:
        try:
            with urllib.request.urlopen(f"http://localhost:8382/catalog/v1/18a49556-cfa8-11f1-a958-dfb9aa4f25df/namespaces/bronze/tables/{t}", timeout=30) as r:
                TID[t] = json.loads(r.read())["metadata"]["table-uuid"]
        except Exception as ex: print("  ! id", t, ex)
etape = sys.argv[1] if len(sys.argv) > 1 else "tout"
trace = []
if etape in ("tout", "valider"):
    for t, (_, o, tid) in sorted(best.items()):
        tid = tid or TID.get(t)
        if not tid: print("  ! id introuvable", t); continue
        acc = lambda v: {"decision": "accepted", "value": v}
        ed = lambda v: {"decision": "edited", "value": v}
        fields, nmod = [], 0
        for fl in o.get("fields", []):
            k = (t, fl["name"]); s = fl.get("sensitivity")
            if k in COL and COL[k] != s: sens = ed(COL[k]); nmod += 1
            else: sens = acc(s)
            fields.append({"name": fl["name"], "description": acc(fl.get("description", "")), "sensitivity": sens})
        levels = [f["sensitivity"]["value"] for f in fields if f["sensitivity"]["value"] in RK]
        conf = CONF.get(t) or o.get("confidentiality_level")
        if levels and RK.get(conf, -1) < max(RK[l] for l in levels): conf = max(levels, key=RK.get)
        body = {"prompt_version": 5,
                "evidence": acc(o.get("evidence", [])), "keywords": acc(o.get("keywords", [])), "suggested_tags": acc(o.get("suggested_tags", [])),
                "detected_sector": ed(SECT[t]) if t in SECT else acc(o.get("detected_sector")),
                "data_domain": acc(o.get("data_domain")), "business_description": acc(o.get("business_description")),
                "typical_consumers": acc(o.get("typical_consumers") if isinstance(o.get("typical_consumers"), str) else ", ".join(o.get("typical_consumers") or [])),
                "confidentiality_level": ed(conf) if conf != o.get("confidentiality_level") else acc(conf),
                "fields": fields}
        try:
            post(f"{LK}/table/{tid}/enrichment/validate", body); print(f"  ✓ validée  {t:24s} colonnes modifiées={nmod}")
            trace.append({"table": t, "colonnes_modifiees": nmod, "secteur_modifie": t in SECT})
        except Exception as ex: print(f"  ✗ {t}: {ex} {getattr(ex,'read',lambda:b'')()[:300]}")
    json.dump(trace, open("trace_validation_v4.json", "w"), ensure_ascii=False, indent=1)
if etape in ("tout", "indexer"):
    for t, (_, o, tid) in sorted(best.items()):
        tid = tid or TID.get(t)
        for essai in range(5):
            try: post(f"{LK}/table/{tid}/semantic-index", {"columns": []}); print(f"  ✓ indexée  {t}"); break
            except Exception as ex:
                print(f"  … {t} essai {essai+1}: {ex}"); time.sleep(30)
        time.sleep(5)
if etape in ("tout", "rechercher"):
    os.makedirs("resultats_recherche_v4", exist_ok=True); res = {}
    for row in csv.DictReader(open("requetes_pertinence.csv", encoding="utf-8")):
        q = row["requete"]
        for essai in range(5):
            try: res[q] = post(f"{LK}/semantic-search", {"query": q, "limit": 10}); break
            except Exception as ex: print("  …", q, ex); time.sleep(20)
        print(f"  ✓ {q}"); time.sleep(2)
    json.dump(res, open("resultats_recherche_v4/resultats.json", "w"), ensure_ascii=False, indent=1)
print("FINI")