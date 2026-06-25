# KAIZEN — problèmes rencontrés, causes et solutions

> Journal d'amélioration continue du bot LinkedIn PREPZfy. But : ne jamais reperdre du
> temps sur un problème déjà résolu. Pour chaque souci : **Symptôme → Cause → Solution → Leçon.**
> (Voir aussi `HANDOVER.md` pour l'état général du projet.)

---

## A. Déclenchement automatique (GitHub Actions)

### A1. Le run quotidien ne se déclenchait jamais tout seul
- **Symptôme :** le workflow était `active`, mais **0 exécution de type `schedule`** pendant plusieurs jours. Rien ne se postait le matin.
- **Cause :** le cron était réglé sur `0 5 * * *`, soit **pile à l'heure ronde (05:00)**. GitHub Actions **retarde fortement, voire ignore**, les tâches planifiées qui tombent à l'heure pile (forte charge serveur).
- **Solution :** décaler la minute hors de l'heure ronde → `cron: "17 6 * * *"` (06:17 UTC). Depuis, les runs planifiés partent (souvent avec **du retard**, ex. 09:46 au lieu de 06:17 — c'est normal, le cron GitHub est "best-effort").
- **Leçon :** ne jamais planifier un cron GitHub à `:00`. Et accepter que l'heure exacte n'est **pas garantie**. Pour une heure stricte, il faudrait un déclencheur externe (service cron qui appelle l'API GitHub).

### A2. "Re-run" rejoue l'ANCIEN code
- **Symptôme :** on corrige le code, on clique **"Re-run"** sur un run existant, et on revoit l'ancien comportement.
- **Cause :** **"Re-run" rejoue le commit d'origine** de ce run, pas la dernière version.
- **Solution :** toujours utiliser **"Run workflow"** (Actions → le workflow → bouton "Run workflow") pour tester du code à jour.
- **Leçon :** "Re-run" = rejouer le passé ; "Run workflow" = lancer le présent.

### A3. "Success" ne veut pas dire "publié"
- **Symptôme :** un run planifié affiche **success**, mais **rien n'est posté** sur LinkedIn.
- **Cause :** "success" signifie seulement que le programme **n'a pas planté**. `daily_post.py`/`publish.py` se terminent proprement (code 0) quand il n'y a **rien à poster** (aucune offre retenue) ou en cas de **skip volontaire**.
- **Solution :** lire le contenu du run, pas seulement la pastille verte. Vérifier qu'une **carte a bien été générée** (`docs/cards/<date>.png`) et que l'étape "Publish to LinkedIn via Buffer" dit bien **"Posted to Buffer"**.
- **Leçon :** vert ≠ posté. Toujours vérifier l'artefact + le log de l'étape de publication.

---

## B. Publication Buffer (API GraphQL `https://api.buffer.com`)

### B1. `createPost` renvoyait HTTP 400 (format de l'image)
- **Symptôme :** la lecture des channels marchait, mais créer le post échouait en **HTTP 400**.
- **Cause :** l'image était envoyée comme `assets: [{ url, mimeType }]`. Or le type `AssetInput` de Buffer est un **`@oneOf`** : il faut préciser la variante. `mimeType` n'existe pas à ce niveau.
- **Solution :** `assets: [{ image: { url: $url } }]`.
- **Leçon :** un input GraphQL `@oneOf` exige **une variante nommée** (`image`/`video`/...), pas des champs à plat.

### B2. `createPost` renvoyait HTTP 400 (types des variables)
- **Symptôme :** toujours 400 après B1.
- **Cause :** variables GraphQL mal typées : `$channelId: String!` et `$dueAt: String!`.
- **Solution :** utiliser les **scalaires exacts de Buffer** : `$channelId: ChannelId!` et `$dueAt: DateTime!`.
- **Leçon :** respecter les scalaires custom du schéma (ne pas tout mettre en `String`).

### B3. Les erreurs Buffer étaient illisibles
- **Symptôme :** le log montrait seulement `HTTP Error 400: Bad Request`, sans détail → impossible de corriger.
- **Cause :** `urllib` lève une `HTTPError` sans qu'on lise le **corps** de la réponse (qui contient le vrai message GraphQL).
- **Solution :** dans `_graphql`, intercepter `HTTPError`, lire `e.read()`, et remonter le message exact (ex. *"Variable $channelId ... expecting ChannelId!"*). C'est ce qui a permis de régler B2 et d'identifier B5.
- **Leçon :** toujours **faire remonter le corps d'erreur** d'une API, sinon on débogue à l'aveugle.

### B4. Un post de TEST est parti sur la vraie Page
- **Symptôme :** un post bidon ("PREPZfy test post...") a été publié sur la vraie Page LinkedIn.
- **Cause :** un workflow de test (`buffer_test.yml`) publiait **pour de vrai** (pas en dry run) avec une **caption de démo**, vers la **vraie Page**.
- **Solution :** workflow de test supprimé. **Règle :** ne jamais déboguer Buffer avec une caption de test sur la Page live. Pour tester sans risque : **dry run** (prépare/affiche sans publier), ou le workflow lecture seule **"Buffer key check"**.
- **Leçon :** isoler les tests de la prod. Un canal de test ne doit jamais pointer vers la vraie audience.

### B5. La clé Buffer était refusée ("Not authorized to access this resource")
- **Symptôme :** même la simple lecture des channels échouait ; la clé avait pourtant marché 2 jours avant.
- **Cause :** la valeur dans le secret `BUFFER_API_KEY` était **invalide / périmée / pas la bonne** (une clé valide vient de **https://publish.buffer.com/settings/api** et commence par **`buf_`**).
- **Solution :** créer un workflow **lecture seule "Buffer key check"** (aucun coût Anthropic) qui affiche **présence + longueur** de la clé puis tente la lecture → ça distingue "secret vide" de "clé refusée par Buffer". Puis régénérer une clé `buf_` et mettre à jour le secret.
- **Leçon :** un diagnostic doit séparer les couches (secret GitHub vs validité côté Buffer). Ne jamais afficher la clé elle-même, seulement sa **longueur**.

### B7. La VRAIE cause du "Not authorized" : la clé ne peut pas LIRE les channels
- **Symptôme :** clé fraîche, bien collée, et pourtant `list_channels()` renvoyait toujours "Not authorized to access this resource". Plusieurs jours perdus à soupçonner une mauvaise clé.
- **Cause :** un **diagnostic par paliers** (compte → organisations → channels) a montré que la clé lit très bien le **compte** et l'**organisation**, mais **pas** la **liste des channels**. La nouvelle clé "LKDN" (créée le 24/06) a été générée avec des permissions qui **n'incluent pas la lecture des channels** (l'ancienne l'avait → "ça marchait avant"). Or **publier n'a pas besoin** de lire les channels : il faut seulement `posts:write` + l'**ID du channel** (déjà connu : `6a39468f5ab6d2f1065c965c`).
- **Solution :** ne plus appeler `list_channels()` avant de poster ; publier **directement sur l'ID du channel connu** (constante `DEFAULT_LINKEDIN_CHANNEL_ID`, surchargée par `BUFFER_CHANNEL_ID`). Une lecture facultative qui échoue ne doit jamais bloquer l'action principale.
- **Leçon double :** (1) **diagnostiquer par paliers** quand une requête composite échoue, au lieu de conclure "la clé est mauvaise" ; (2) ne jamais faire dépendre une action (publier) d'une lecture annexe (lister) dont on connaît déjà le résultat.

### B6. Le premier commentaire n'est pas automatisable
- **Symptôme :** on voulait que le bot poste aussi le 1er commentaire (lien `jobs.prepzfy.com`).
- **Cause :** l'API Buffer (beta) **n'expose pas les commentaires**.
- **Solution :** le bot **publie image + caption** et **affiche les variantes** de premier commentaire ; le propriétaire en colle une **à la main** (idéalement depuis son profil perso).
- **Leçon :** connaître les limites d'une API beta avant de promettre une auto complète.

---

## C. Logique du pipeline (sélection / contenu)

### C1. Le filtre "attractivité" pouvait tout bloquer en silence
- **Symptôme :** certains jours (ex. 25/06) **aucune carte générée**, run "success", rien publié.
- **Cause :** le filtre `prefilter_known` ne garde que les offres ayant un **logo Brandfetch**. Si Brandfetch est en panne/limité, ou si les boîtes du jour sont inconnues de Brandfetch, **toute la liste est vidée** → plus rien à publier → skip silencieux.
- **Solution :** rendre le filtre **fail-safe** : s'il reste moins de `MIN_OFFERS` (2) offres, **retomber sur la liste fraîche complète** pour publier quand même.
- **Leçon :** un filtre de qualité ne doit **jamais** pouvoir réduire le résultat à zéro sans repli. Toujours un plan B.

### C2. Le tagging des entreprises (et son filet de sécurité)
- **Symptôme :** risque qu'un tag mal formé fasse échouer toute la publication.
- **Cause :** les mentions LinkedIn (`metadata.linkedin.annotations`) exigent des données exactes (id numérique d'organisation, positions dans le texte).
- **Solution :** `publish_with_fallback` tente **avec** les tags ; si Buffer/LinkedIn refuse, **republie sans les tags**. Les IDs sont dans `li_companies.json` (entrées sans `id` ignorées).
- **Leçon :** une fonctionnalité "bonus" (tags) ne doit jamais pouvoir casser la fonction principale (publier).

### C3. Erreur d'analyse de ma part : "on ne peut pas tagger"
- **Symptôme :** j'avais d'abord affirmé que tagger les entreprises était impossible.
- **Cause :** réponse de mémoire, sans vérifier la doc.
- **Solution :** après vérification, Buffer **supporte** les mentions d'organisations LinkedIn → fonctionnalité construite.
- **Leçon :** vérifier la doc avant de dire "impossible".

---

## D. Méthode de travail

### D1. Une seule action à la fois, pas de détour "au cas où"
- **Symptôme :** instructions confuses (ex. envoyer chercher un "API Explorer" en option, puis dire "pas besoin") → perte de temps et agacement.
- **Cause :** mélange de l'étape essentielle avec des options facultatives.
- **Solution :** **une seule action claire** à la fois ; garder les alternatives pour moi ; **vérifier moi-même** les résultats (via l'API GitHub) quand c'est possible, au lieu de faire lire des logs.
- **Leçon :** clarté > exhaustivité. Le propriétaire est non-technique : un chemin unique, sans bruit.

---

## Réflexes à garder (checklist rapide)
- Tester du nouveau code → **"Run workflow"**, jamais "Re-run".
- Cron GitHub → **hors heure ronde**, et heure non garantie.
- Run vert → **vérifier qu'une carte existe + le log "Posted to Buffer"**.
- Déboguer Buffer → **dry run** ou **"Buffer key check"**, **jamais** sur la Page live.
- Clé Buffer → vient de **publish.buffer.com/settings/api**, commence par **`buf_`**.
- Tout filtre/tag → **fail-safe** : ne jamais réduire à zéro ni casser la publication.
- Secrets → ne jamais afficher la valeur, seulement présence/longueur.
