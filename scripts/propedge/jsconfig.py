"""Read measured constants out of src/app.jsx rather than keeping a second copy.

Several numbers in this repo were measured once and live in the frontend because
that is where they are used: the residual scale per game, stat and window, the
exponent that stretches it to an unobserved window, the confidence threshold the
Parlays tab gates on. Anything here that needs them reads them from the file, so
what Python computes is what the app will do and not an approximation of it.

The alternative -- copying the numbers -- fails silently and in the worst
direction: the copy goes stale, both sides keep producing plausible figures, and
the disagreement only shows up as money.

This is a regex over JavaScript, which fails LOUDLY when a constant is renamed
and could fail quietly if a literal were reformatted, so both cases are tested
(tests/test_propedge_jsconfig.py). It was factored out of
scripts/dev/decile_test.py, which had the only copy.
"""
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(REPO_ROOT, "src", "app.jsx")


def source(path=None):
    with open(path or APP, encoding="utf-8") as f:
        return f.read()


def number(name, text=None):
    """`const NAME = 0.63;` -> 0.63. Raises if it is not there any more."""
    text = text if text is not None else source()
    found = re.search(rf"const {re.escape(name)}\s*=\s*(-?\d+(?:\.\d+)?)", text)
    if not found:
        raise LookupError(f"{name} is not in src/app.jsx as a plain number — "
                          "has it been renamed or made an expression?")
    return float(found.group(1))


def obj(name, text=None):
    """A nested object literal of numbers, as JSON-compatible dicts.

    Handles the two things that make a JS literal invalid JSON here: comments
    and trailing commas, and BARE NUMERIC KEYS. RESIDUAL_SCALE keys its windows
    by map count -- `kills: { 1: 4.66, 2: 7.70 }` -- and the first version of
    this quoted only identifier-like keys, so `1:` reached json.loads unquoted
    and the whole thing died. Keys come back as strings either way, which is why
    callers look windows up by str(maps).
    """
    text = text if text is not None else source()
    start = text.find(f"const {name} = {{")
    if start < 0:
        raise LookupError(f"{name} is not in src/app.jsx as an object literal")
    brace = text.index("{", start)
    depth, end = 0, brace
    for end in range(brace, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                break
    body = text[brace:end + 1]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    body = re.sub(r"([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*|\d+)\s*:", r'\1"\2":', body)
    try:
        return json.loads(body)
    except json.JSONDecodeError as problem:
        raise LookupError(f"{name} in src/app.jsx is no longer a plain literal of "
                          f"numbers: {problem}") from problem
