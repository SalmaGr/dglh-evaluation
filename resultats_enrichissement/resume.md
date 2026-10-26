# Résultats DGLH : enrichissement et sensibilité

24 tables validées comparées à la proposition brute du LLM ; 296 colonnes classées.

## 1. Décisions humaines par type de champ

| Champ | N | Accepté | Édité | Rejeté |
| --- | ---: | ---: | ---: | ---: |
| Secteur détecté | 24 | 100.0 % | 0.0 % | 0.0 % |
| Domaine de données | 24 | 100.0 % | 0.0 % | 0.0 % |
| Description métier | 24 | 100.0 % | 0.0 % | 0.0 % |
| Consommateurs types | 24 | 100.0 % | 0.0 % | 0.0 % |
| Confidentialité (table) | 24 | 100.0 % | 0.0 % | 0.0 % |
| Tags | 24 | 50.0 % | 50.0 % | 0.0 % |
| Mots-clés | 24 | 100.0 % | 0.0 % | 0.0 % |
| Description de colonne | 296 | 100.0 % | 0.0 % | 0.0 % |
| Sensibilité de colonne | 296 | 100.0 % | 0.0 % | 0.0 % |
| **Total** | **760** | **98.4 %** | **1.6 %** | **0.0 %** |

## 2. Classification de sensibilité (LLM vs validation humaine)

Colonnes comparées : 296 (sensibilité rejetée et non persistée : 0).

Matrice de confusion (lignes = proposé par le LLM, colonnes = validé par l'humain) :

| LLM \ Humain | Public | Internal | Confidential | Restricted | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| **Public** | 19 | 0 | 0 | 0 | 19 |
| **Internal** | 0 | 251 | 0 | 0 | 251 |
| **Confidential** | 0 | 0 | 13 | 0 | 13 |
| **Restricted** | 0 | 0 | 0 | 13 | 13 |
| **Total** | 19 | 251 | 13 | 13 | 296 |

- **Accord (exactitude)** : 100.0 % (296/296)
- **Kappa de Cohen** : 1.000
- **Sous-classement** (LLM moins sévère que l'humain, risque d'exposition) : 0.0 % (0)
- **Sur-classement** (LLM plus sévère, excès de prudence) : 0.0 % (0)

| Niveau | Précision | Rappel | F1 | Support (humain) |
| --- | ---: | ---: | ---: | ---: |
| Public | 1.00 | 1.00 | 1.00 | 19 |
| Internal | 1.00 | 1.00 | 1.00 | 251 |
| Confidential | 1.00 | 1.00 | 1.00 | 13 |
| Restricted | 1.00 | 1.00 | 1.00 | 13 |

## 3. Secteur détecté par table

| Table | Secteur proposé | Secteur validé |
| --- | --- | --- |
| bronze.art_02 | industrie des boissons non alcoolisées / production de boissons gazeuses | = (accepté) |
| bronze.art_divers | retail - grocery and household goods | = (accepté) |
| bronze.articles_07 | home appliance distribution | = (accepté) |
| bronze.avis_emails | distribution de supermarché - service client | = (accepté) |
| bronze.avis_reseaux_sociaux | retail - supermarché | = (accepté) |
| bronze.catalogue_05 | distribution de fournitures scolaires | = (accepté) |
| bronze.clients_crm | banque de détail - gestion de la relation client | = (accepté) |
| bronze.clients_fidelite | retail - grocery loyalty | = (accepté) |
| bronze.clients_web | e‑commerce retail - online consumer marketplace | = (accepté) |
| bronze.ecommerce_clickstream | commerce électronique - vente au détail | = (accepté) |
| bronze.fournisseurs_fiches | industrie agroalimentaire - fournisseurs | = (accepté) |
| bronze.hist_prix | distribution de biens de consommation | = (accepté) |
| bronze.items_06 | children's apparel retail | = (accepté) |
| bronze.pos_logs | distribution de produits alimentaires - supermarché | = (accepté) |
| bronze.prod_a | distribution alimentaire - grande distribution | = (accepté) |
| bronze.prod_x1 | distribution de produits de grande consommation - hypermarché | = (accepté) |
| bronze.promos | distribution alimentaire - supermarché | = (accepté) |
| bronze.ref_produit_04 | fabrication de produits d'entretien ménager | = (accepté) |
| bronze.rh_employes | distribution de détail - supermarchés | = (accepté) |
| bronze.stock_mouvements | distribution de produits de grande consommation - supermarchés | = (accepté) |
| bronze.t_prd_03 | industrie pharmaceutique / distribution de médicaments et produits de santé | = (accepté) |
| bronze.ventes_lignes | distribution alimentaire - supermarché | = (accepté) |
| bronze.ventes_paiements | distribution de détail - supermarchés | = (accepté) |
| bronze.ventes_tickets | distribution de supermarché | = (accepté) |

## 4. Coût des appels LLM

- Appels journalisés : 30 ; modèle(s) : openai/gpt-oss-120b (30)
- Latence : moyenne 5.4 s, médiane 5.2 s, max 11.0 s
- Tokens par appel : moyenne 6939 (dont prompt 4878), max 8242
