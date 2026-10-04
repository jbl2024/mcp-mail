# mail-smoke

Smoke IMAP en lecture seule, sans serveur MCP. Copier `config.example.yaml` vers
`config.yaml` et `.env.example` vers `.env`, puis renseigner la configuration IMAP
privée. Ce dossier fait partie du dépôt principal ; les fichiers `.env`,
`config.yaml` et le répertoire `.venv/` restent ignorés par Git.

`make test` ignore les tests live ; `make test-real` autorise les lectures réelles.
Les tests découvrent les dossiers sélectionnables du serveur (ou ceux de la
restriction facultative `folders`), recherchent quelques en-têtes et lisent
un mail si présent, sans modification de flags ni affichage de contenu privé.
