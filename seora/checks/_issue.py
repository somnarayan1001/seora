"""Building blocks shared by all check groups."""

SEVERITIES = ("error", "warning", "notice", "pass")


def issue(id, severity, title, detail="", fix=""):
    """One finding. title is short; detail says what was found; fix is a plain-English instruction.

    Passed checks normally have no fix. Keep page content in detail short: it is
    shown to people and exported to CSV.
    """
    assert severity in SEVERITIES, severity
    return {"id": id, "severity": severity, "title": title, "detail": detail, "fix": fix if severity != "pass" else ""}


def clip(text, limit=120):
    """Shorten page content quoted in a detail."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
