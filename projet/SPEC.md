# SPEC : Plateforme de Politique Open Source (The Universal Stack)

## 1. Objectif & Périmètre
Application participative permettant aux citoyens et élus d'explorer des textes de loi officiels, simuler leur impact socio-économique et géographique, proposer des variantes (flèche descendante), voter au Jugement Majoritaire et fusionner les nœuds compatibles en lois de consensus (flèche ascendante).

## 2. Stack & Contraintes Techniques
- **Monorepo TypeScript (Node 22)** :
  - `backend/` : API Hono (`hono`, `@hono/node-server`), Drizzle ORM (`drizzle-orm`, `postgres`), tests `node --import tsx --test`.
  - `harvester/` : Worker BullMQ + Redis (`bullmq`, `zod`, `drizzle-orm`), schéma partagé `backend/src/db/schema.ts` via `shared-db`.
  - `frontend/` : Expo Router + React Native Web (`expo`, `react-native`).
- **Contraintes** : ESM NodeNext (extensions `.js` dans imports relatifs `backend/` et `harvester/`), <= 150 lignes par fichier, aucun appel API externe bloquant dans `backend/`, tests sans réseau externe (`node:test` + `node:assert/strict`).

## 3. Architecture Cible
1. **`backend/src/domain/`** : types ontologie (`Groupe`, `Personne`, `Loi`, `Vote`, `Temps`, `Lieu`, `Infos`, `Commentaire`), machine à états, moteur d'impact, compatibilité & fusion.
2. **`backend/src/auth/`** : tokens Magic-Link / PIN (`node:crypto`) et rate-limiting IP.
3. **`backend/src/routes/`** : routes Hono (validation entrées, lecture DB locale, `202 Accepted` si job harvester requis) montées dans `backend/src/index.ts`.
4. **`harvester/src/adapters/`** : adaptateurs Open Data (`geo.api.gouv.fr`, textes/scrutins) implémentant `BaseAdapter`, validation Zod et upsert idempotent.
5. **`frontend/src/`** : écrans Expo Router et composants glassmorphisme bleu clair (explorateur de lois, simulateur d'impact, vote Jugement Majoritaire).

## 4. Exigences Fonctionnelles Testables
- **REQ-01** : Modéliser les 4 entités principales (`Groupe`, `Personne`, `Loi`, `Vote`) et 4 greffons (`Temps`, `Lieu`, `Infos`, `Commentaire`) avec transitions d'état valides (`draft -> debating -> voting -> merged -> enacted -> archived`).
- **REQ-02** : Calculer un score d'impact personnel déterministe à partir d'un profil (`Infos` + `Lieu`) et des paramètres d'une `Loi`.
- **REQ-03** : Implémenter le calcul du Jugement Majoritaire et la détection de compatibilité + fusion (`merge`) entre deux nœuds frères `Loi`.
- **REQ-04** : Exposer les routes API Hono (`/health`, `/laws`, `/laws/:id/fork`, `/laws/:id/vote`, `/laws/:id/merge`) avec validation stricte et réponse HTTP `202` lors d'un dispatch asynchrone.
- **REQ-05** : Implémenter l'authentification sans mot de passe (PIN / Magic Link signé HMAC via `AUTH_SECRET`) et un limiteur de débit en mémoire.
- **REQ-06** : Implémenter un adaptateur `harvester` Open Data (`geo.api.gouv.fr`) validé par Zod avec cache brut et upsert idempotent.
- **REQ-07** : Exposer l'interface universelle dans `frontend/src/` (exploration des lois, simulation interactive d'impact et vote).

## 5. Critères d'Acceptation Globaux
- `tsc --noEmit` passe avec code `0` dans `backend/`, `harvester/` et `frontend/`.
- Tous les tests `backend/tests/*.test.ts` et `harvester/tests/*.test.ts` passent (`exit code 0`).
- Le lancement conteneurisé `./start.sh` dans `projet/` reste sain (`/health` HTTP 200).
