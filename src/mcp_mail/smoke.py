"""Opt-in standalone reader smoke, without importing or starting MCP."""

import argparse
import asyncio
import json
from argparse import Namespace

from .config import ConfigError, load_config
from .reader import MailReadError
from .service import MailService


async def run(args: Namespace) -> None:
    """Validate discovery, search and one read per non-empty selectable folder.

    ``args.config`` is an optional explicit YAML path; None uses the normal
    configuration precedence. Only counts and status metadata are written to
    stdout. ConfigError/MailReadError propagate so failed reads cannot report success.
    Calling this function directly assumes the caller has opted into live access.
    """
    service = MailService(load_config(args.config))
    for account in service.config.accounts.values():
        discovered = await service.call("list_folders", account.name)
        for entry in discovered["result"]:
            if not entry["selectable"]:
                continue
            folder = entry["name"]
            result = await service.call(
                "search_messages",
                account.name,
                folder=folder,
                limit=min(5, service.config.settings.max_results),
            )
            page = result["result"]
            if page["messages"]:
                uid = page["messages"][0]["uid"]
                await service.call(
                    "get_message",
                    account.name,
                    folder=folder,
                    uid=uid,
                    uidvalidity=page["uidvalidity"],
                )
            print(
                json.dumps(
                    {
                        "account": account.name,
                        "folder": folder,
                        "matched": page["total"],
                        "status": "ok",
                    }
                )
            )


def main() -> None:
    """Require --live and run the smoke; report failures without exception-chain details."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--live", action="store_true", help="Authorize an actual IMAP connection")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to opt into real IMAP reads")
    try:
        asyncio.run(run(args))
    except (ConfigError, MailReadError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
