# mail-smoke

Smoke IMAP en lecture seule, sans serveur MCP. Copier `.env.example` vers `.env`
et renseigner `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`. Aucun fichier YAML n’est
nécessaire : TLS sur le port 993 et tous les dossiers accessibles par défaut.

`make test` ignore les tests live ; `make test-real` autorise les lectures réelles.
Les tests découvrent les dossiers sélectionnables, recherchent quelques en-têtes
et lisent un mail si présent, sans modification de flags ni affichage de contenu privé.

Pour une configuration avancée, copier `config.example.yaml` vers `config.yaml`
et définir `MCP_MAIL_CONFIG=./config.yaml` dans `.env`. Le YAML devient prioritaire.
Ce dossier fait partie du dépôt principal ; `.env`, `config.yaml` et `.venv/`
restent ignorés par Git.
