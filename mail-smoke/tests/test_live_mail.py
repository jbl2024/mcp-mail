import os
from argparse import Namespace

import pytest

from mcp_mail.smoke import run

pytestmark = pytest.mark.skipif(os.getenv("LIVE_MAIL") != "1", reason="Live IMAP opt-in required")


async def test_mail_reads():
    await run(Namespace(config=os.environ.get("MCP_MAIL_CONFIG")))
