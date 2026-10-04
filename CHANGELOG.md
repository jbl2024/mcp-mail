# Changelog

## [Unreleased]

- Start with IMAP_HOST, IMAP_USER and IMAP_PASSWORD only; YAML configuration is optional.
- Use TLS on port 993 and safe defaults; explicit YAML configuration takes precedence.

- Discover all selectable IMAP folders by default; folder restrictions are optional.
- Search all folders unless a specific folder is requested, with global pagination,
  per-message folder identity and explicit partial errors.
- Update mail-smoke to discover selectable folders.

## [20261004-1] - 2026-10-04

- chore: remove remaining CalDAV references (4d81ca6)
- Update changelog (e08784c)


## [20261004] - 2026-10-04

- Initial commit (d0bcc28)
