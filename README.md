# mcp-mail

Serveur MCP IMAP strictement en lecture seule, Python 3.12+, basé sur IMAPClient.
Aucun calendrier, envoi, changement de flags, déplacement ou suppression.

## Installation et configuration

```sh
uv sync
cp .env.example .env
```

Renseigner seulement trois variables dans `.env` : `IMAP_HOST`, `IMAP_USER` et
`IMAP_PASSWORD`. Le serveur crée le compte `primary`, se connecte en TLS sur le
port 993 avec vérification des certificats et découvre tous les dossiers.
Les délais et limites de taille utilisent des valeurs par défaut.

```sh
uv run --env-file .env mcp-mail
```

Le transport MCP est stdio. Pour un client MCP, utiliser `uv` avec les arguments
`run`, `--directory`, le chemin du projet, `--env-file`, le chemin du fichier privé,
et `mcp-mail`. Le client peut aussi fournir directement les trois variables
d’environnement au processus. `.env` est chargé par `uv --env-file`.

### Configuration avancée facultative

Pour plusieurs comptes, STARTTLS, un port particulier, une restriction de dossiers
ou des limites personnalisées, copier `config.example.yaml` vers `config.yaml`,
puis définir `MCP_MAIL_CONFIG=./config.yaml`. Ce fichier est prioritaire sur le
mode à trois variables. Un fichier explicitement demandé mais invalide provoque
une erreur ; il n’y a pas de repli silencieux vers une autre connexion.
Sans `MCP_MAIL_CONFIG`, un éventuel fichier `config.yaml` local est ignoré.
Le smoke accepte également `--config config.yaml`.

Les identifiants du YAML sont uniquement référencés par noms de variables
d’environnement. TLS avec vérification des certificats est obligatoire :
`security: tls` (port 993) ou `security: starttls` (port 143).
Une liste `folders` facultative permet de restreindre les dossiers accessibles.

## Outils

| Outil | Fonction |
|---|---|
| `list_accounts` | Alias, labels et restriction facultative de dossiers |
| `list_folders` | Découverte des dossiers du serveur et disponibilité |
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

Sans `folder`, la recherche parcourt tous les dossiers sélectionnables accessibles
du compte demandé, y compris les archives et les messages envoyés. Avec
`folder: "INBOX"` ou `folder: "Archive"`, elle cible uniquement ce dossier.
Une restriction `folders` configurée reste appliquée à toutes les opérations.
Les résultats sont classés par nom de dossier puis UID décroissant dans chaque
dossier ; il ne s’agit pas d’un classement chronologique global.
`limit` et `offset` paginent les en-têtes ; `total` et `next_offset` sont retournés.
Chaque résultat porte `account`, `folder`, `uid` et `uidvalidity` : les UIDs ne
sont pas comparables entre dossiers. Un message présent dans plusieurs dossiers
peut apparaître plusieurs fois. `partial` et `errors` signalent les dossiers dont
la recherche ou la lecture a échoué ; `total` compte les correspondances des
dossiers recherchés avec succès. Une recherche sur plusieurs dossiers prend
plus de temps, car IMAP recherche dossier par dossier.
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

Chaque opération ouvre une session indépendante. Une seule opération par compte
et quatre au maximum au total sont actives. Une annulation ne libère pas la
capacité avant la fin réelle du travail réseau. La sélection utilise
`readonly=True` (EXAMINE), les téléchargements `BODY.PEEK`, et la fermeture LOGOUT.
Aucune commande de modification ni CLOSE/EXPUNGE n’est appelée. Lire un mail ne
modifie pas son statut lu/non lu. Les erreurs serveur sont masquées afin de ne pas
exposer des informations de connexion.

Les corps et pièces jointes sont bornés par taille ; le corps Markdown peut être
tronqué avec un indicateur explicite. L’extraction charge le message MIME complet
sous `max_message_bytes`, puis vérifie `max_attachment_bytes`. Le base64 augmente
la taille du résultat. Un cache mémoire temporaire par compte évite les téléchargements répétés :
les corps MIME sont réutilisés pendant 30 secondes au maximum, avec un budget de 20 Mio
et 32 entrées. L’existence du message, ses flags et UIDVALIDITY sont revérifiés
à chaque lecture. Les correspondances de recherche restent en cache 10 secondes
(50 000 UIDs et 32 entrées maximum), partagées entre pages d’une même recherche.
Les en-têtes et flags ne sont pas mis en cache ; les nouveaux résultats de
recherche peuvent apparaître après ce délai. Les clés distinguent dossiers et
UIDVALIDITY. Les entrées expirées sont retirées au prochain accès au cache ;
aucun contenu n’est écrit sur disque. Les budgets concernent les données
conservées et non la mémoire totale du processus. Les champs de mail,
liens et pièces jointes restent des contenus externes non fiables.

## Tests et smoke

```sh
make test
uv run --env-file .env mail-smoke --live
```

`make test` utilise exclusivement des réponses IMAP simulées et un dépôt Git local
pour les tests de release. Le smoke appelle directement le service sans lancer MCP :
il découvre les dossiers sélectionnables, recherche cinq mails maximum par dossier
et lit le premier message
si disponible. Il n’affiche que des compteurs et statuts, sans corps ni identifiants
de messages. `--live` est obligatoire pour autoriser une connexion réelle.

Le dossier `mail-smoke/`, inclus dans ce dépôt, fournit également `make test-real`.
Créer son fichier privé `.env` à partir de `.env.example`, puis renseigner les
trois variables IMAP. Le YAML reste facultatif. Les fichiers privés sont ignorés
par Git.

`make build` construit le paquet ; `make release` conserve le mécanisme de release
avec tests, changelog, commit et publication atomique vers le remote configuré.
