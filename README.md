# mcp-mail

Serveur MCP IMAP strictement en lecture seule, Python 3.12+, basé sur IMAPClient.
Aucun calendrier, envoi, changement de flags, déplacement ou suppression.

## Installation et configuration

```sh
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
```

Renseigner les variables de connexion dans `.env`, puis adapter les comptes et la liste
explicite des dossiers autorisés dans `config.yaml`. Les identifiants sont uniquement
référencés par noms de variables d’environnement. TLS avec vérification des certificats
est obligatoire : `security: tls` (port 993) ou `security: starttls` (port 143).
Plusieurs comptes et profils de connexion sont possibles.

```sh
uv run --env-file .env mcp-mail
```

Le transport MCP est stdio. Pour un client MCP, utiliser `uv` avec les arguments
`run`, `--directory`, le chemin du projet, `--env-file`, le chemin du fichier privé,
et `mcp-mail`. `MCP_MAIL_CONFIG` choisit le fichier YAML (défaut : `config.yaml`).

## Outils

| Outil | Fonction |
|---|---|
| `list_accounts` | Alias, labels et dossiers configurés |
| `list_folders` | Dossiers autorisés et disponibilité |
| `search_messages` | Recherche IMAP côté serveur et en-têtes paginés |
| `get_message` | Corps MIME en Markdown et métadonnées des pièces jointes |
| `get_attachment` | Pièce jointe encodée en base64, sans écriture de fichier |
| `get_thread` | En-têtes liés par Message-ID, References et In-Reply-To |

La recherche combine `query` (TEXT), `sender`, `recipient`, `subject`, `seen`,
`flagged`, `important`, `since` et `before`. `seen: false` sélectionne les non-lus.
`important` signifie `\\Flagged` ou le mot-clé `$Important`, dont la prise en charge
dépend du serveur. Ce n’est pas une classification automatique du contenu.
Les dates utilisent `YYYY-MM-DD`, sur la date interne IMAP : début inclus, fin exclue.
Les chaînes sont échappées par IMAPClient ; aucun critère IMAP brut n’est exposé.
Les recherches Unicode utilisent UTF-8 ; le serveur doit accepter ce charset.

Les résultats sont classés par UID décroissant (ordre d’arrivée dans le dossier).
`limit` et `offset` paginent les en-têtes ; `total` et `next_offset` sont retournés.
Les UIDs correspondants sont récupérés par SEARCH ; seuls les en-têtes de la page
sont téléchargés. La pagination reflète l’état courant, et peut bouger à l’arrivée
ou suppression d’un mail.

Pour lire un mail, conserver `account`, `folder`, `uid` et `uidvalidity` issus de
la recherche. Une modification de UIDVALIDITY invalide les anciens identifiants.
Le corps texte est préféré à HTML, converti avec markdownify si nécessaire.
Les pièces jointes ont un `index` à utiliser dans `get_attachment` ; leur nom
reste une métadonnée et n’est jamais utilisé comme chemin local.

Les fils sont recherchés dans le même dossier via un parcours des en-têtes récents,
limité par `max_thread_messages`. `scan_truncated` indique un parcours incomplet,
et `truncated` une limite de restitution. Les sujets identiques ne suffisent pas
à relier deux mails. Il n’y a pas de recherche de fils entre dossiers.

## Lecture seule et limites

Chaque opération ouvre une session indépendante. La sélection utilise
`readonly=True` (EXAMINE), les téléchargements `BODY.PEEK`, et la fermeture LOGOUT.
Aucune commande de modification ni CLOSE/EXPUNGE n’est appelée. Lire un mail ne
modifie pas son statut lu/non lu. Les erreurs serveur sont masquées afin de ne pas
exposer des informations de connexion.

Les corps et pièces jointes sont bornés par taille ; le corps Markdown peut être
tronqué avec un indicateur explicite. L’extraction charge le message MIME complet
sous `max_message_bytes`, puis vérifie `max_attachment_bytes`. Le base64 augmente
la taille du résultat. Aucun cache persistant n’est créé. Les champs de mail,
liens et pièces jointes restent des contenus externes non fiables.

## Tests et smoke

```sh
make test
uv run --env-file .env mail-smoke --config config.yaml --live
```

`make test` utilise exclusivement des réponses IMAP simulées et un dépôt Git local
pour les tests de release. Le smoke appelle directement le service sans lancer MCP :
il vérifie les dossiers, recherche cinq mails maximum et lit le premier message
si disponible. Il n’affiche que des compteurs et statuts, sans corps ni identifiants
de messages. `--live` est obligatoire pour autoriser une connexion réelle.

Le dossier `mail-smoke/`, inclus dans ce dépôt, fournit également `make test-real`.
Créer ses fichiers privés `config.yaml` et `.env` à partir des exemples, puis
renseigner les paramètres IMAP. Ces fichiers privés restent ignorés par Git.

`make build` construit le paquet ; `make release` conserve le mécanisme de release
avec tests, changelog, commit et publication atomique vers le remote configuré.
