"""Opt-in standalone reader smoke, without importing or starting MCP."""

import argparse
import asyncio
import json

from .config import ConfigError, load_config
from .reader import MailReadError
from .service import MailService


async def run(args):
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


def main():
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
