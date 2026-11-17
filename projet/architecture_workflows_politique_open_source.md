# Architecture Générale & Workflows : Plateforme de Politique Open Source

**Stack cible :** The Universal Stack (Expo / React Native Web, Hono TypeScript, Drizzle ORM, PostgreSQL 16 + PostGIS, BullMQ + Redis 7).

---

## 1. Concepts Fondamentaux : La Double Flèche Législative

Le système repose sur une mécanique inspirée de **Git** et de la **maïeutique (flèche descendante / flèche ascendante)** pour transformer le chaos des opinions politiques en textes de loi précis et consensuels.

1. **Le Nœud Racine (L'État du Droit) :**
   Tout part d'un texte officiel existant (Code, Loi, Article) ou d'une problématique initiale importée depuis les APIs publiques.
2. **La Flèche Descendante (Divergence & Raffinement) :**
   Lorsqu'un nœud génère un désaccord ou manque de précision, il se divise en **deux ou plusieurs nœuds enfants dépendants** (amendements, variantes paramétrées, contre-propositions). Chaque niveau descendant précise davantage la loi (seuils chiffrés, zones géographiques PostGIS, catégories de citoyens concernées) pour isoler le point exact de blocage.
3. **La Validation Mutuelle (Compatibilité par le Vote) :**
   Les utilisateurs votent sur les nœuds frères d'un même niveau en fonction de l'impact calculé sur leur propre profil. Lorsque deux nœuds d'un même niveau atteignent un seuil de compatibilité et d'approbation mutuelle, ils sont déclarés **compatibles**.
4. **La Flèche Ascendante (Fusion / Merge de Consensus) :**
   Les nœuds frères validés fusionnent en un nœud unifié de niveau supérieur. Le système remonte ainsi l'arbre étape par étape jusqu'à la racine, produisant une **Loi de Consensus** validée par l'ensemble des profils socio-économiques et prête à être soumise aux élus.

---

## 2. Ontologie des Objets (Graphe Récursif Polymorphique)

Tous les objets héritent d'une identité unique (`UUID`), d'une machine à états (`draft`, `debating`, `voting`, `merged`, `enacted`, `archived`) et d'une capacité d'auto-référencement (`parent_id`).

### A. Les 4 Objets Principaux (Nœuds du Graphe)
* **`Groupe` :** Entité collective (Parti politique, groupe parlementaire, syndicat, association, collectif citoyen).
  * *Hiérarchie :* Parti $\rightarrow$ Courant / Commission $\rightarrow$ Section locale.
* **`Personne` :** Acteur individuel (Député, Sénateur, Ministre, Citoyen inscrit).
  * *Relation :* Rattachée temporellement à un ou plusieurs `Groupes` et enrichie par son profil EAV.
* **`Loi` :** Unité normative (Code, Projet de loi, Article, Alinéa, Fork citoyen, Simulation).
  * *Hiérarchie :* Loi officielle $\rightarrow$ Article $\rightarrow$ Alinéa $\rightarrow$ Variante Citoyenne A / Variante Citoyenne B.
* **`Vote` :** Scrutin ou évaluation (Scrutin public officiel, Jugement Majoritaire citoyen, Vote par délégation).
  * *Hiérarchie :* Vote global sur un texte $\rightarrow$ Vote granulaire sur un alinéa $\rightarrow$ Vote de validation de fusion entre deux nœuds.

### B. Les 4 Objets Secondaires (Greffons Polymorphiques)
Rattachables à n'importe quel objet principal **ou** à un autre objet secondaire via `(target_type, target_id)`.

* **`Temps` :** Ancrage chronologique (Mandat, session parlementaire, date d'entrée en vigueur, période de transition).
  * *Auto-référencement :* Législature $\rightarrow$ Session $\rightarrow$ Séance de vote.
* **`Lieu` :** Ancrage spatial PostGIS `Geometry` (Pays, région, département, circonscription, commune, zone d'impact).
  * *Auto-référencement :* France $\rightarrow$ Région $\rightarrow$ Circonscription $\rightarrow$ Commune.
* **`Infos` (EAV Typé) :** Attributs dynamiques `label + info + unité + type` (ex: *Budget : 120M€*, *Revenu fiscal de référence*, *Code ROME*, *Taux de TVA*).
  * *Auto-référencement :* Une `Info` (ex: *Coût estimé*) peut porter une sous-`Info` (ex: *Source INSEE* ou *Hypothèse de calcul*).
* **`Commentaire` :** Argumentaire structuré lié à une friction de profil (`Pour`, `Contre`, `Précision`, `Problème détecté`).
  * *Auto-référencement :* Arbre de discussion infini permettant d'affiner un argument avant de le transformer en nœud `Loi` enfant.

---

## 3. Ligne Chronologique 1 : Workflow d'Ingestion des APIs (Harvester)

Ce pipeline asynchrone (**Node.js + BullMQ + Redis 7**) tourne en continu en arrière-plan pour maintenir le socle officiel (`is_official = true`) à jour sans doublons.

1. **T+0 — Planification & Déduplication (Redis / Filtre de Bloom) :**
   Le scheduler BullMQ déclenche les jobs par fournisseur. Vérification via `ETag / If-None-Match` et filtre de Bloom pour ignorer les ressources inchangées.
2. **T+1 — Extraction Géographique (`geo.api.gouv.fr` & `INSEE`) :**
   * Ingestion des limites administratives (Régions, Départements, Circonscriptions, Communes).
   * Création des objets secondaires **`Lieu`** avec indexation spatiale **PostGIS (GIST)**.
3. **T+2 — Extraction des Acteurs (`Répertoire National des Élus`, `Assemblée Nationale`, `Sénat`, `HATVP`) :**
   * Ingestion des députés, sénateurs et membres du gouvernement $\rightarrow$ création des objets **`Personne`**.
   * Ingestion des partis et groupes parlementaires $\rightarrow$ création des objets **`Groupe`**.
   * Greffe des mandats (**`Temps`**), circonscriptions (**`Lieu`**) et déclarations d'intérêts (**`Infos`**).
4. **T+3 — Extraction Normative (`API PISTE Légifrance`, `OpenData Assemblée / Sénat`) :**
   * Récupération des textes en vigueur, projets de loi et amendements.
   * Découpage syntaxique automatique via **Zod** : création de l'arbre récursif **`Loi`** (*Texte $\rightarrow$ Article $\rightarrow$ Alinéa*).
   * Extraction automatique des paramètres chiffrés et métadonnées vers des objets **`Infos`** (budgets, dates d'application **`Temps`**, zones concernées **`Lieu`**).
5. **T+4 — Extraction des Décisions (`Scrutins Publics Assemblée & Sénat`, `NosDéputés.fr`) :**
   * Ingestion des résultats de votes officiels $\rightarrow$ création des objets **`Vote`** reliés aux **`Personnes`**, **`Groupes`** et **`Lois`**.
6. **T+5 — Merge Non Destructif en Base (`PostgreSQL 16`) :**
   * Upsert idempotent des nœuds officiels. Les branches citoyennes (`is_official = false`) greffées sur des articles existants sont conservées et notifiées si leur nœud parent officiel a évolué.

---

## 4. Ligne Chronologique 2 : Workflow Utilisateur & Convergence (Citoyen $\rightarrow$ Élu)

Ce parcours décrit comment un citoyen utilise l'application universelle (Web, iOS, Android) pour simuler, préciser et fusionner des lois.

### Phase 1 : Initialisation du Profil Réel
1. **Étape 1 — Connexion sans friction :** Authentification par Magic Link ou code PIN. Création de l'objet **`Personne`** (pseudonyme public, identité chiffrée).
2. **Étape 2 — Paramétrage Socio-Économique & Spatial :**
   * L'utilisateur renseigne son **`Lieu`** de résidence/travail (géocodage PostGIS).
   * Il complète ses **`Infos`** personnelles (statut pro, tranche de revenus, composition du foyer, mode de transport, logement). *Ces données restent privées et servent uniquement au moteur de calcul local/anonymisé.*

### Phase 2 : Exploration & Simulation d'Impact
3. **Étape 3 — Flux Personnalisé :** Le backend Hono interroge PostGIS et les attributs EAV pour afficher les **`Lois`** (officielles et officieuses) qui impactent directement le profil de l'utilisateur.
4. **Étape 4 — Simulation en Direct ("Bac à Sable") :**
   * L'utilisateur ouvre une **`Loi`** et visualise son **Score d'Impact Personnel** (ex: *gain/perte financière estimée, impact sur ses droits ou son territoire*).
   * Il manipule les curseurs des objets **`Infos`** rattachés à la loi (ex: modifier un plafond ou une zone géographique) pour observer en temps réel comment l'impact évolue pour lui et pour les autres profils statistiques.

### Phase 3 : La Flèche Descendante (Précision & Divergence)
5. **Étape 5 — Expression d'une Friction (`Commentaire`) :**
   * Si la loi pénalise son profil, l'utilisateur poste un **`Commentaire`** structuré greffé sur l'alinéa précis : *"En tant que [Profil X], cette règle me bloque à cause de [Condition Y]"*.
6. **Étape 6 — Création de Nœuds Enfants (Fork / Précision) :**
   * Pour résoudre la friction, l'utilisateur (ou la communauté) crée un **nœud `Loi` enfant** (Nœud 1.1) qui précise l'alinéa (ex: ajout d'une exception ou d'un palier progressif).
   * D'autres profils créent un **nœud frère** (Nœud 1.2) avec une autre précision. L'arbre s'élargit pour couvrir toutes les réalités de terrain.

### Phase 4 : Validation & Flèche Ascendante (Consensus & Fusion)
7. **Étape 7 — Vote Multi-Critères (`Vote`) :**
   * Les utilisateurs votent sur les différentes branches (1.1, 1.2, 1.3) via un **Jugement Majoritaire** (*Très satisfaisant $\rightarrow$ À rejeter*).
   * Le système affiche en temps réel la **Matrice de Compatibilité** : quels profils socio-économiques et quels **`Groupes`** politiques valident chaque branche.
8. **Étape 8 — Détection de Compatibilité & Merge :**
   * Dès que deux nœuds frères (ex: 1.1 et 1.2) ne sont pas contradictoires sur leurs objets **`Infos`** et **`Lieu`**, et obtiennent une validation mutuelle des votants des deux branches, le système propose une **Fusion (Merge)**.
   * Les votants valident la synthèse $\rightarrow$ les deux nœuds fusionnent en un nœud consolidé qui remonte d'un cran dans l'arborescence (**Flèche Ascendante**).
9. **Étape 9 — Transmission Automatisée aux Décideurs :**
   * Une fois la racine atteinte, la **`Loi` de Consensus** (accompagnée de ses preuves statistiques par profil, par circonscription PostGIS et par groupe politique) est publiée dans le tableau de bord des élus (`Personnes` officielles), leur fournissant un texte prêt à être déposé comme amendement réel à l'Assemblée ou au Sénat.