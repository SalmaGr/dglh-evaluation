"""
Reconversion de bronze.t_prd_03 : rayon "pharmacie" -> rayon "hygiene & beaute".

Une grande surface tunisienne ne vend pas de medicaments (monopole des pharmacies).
On remplace donc les libelles des medicaments par des produits d'hygiene/beaute,
en GARDANT id_art, gencod, id_frn, les colonnes techniques et les defauts de qualite
volontaires (casse, espaces, mrg vides, dates mal formatees...).

- Les ventes qui referencent id_art restent coherentes.
- TVA 7 -> 19 pour les lignes converties, pvt recalcule (= pv_ht * 1.19).
- Avant d'ecrire : tag Iceberg "avant_reconversion_hygiene" (retour arriere possible).
- La correction est documentee dans les proprietes de la table (dglh.correction.*).

Usage :
    python reconvertir_t_prd_03.py            # DRY RUN : genere apercu_reconversion.csv, n'ecrit rien
    python reconvertir_t_prd_03.py --apply    # applique la modification sur Iceberg

Variables d'environnement (adapter a ta stack) :
    CATALOG_URI   ex. http://localhost:8382/catalog
    WAREHOUSE     ex. lakehouse
    TABLE         ex. bronze.t_prd_03
    S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY   (optionnel, si Lakekeeper ne fournit pas les credentials)
"""
import csv
import os
import re
import sys
import zlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

# --- 1. Ce qu'on convertit (medicaments / quasi-medicaments) -------------------
MEDICAMENTS = [
    "PARACETAMOL", "ASPIRINE", "IBUPROFENE", "SIROP TOUX", "COLLYRE",
    "SPRAY NASAL", "PASTILLES GORGE", "SERUM PHYSIOLOGIQUE",
    "VITAMINE C", "MAGNESIUM B6",
]
# Gardes tels quels (premiers soins, vendus en grande surface) :
# PANSEMENTS, COMPRESSES STERILES, ALCOOL 70, EAU OXYGENEE, ANTISEPTIQUE, THERMOMETRE

# Marques a consonance pharmaceutique -> marques cosmetiques
MARQUES = {
    "LABO CARTHAGE": "CARTHAGE BEAUTE",
    "LABO MEDINA": "MEDINA SOINS",
    "SAHEL PHARMA": "SAHEL HYGIENE",
    "PHARMA TANIT": "TANIT COSMETIQUE",
    "SALAMBO LABS": "SALAMBO",
    "UNIMED": "FRESHIA",
}

# Nouveaux produits : (libelle, facteur de prix applique a pa et pv_ht)
NOUVEAUX_PRODUITS = [
    ("BAIN DE BOUCHE MENTHE 250ML", 1.2),
    ("BAIN DE BOUCHE PROTECTION GENCIVES 500ML", 1.8),
    ("EAU DE TOILETTE FEMME 100ML", 3.5),
    ("EAU DE TOILETTE HOMME 100ML", 3.5),
    ("EAU DE COLOGNE CITRON 250ML", 1.5),
    ("DEODORANT SPRAY 150ML", 1.4),
    ("DEODORANT BILLE 50ML", 1.1),
    ("GEL DOUCHE 500ML", 1.3),
    ("SHAMPOING CHEVEUX NORMAUX 400ML", 1.3),
    ("APRES-SHAMPOING 200ML", 1.2),
    ("CREME SOLAIRE SPF30 200ML", 2.5),
    ("CREME HYDRATANTE VISAGE 50ML", 1.6),
    ("MOUSSE A RASER 200ML", 1.1),
    ("RASOIRS JETABLES X5", 1.0),
    ("COTON DEMAQUILLANT X80", 0.7),
    ("EAU MICELLAIRE 400ML", 1.8),
    ("BROSSE A DENTS MEDIUM", 0.8),
    ("LINGETTES BEBE X64", 1.0),
]

TVA_NOUVELLE = Decimal("19")
Q = Decimal("0.001")


def est_medicament(lib: str) -> bool:
    l = lib.upper()
    return any(m in l for m in MEDICAMENTS)


def nouveau_libelle(lib: str, id_art: str):
    """Construit le nouveau libelle en conservant les defauts de forme de l'original."""
    stripped = lib.strip()
    upper = stripped.upper()
    marque = next((m for m in MARQUES if upper.startswith(m)), None)
    nouvelle_marque = MARQUES[marque] if marque else upper.split(" ")[0]
    # choix deterministe par id_art -> meme article = meme produit dans tous les exports
    idx = zlib.crc32(str(id_art).encode()) % len(NOUVEAUX_PRODUITS)
    produit, facteur = NOUVEAUX_PRODUITS[idx]
    new = f"{nouvelle_marque} {produit}"
    if stripped.islower():          # defaut "tout en minuscules" conserve
        new = new.lower()
    lead = lib[: len(lib) - len(lib.lstrip())]
    trail = lib[len(lib.rstrip()):]
    return lead + new + trail, Decimal(str(facteur))


def to_dec(v):
    if v is None or v == "":
        return None
    return Decimal(str(v))


def convertir_ligne(row: dict) -> dict:
    """Retourne une nouvelle ligne (dict). Ne touche pas aux lignes non-medicament."""
    if not row.get("lib") or not est_medicament(row["lib"]):
        return row
    new = dict(row)
    new["lib"], f = nouveau_libelle(row["lib"], row["id_art"])
    pa, pv_ht = to_dec(row.get("pa")), to_dec(row.get("pv_ht"))
    new["pa"] = (pa * f).quantize(Q, ROUND_HALF_UP) if pa is not None else None
    new["pv_ht"] = (pv_ht * f).quantize(Q, ROUND_HALF_UP) if pv_ht is not None else None
    new["tva"] = TVA_NOUVELLE
    if new["pv_ht"] is not None:
        new["pvt"] = (new["pv_ht"] * (1 + TVA_NOUVELLE / 100)).quantize(Q, ROUND_HALF_UP)
    # mrg inchangee : (pv_ht - pa) / pa ne change pas quand on multiplie les deux par f
    return new


# --- 2. Adaptation au type reel des colonnes Iceberg -------------------------------
def caster(valeur, arrow_type):
    import pyarrow as pa
    if valeur is None:
        return None
    if isinstance(valeur, Decimal):
        if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
            return f"{valeur:.3f}" if valeur != valeur.to_integral() or "." in str(valeur) else str(int(valeur))
        if pa.types.is_decimal(arrow_type):
            return valeur.quantize(Decimal(1).scaleb(-arrow_type.scale))
        if pa.types.is_integer(arrow_type):
            return int(valeur)
        if pa.types.is_floating(arrow_type):
            return float(valeur)
    return valeur


def main():
    apply = "--apply" in sys.argv
    import pyarrow as pa
    from pyiceberg.catalog import load_catalog

    props = {
        "type": "rest",
        "uri": os.environ.get("CATALOG_URI", "http://localhost:8382/catalog"),
        "warehouse": os.environ.get("WAREHOUSE", "lakehouse"),
    }
    if os.environ.get("S3_ENDPOINT"):
        props.update({
            "s3.endpoint": os.environ["S3_ENDPOINT"],
            "s3.access-key-id": os.environ.get("S3_ACCESS_KEY", ""),
            "s3.secret-access-key": os.environ.get("S3_SECRET_KEY", ""),
            "s3.path-style-access": "true",
        })
    catalog = load_catalog("dglh", **props)
    tbl = catalog.load_table(os.environ.get("TABLE", "bronze.t_prd_03"))

    data = tbl.scan().to_arrow()
    schema = data.schema
    rows = data.to_pylist()

    nouvelles, avant_apres = [], []
    for r in rows:
        n = convertir_ligne({k: (str(v) if isinstance(v, (int, float)) and k in ("pa", "pv_ht", "pvt", "tva") else v)
                             for k, v in r.items()})
        if n is not r and n["lib"] != r["lib"]:
            avant_apres.append({"id_art": r["id_art"], "avant": r["lib"], "apres": n["lib"],
                                "pv_ht_avant": r["pv_ht"], "pv_ht_apres": n["pv_ht"],
                                "tva_avant": r["tva"], "pvt_apres": n["pvt"]})
            n = {k: caster(v, schema.field(k).type) for k, v in n.items()}
        else:
            n = r
        nouvelles.append(n)

    with open("apercu_reconversion.csv", "w", newline="", encoding="utf-8") as f:
        if avant_apres:
            w = csv.DictWriter(f, fieldnames=list(avant_apres[0]))
            w.writeheader()
            w.writerows(avant_apres)

    print(f"Lignes totales : {len(rows)} | converties : {len(avant_apres)} | inchangees : {len(rows) - len(avant_apres)}")
    print("Apercu ecrit dans apercu_reconversion.csv")

    if not apply:
        print("DRY RUN : rien n'a ete ecrit. Relance avec --apply pour appliquer.")
        return

    snap = tbl.current_snapshot()
    if snap is not None:
        tbl.manage_snapshots().create_tag(snap.snapshot_id, "avant_reconversion_hygiene").commit()
        print(f"Tag 'avant_reconversion_hygiene' -> snapshot {snap.snapshot_id}")

    tbl.overwrite(pa.Table.from_pylist(nouvelles, schema=schema))

    with tbl.transaction() as tx:
        tx.set_properties({
            "dglh.correction.date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dglh.correction.motif": "Medicaments retires : monopole des pharmacies en Tunisie, "
                                     "non vendus en grande surface. Reconversion en hygiene & beaute.",
            "dglh.correction.lignes_modifiees": str(len(avant_apres)),
            "dglh.correction.tag_avant": "avant_reconversion_hygiene",
        })
    tbl = catalog.load_table(os.environ.get("TABLE", "bronze.t_prd_03"))
    print(f"OK. Nouveau snapshot : {tbl.current_snapshot().snapshot_id}")


if __name__ == "__main__":
    main()