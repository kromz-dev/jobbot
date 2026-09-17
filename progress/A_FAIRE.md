# À faire (rappels)

## 1. Créer le compte francetravail.io (gratuit, ~5 min) — accepté le 17/09/2026, à faire plus tard

Pourquoi : active l'**API officielle France Travail « Offres d'emploi v2 »** dans JobBot
(données plus fiables que la page web : expérience exigée « débutant accepté », qualification, GPS, salaire,
jusqu'à 150 offres par requête au lieu de 60).

> La Bonne Boîte (établissements qui recrutent, candidatures spontanées) fonctionne **sans compte** :
> ce rappel ne bloque rien.

Étapes :
1. Aller sur https://francetravail.io et créer un compte (« Se connecter » → « Créer un compte »).
2. « Mes applications » → **Créer une application** (nom : `JobBot`, usage personnel).
3. Dans l'application, **ajouter l'API « Offres d'emploi v2 »** (catalogue → Offres d'emploi → S'abonner).
4. Copier l'**identifiant client** et la **clé secrète**.
5. Dans JobBot : onglet **Recherche → Intégrations → France Travail**, coller les deux valeurs,
   cliquer **Tester la connexion** puis **Enregistrer**.
   (Alternative : variables d'environnement `FT_CLIENT_ID` et `FT_CLIENT_SECRET`.)

Les clés sont stockées dans `data/config.json`, qui n'est **jamais** envoyé sur GitHub.

Statut : ☐ à faire
