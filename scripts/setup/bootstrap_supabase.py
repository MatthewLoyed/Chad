"""
One-command bootstrap for the OPTIONAL Supabase upgrade tier (issue #403).

Chad works with zero Supabase configuration out of the box — this script is only
for users who want the upgrade: richer memory/RAG and (eventually) cross-device
chat history sync.

Persona prompts are NOT part of this. As of #589 they are local files
(prompts/persona + config/persona, see src/infrastructure/repositories/persona_store.py)
for every tier, so there is no persona seeding step here and Supabase is never
consulted for prompts.

Runs, in order:
  1. apply_migrations.py   — creates every table + RPC function Chad needs
  2. refresh_rag_tools.py's tool sync — discovers local + MCP tools, seeds tool RAG

After a brand-new Supabase project has its URL/keys/DB connection string in
`.env` (see docs/setup/COLD_START.md's "Optional: Supabase upgrade path"),
this is the only command needed:

    $env:PYTHONPATH="."; .\\.venv\\Scripts\\python.exe scripts\\setup\\bootstrap_supabase.py

Safe to re-run — every step is idempotent.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("BootstrapSupabase")


def main() -> None:
    load_dotenv()

    missing = [
        var for var in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_DB_URL")
        if not os.getenv(var)
    ]
    if missing:
        logger.error(
            f"Missing required .env variable(s): {', '.join(missing)}. "
            "See docs/setup/COLD_START.md step 3."
        )
        sys.exit(1)

    logger.info("=== Step 1/2: applying schema migrations ===")
    from scripts.setup.apply_migrations import apply_all
    apply_all(os.getenv("SUPABASE_DB_URL"))

    logger.info("\n=== Step 2/2: seeding tool RAG (discovers local + MCP tools) ===")
    from src.scripts.refresh_rag_tools import refresh_supabase_tools
    asyncio.run(refresh_supabase_tools())

    logger.info(
        "\nDone. Supabase is provisioned — start the Brain "
        "(src/remote/server.py) and it should boot with a working persona and tool RAG."
    )


if __name__ == "__main__":
    main()
