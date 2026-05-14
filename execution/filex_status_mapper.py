"""Map Filex webhook Status text to Notion FILEX STATUS value."""

DIRECT_MAP = {
    "Order Placed":                   "Label Created",
    "To be Picked Up":                "Label Created",
    "Received at Hubs":               "Handed Off",
    "Assigned to Driver":             "Shipped",
    "Delivered":                      "Delivered",
    "Cancelled":                      "Cancelled",
    "Receiver Cancelled With Money":  "Receiver Cancelled With Money",
    "Received in CSA":                "Received in CSA",
    "Return to Origin":               "Return to Origin",
    "Location Changed":               "LC",
    "Schedule for tomorrow":          "SFT",
    "Mobile Not Answered":            "MNA",
    "Future Delivery":                "FD",
}

IN_OPS_COMPOUND = {
    "MNA": "MNA-OPS",
    "SFT": "SFT-OPS",
    "LC":  "LC-OPS",
    "FD":  "FD-OPS",
}

# Promotion of mapped FILEX STATUS into the main ORDER STATUS select.
# Values must match the EXACT option names in the Notion ORDER STATUS dropdown
# (including emoji prefixes — Notion silently no-ops if an option doesn't exist).
# Anything not listed here leaves ORDER STATUS untouched.
ORDER_STATUS_FROM_FILEX = {
    "Shipped":          "\U0001F69A SHIPPED",   # 🚚 SHIPPED
    "Delivered":        "✅ DELIVERED",     # ✅ DELIVERED
    "Return to Origin": "RTO",
}

# Downstream states that supersede a promotion target. If the order's current
# ORDER STATUS is one of these, do NOT promote — an operator has progressed it
# forward and we must not stomp their manual change on the next poll tick.
#
# Example: RTO -> ↩️ RETURNED is a manual transition done by ops after they
# physically verify the return. The 5-min Filex poller keeps reporting
# "Return to Origin", so without this guard it would keep reverting to RTO.
PROMOTION_DOWNSTREAM_STATES = {
    "RTO": {"↩️ RETURNED"},   # ↩️ RETURNED
}


def order_status_from_filex(
    filex_status: str | None,
    current_order_status: str | None = None,
) -> str | None:
    """Return the ORDER STATUS value to promote to (Shipped / Delivered / RTO),
    or None if this Filex status should not change the main ORDER STATUS.

    If the order is already in a downstream state (e.g. manually moved from
    RTO to ↩️ RETURNED), returns None so the promotion is skipped.
    """
    if not filex_status:
        return None
    target = ORDER_STATUS_FROM_FILEX.get(filex_status.strip())
    if not target:
        return None
    downstream = PROMOTION_DOWNSTREAM_STATES.get(target, set())
    if current_order_status and current_order_status.strip() in downstream:
        return None
    return target


def map_status(filex_status: str | None, current_notion_status: str | None) -> str:
    """
    Convert a Filex webhook status text into the Notion FILEX STATUS value.

    Inputs:
        filex_status: status text from the webhook payload. None or empty
                      string returns "".
        current_notion_status: the order's current FILEX STATUS in Notion,
                               used only to compute compound 'In OPS'
                               variants (e.g. MNA + In OPS = MNA-OPS).

    Behavior:
        - Whitespace around `filex_status` is stripped before matching.
        - Matching is exact-case. Filex's API has been consistent so far;
          if vendor casing ever drifts (e.g. 'DELIVERED'), the unknown
          value is passed through and Notion auto-creates the option.
        - Unknown statuses pass through unchanged so Notion can record
          them; we add them to DIRECT_MAP later.
    """
    if not filex_status:
        return ""
    s = filex_status.strip()
    if not s:
        return ""
    if s == "In OPS":
        return IN_OPS_COMPOUND.get(current_notion_status, "In OPS")
    return DIRECT_MAP.get(s, s)
