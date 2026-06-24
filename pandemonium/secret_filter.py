"""Content-blind secret redaction for code handed back to the model.

Pattern-based (no semantic understanding), tuned for PRECISION over recall: it must never
mangle ordinary source, so it fires only on high-signal secret shapes — PEM private-key
blocks, well-known provider token formats, and quoted values assigned to obviously
secret-named keys. A miss is acceptable (the caller still reports the redaction count); a
false positive that corrupts code is not.

Scope: this guards the OUTPUT path (what `repo_get` returns to the agent/LLM). It is NOT an
index-time guarantee — secrets already committed to a repo are the repo's problem; this just
stops them being relayed verbatim into a model context.
"""

from __future__ import annotations

import re
from typing import Tuple

REDACTED = "***REDACTED***"

# Whole-match redactions: PEM private-key blocks + recognizable provider token formats.
_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
               re.DOTALL),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),            # AWS access key id
    re.compile(r"\bgh[posru]_[0-9A-Za-z]{36,}\b"),  # GitHub personal/OAuth/server tokens
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),  # Slack tokens
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),         # OpenAI-style secret keys
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),      # Google API key
]

# Quoted value assigned to a secret-named key: redact ONLY the value, keep the key + quotes
# so the code shape (and the fact that a secret lived here) stays legible.
_ASSIGN = re.compile(
    r"""(?ix)
    ( (?: api[_-]?key | secret | token | password | passwd | access[_-]?key
        | private[_-]?key | client[_-]?secret | auth[_-]?token ) \s* [:=] \s* )
    (['"]) ([^'"]{8,}) (\2)
    """)


def redact_secrets(text: str) -> Tuple[str, int]:
    """Return (redacted_text, n_redactions). n_redactions == 0 means the text is unchanged."""
    if not text:
        return text, 0
    total = 0
    out = text
    for pat in _PATTERNS:
        out, k = pat.subn(REDACTED, out)
        total += k
    out, k = _ASSIGN.subn(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(4)}", out)
    total += k
    return out, total
