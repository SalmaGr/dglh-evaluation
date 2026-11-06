#!/bin/bash
# Relance le BON serveur Lakekeeper (prompt v3) puis génère 3 tours d'enrichissement.
# Usage :  bash lancer_tout.sh          (ou : bash lancer_tout.sh test  pour un seul essai)
set -u
LOGDIR=/home/user/implementation/dglh-evaluation/dglh-logs
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

echo "== 1. Recherche de la copie du projet qui contient le nouveau prompt"
PROJ=""
for d in ~/implementation/project/lakekeeper ~/project/lakekeeper; do
  if [ -d "$d" ] && grep -rlq "official identity data" --include=enrichment.rs "$d" 2>/dev/null; then PROJ="$d"; break; fi
done
if [ -z "$PROJ" ]; then echo "ERREUR : aucune copie ne contient le nouveau prompt dans enrichment.rs."; exit 1; fi
echo "   -> $PROJ"
grep -rh "ENRICHMENT_PROMPT_VERSION: u32" --include=enrichment.rs "$PROJ"

echo "== 2. Arrêt de l'ancien serveur sur le port 8382"
PIDS=$(ss -ltnp 2>/dev/null | grep ':8382 ' | grep -o 'pid=[0-9]*' | cut -d= -f2)
for p in $PIDS; do kill "$p" && echo "   arrêté : pid $p"; done
sleep 3

echo "== 3. Compilation et lancement du serveur (patience : quelques minutes)"
cd "$PROJ"
source ~/implementation/project/lakekeeper_env.sh
export ENRICHMENT_LOG_DIR="$LOGDIR"
export ENRICHMENT_SAMPLE_SIZE=20
export PATH=~/gcc-wrapper:$PATH
mkdir -p "$LOGDIR"
cargo build -j 2 --bin lakekeeper || { echo "ERREUR de compilation"; exit 1; }
nohup cargo run --bin lakekeeper -- serve > ~/lakekeeper_serveur.log 2>&1 &
echo "   serveur lancé (journal : ~/lakekeeper_serveur.log)"
for i in $(seq 1 60); do
  curl -s http://localhost:8382/management/v1/info >/dev/null 2>&1 && break; sleep 2
done
curl -s http://localhost:8382/management/v1/info >/dev/null || { echo "ERREUR : le serveur ne répond pas. Voir ~/lakekeeper_serveur.log"; exit 1; }
echo "   serveur OK sur le port 8382"

echo "== 4. Génération"
cd "$SCRIPTS"
if [ "${1:-}" = "test" ]; then
  python3 generer_enrichissements.py --test
else
  python3 generer_enrichissements.py --tours 3
fi

echo "== 5. Vérification"
N=$(ls "$LOGDIR"/*.json 2>/dev/null | wc -l)
V=$(grep -l "official identity data" "$LOGDIR"/*.json 2>/dev/null | wc -l)
echo "   fichiers de log : $N   dont avec le nouveau prompt : $V"
cd /home/user/implementation/dglh-evaluation && zip -qr ~/dglh-logs-v3.zip dglh-logs && echo "   archive : ~/dglh-logs-v3.zip"
echo "Le serveur reste lancé en arrière-plan (pour l'arrêter : kill \$(ss -ltnp | grep ':8382 ' | grep -o 'pid=[0-9]*' | cut -d= -f2))"