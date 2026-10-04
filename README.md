# mcp-mail

Serveur MCP IMAP strictement en lecture seule, Python 3.12+, basé sur IMAPClient.
Aucun calendrier, envoi, changement de flags, déplacement ou suppression.

## Installer pour utiliser le serveur MCP

Prérequis : un checkout complet du projet (avec `uv.lock`) et `uv` installé.
Le projet demande Python 3.12 ou supérieur ; `uv` prépare l’environnement Python.
Depuis le dossier du projet :

```sh
make install
```

Sans `make`, utiliser `sh scripts/install.sh`. L’installation utilise le verrou de
dépendances, installe le paquet et ses dépendances de production dans `.venv`,
puis vérifie les imports du serveur. Elle ne contacte aucune boîte IMAP et ne
requiert aucun identifiant. Le paquet est installé sans mode éditable : après une
mise à jour du code, relancer `make install`, puis redémarrer le client MCP.

L’utilisateur qui installe doit pouvoir créer ou modifier `.venv` et son contenu.
L’utilisateur qui lance le serveur doit pouvoir lire et exécuter cet environnement.
Le projet vérifie les permissions de base et explique les échecs d’installation ;
il ne change pas les propriétaires ou permissions système automatiquement.

### Configurer et lancer

Fournir seulement trois variables au processus : `IMAP_HOST`, `IMAP_USER` et
`IMAP_PASSWORD`. Le compte s’appelle `primary`, utilise TLS sur le port 993 avec
vérification des certificats et découvre tous les dossiers. Les délais et limites
utilisent des valeurs par défaut.

Configurer le client MCP pour exécuter **le script de lancement installé**.
Exemple à adapter avec le chemin absolu du checkout et les valeurs privées :

```json
{
  "mcpServers": {
    "imap": {
      "command": "/bin/sh",
      "args": ["/path/to/mcp-mail/scripts/run.sh"],
      "env": {
        "IMAP_HOST": "imap.example.test",
        "IMAP_USER": "mail-user@example.test",
        "IMAP_PASSWORD": ""
      }
    }
  }
}
```

Le mot de passe vide est un emplacement à renseigner via les paramètres privés
du client. La forme exacte de cette configuration dépend du client MCP.
Le transport est stdio : le client lance le processus et communique avec lui.

Pour un lancement manuel avec les variables déjà présentes dans l’environnement :

```sh
make run
```

Le script utilise directement `.venv/bin/python -B -m mcp_mail`. Il ne résout,
n’installe et ne met à jour aucune dépendance, et ne nécessite pas `uv` au lancement.
Il fonctionne depuis n’importe quel dossier ; les chemins YAML relatifs sont
résolus par rapport au checkout. Il ne charge pas automatiquement `.env`.

Pour un fichier `.env` privé, copier `.env.example` et renseigner les trois valeurs,
puis utiliser `uv` uniquement comme lanceur, **sans synchronisation** :

```sh
uv run --no-sync --env-file .env python -m mcp_mail
```

Ce lancement suppose que `make install` a déjà réussi. Ne pas utiliser `uv run`
sans `--no-sync` comme commande de déploiement : il peut tenter de modifier `.venv`
avant de démarrer le serveur.

### Comprendre une erreur « Permission denied »

Une erreur pendant la suppression ou le remplacement d’un fichier dans `.venv`
indique que la mise à jour de l’environnement n’a pas pu aboutir. Vérifier son
propriétaire et les droits avec l’administrateur, puis relancer `make install`
avec l’utilisateur autorisé. Le script n’efface pas un environnement existant.
Un démarrage sans synchronisation ne répare pas une installation partielle.

`mail-smoke` est une commande utilitaire installée avec le paquet. Sa présence
dans un message d’installation ne signifie pas que le smoke a été lancé.
Il n’est jamais exécuté par l’installation ou le lancement MCP.

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

## Développement et tests hors ligne

Le développement utilise un environnement éditable avec les outils de test :

```sh
uv sync
make test
uv run ruff check src tests
uv run ruff format --check src tests
```

`make dev` lance le serveur avec `uv run` pour le développement, avec les variables
IMAP déjà fournies. Pour un `.env` local : `uv run --env-file .env mcp-mail`.
`make test` utilise exclusivement des réponses IMAP simulées et un dépôt Git
local pour les tests de release. Il ne se connecte à aucune boîte réelle.
Après `make install`, `make test` peut réinstaller les dépendances de développement ;
ces commandes se lancent dans un checkout de développement disposant des droits
d’écriture, pas dans un environnement de production figé.

`make build` construit le paquet ; `make release` conserve le mécanisme de release
avec tests, changelog, commit et publication atomique vers le remote configuré.

## Smoke réel, facultatif

Après installation, avec un `.env` privé renseigné :

```sh
uv run --no-sync --env-file .env python -m mcp_mail.smoke --live
```

Si les variables sont déjà présentes dans l’environnement, `uv` n’est pas nécessaire :

```sh
.venv/bin/python -m mcp_mail.smoke --live
```

Le smoke appelle directement le service sans lancer MCP : il découvre les dossiers
sélectionnables, recherche cinq mails maximum par dossier et lit le premier message
si disponible. Il affiche seulement des compteurs et statuts. `--live` est obligatoire
pour autoriser une connexion réelle ; aucune modification de mail n’est effectuée.

Le dossier `mail-smoke/`, inclus dans ce dépôt, fournit également `make test-real`
pour le développement. Il utilise son propre `.env` et synchronise son environnement.
Le YAML reste facultatif. Les fichiers privés sont ignorés par Git.
