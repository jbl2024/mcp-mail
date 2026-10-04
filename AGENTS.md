# Agent instructions

- Keep the server strictly read-only: do not add write tools, business APIs, or
  HTTP write requests.
- Select folders with readonly=True; fetch content only with BODY.PEEK.
- Never change flags, send mail, append, move, copy, delete or expunge messages.
- Keep mail-smoke opt-in; never migrate private credentials into examples.
- Never add secrets, real identifiers, personal names, or real infrastructure
  URLs to examples, tests, or documentation.
- Use reserved domains (`example.test`, `example.invalid`) and fictional data
  in all examples.
- Every change to the IMAP reader must have a test that simulates server
  responses without depending on an external service.
- Run `make test` before proposing a change.
- Always generate a commit message at the end of an action which updates code or docs (format: `feat: ...`, `fix: ...`, `docs: ...`, etc.).

## Validate changes

Run these commands from the repository root before proposing a change:

```sh
uv sync
make test
uv run ruff check src tests
uv run ruff format --check src tests
```

- `make test` runs offline tests, including simulated IMAP responses and smoke
  coverage. It must pass; fix failures before reporting completion.
- Add or update simulated IMAP tests for changes to the reader. Never make the
  ordinary test suite depend on a live mailbox.
- For packaging or dependency changes, also run `make build`.
- Report validation results and any checks that could not be run. A skipped live
  test is not evidence that a real IMAP connection succeeded.

## Live read-only smoke

After changes affecting IMAP connection, folder discovery, search or message
reading, also run the standalone smoke when private connection credentials are
available and live access is authorized for the task:

```sh
uv run --env-file .env mail-smoke --live
```

The private `.env` needs `IMAP_HOST`, `IMAP_USER` and `IMAP_PASSWORD`. TLS on port
993 and all selectable folders are the defaults. For advanced configuration,
set `MCP_MAIL_CONFIG` in `.env` or explicitly pass the YAML file:

```sh
uv run --env-file .env mail-smoke --config config.yaml --live
```

The integrated `mail-smoke/` test project provides an alternative. Run from that
folder, using its own private `.env`:

```sh
make test-real
```

- These commands connect to the real IMAP server without starting MCP. They
  discover selectable folders, search a small page and read one message per
  non-empty folder; they must never modify messages or flags.
- Keep the smoke opt-in (`--live` or `LIVE_MAIL=1`). Running `make test` inside
  `mail-smoke/` skips the live test by default.
- Never print or commit credentials, private configuration or mail content.
  Use the existing count/status output when reporting smoke results.
- If credentials or authorized live access are unavailable, report that the live
  smoke was not run; still complete the offline validation.
