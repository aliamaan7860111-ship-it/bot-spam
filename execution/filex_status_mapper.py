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


def map_status(filex_status: str, current_notion_status: str | None) -> str:
    """
    Convert a Filex webhook status text into the Notion FILEX STATUS value.

    For 'In OPS': the result depends on the current Notion status (e.g.
    'MNA' + 'In OPS' → 'MNA-OPS'). For everything else, uses DIRECT_MAP
    or passes through unknown values.
    """
    s = filex_status.strip()
    if s == "In OPS":
        return IN_OPS_COMPOUND.get(current_notion_status, "In OPS")
    return DIRECT_MAP.get(s, s)
