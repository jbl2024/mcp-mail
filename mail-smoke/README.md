# mail-smoke

Smoke IMAP en lecture seule, sans serveur MCP. Copier `config.example.yaml` vers
`config.yaml` et `.env.example` vers `.env`, puis renseigner la configuration IMAP
privée. Les anciens fichiers privés CalDAV sont conservés mais ne sont pas compatibles.

`make test` ignore les tests live ; `make test-real` autorise les lectures réelles.
Les tests parcourent les dossiers autorisés, recherchent quelques en-têtes et lisent
un mail si présent, sans modification de flags ni affichage de contenu privé.
