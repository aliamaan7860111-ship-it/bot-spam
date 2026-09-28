"""
cutover.py
==========
One switch for the day Notion stopped being the answer.

Nine services write to the Notion orders database. Retiring it by deleting
those calls would be a change nobody could undo in a hurry, on a Sunday, with
the fulfilment team waiting - and the one thing a cutover needs is a way back
that takes seconds.

So every Notion write on the orders side asks this first. With
`NOTION_RETIRED=1` in .env they all go quiet together; without it they all
come back together. One line, one restart, both directions.

Two things this deliberately does NOT cover:

  * Reading. A read from Notion after the cutover is not a safety net, it is
    a stale answer. Those are repointed at GRQ OS outright, behind their own
    per-bot flags, so a bot is either reading the new system or the old one
    and never half of each.

  * The leads ticket database. It is a different Notion database and it still
    holds the thing GRQ OS has not been given yet: the round-robin roster
    that decides whose lead this is. Leads keep dual-writing until the rota
    is built. `NOTION_RETIRED` says nothing about them, which is why this is
    written down here rather than assumed.

Read at call time, never at import: a module imported before load_dotenv sees
an empty environment, and "inert" looks exactly like "nothing to do".
"""
from __future__ import annotations

import os

_TRUE = ("1", "true", "yes", "on")


def notion_retired() -> bool:
    """True once Notion is no longer written to for orders."""
    return os.getenv("NOTION_RETIRED", "").strip().lower() in _TRUE


def write_notion() -> bool:
    """
    Readable at the call site: `if cutover.write_notion(): ...`

    The negative form reads badly around the writes it guards, and a guard
    that is hard to read is a guard somebody eventually inverts.
    """
    return not notion_retired()
