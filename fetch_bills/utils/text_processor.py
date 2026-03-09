"""
text_processor.py — Clean and normalize bill text for TF-IDF vectorization.
"""

import re
import hashlib

# ---------------------------------------------------------------------------
# Stop words (legislative + standard English)
# ---------------------------------------------------------------------------

STOP_WORDS = set("""
a about above after again against all also am an and any are aren't as at
be because been before being below between both but by can can't cannot
could couldn't did didn't do does doesn't doing don't down during each few
for from further get got had hadn't has hasn't have haven't having he he'd
he'll he's her here here's hers herself him himself his how how's i i'd
i'll i'm i've if in into is isn't it it's its itself let's me more most
mustn't my myself no nor not of off on once only or other ought our ours
ourselves out over own same shan't she she'd she'll she's should shouldn't
so some such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've this those through to too
under until up very was wasn't we we'd we'll we're we've were weren't what
what's when when's where where's which while who who's whom why why's will
with won't would wouldn't you you'd you'll you're you've your yours yourself
yourselves
hereby herein hereof hereto hereunder thereof therein thereto thereunder
whereas wherefore wherein whereby whereupon notwithstanding provided however
shall may must will upon pursuant following including without limitation
section subsection paragraph clause subclause title chapter article part
act acts enacted enact amendment amended amend effective date thereof
united states congress senate house representatives public law chapter
title subtitle division subdivision sec such term means including
""".split())

# Legislative boilerplate patterns to strip
BOILERPLATE_PATTERNS = [
    r"be it enacted by the senate and house of representatives.*?following",
    r"in general\.?—",
    r"definitions\.?—",
    r"short title\.?—",
    r"table of contents",
    r"sec(?:tion)?\.?\s*\d+[\.\-]?\s*",
    r"\(a\)\s*|\(b\)\s*|\(c\)\s*|\(d\)\s*|\(e\)\s*|\(f\)\s*|\(g\)\s*",
    r"\(\d+\)\s*",
    r"^(?:an act|a bill)\s+to\s+",
    r"this act (?:may be cited|shall be known) as",
    r"amending.*?u\.?s\.?c\.?",
    r"striking.*?inserting",
    r"appropriat(?:ed|ion|ions) for fiscal year \d{4}",
    r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand))?",
    r"public law \d+-\d+",
    r"u\.s\.c\.\s*\d+",
    r"\d+ stat\. \d+",
]

BOILERPLATE_RE = re.compile(
    "|".join(BOILERPLATE_PATTERNS),
    flags=re.IGNORECASE | re.MULTILINE
)


def clean_bill_text(raw_text: str) -> str:
    """
    Full preprocessing pipeline:
    1. Lowercase
    2. Strip legislative boilerplate
    3. Remove punctuation / numbers
    4. Normalize whitespace
    5. Remove stop words
    6. Return cleaned string
    """
    if not raw_text:
        return ""

    text = raw_text.lower()

    # Strip boilerplate
    text = BOILERPLATE_RE.sub(" ", text)

    # Remove punctuation and digits (keep letters and spaces)
    text = re.sub(r"[^a-z\s]", " ", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Remove stop words and short tokens
    tokens = [w for w in text.split() if w not in STOP_WORDS and len(w) > 2]

    # Simple bigrams for common legislative pairs
    bigrams = []
    for i in range(len(tokens) - 1):
        pair = f"{tokens[i]}_{tokens[i+1]}"
        bigrams.append(pair)

    return " ".join(tokens + bigrams)


def text_hash(raw_text: str) -> str:
    """SHA-256 hash of raw text for change detection."""
    return hashlib.sha256(raw_text.encode("utf-8", errors="replace")).hexdigest()


def extract_keywords(cleaned_text: str, top_n: int = 20) -> list:
    """
    Simple keyword extraction by term frequency (no IDF needed here).
    Returns top_n most frequent meaningful terms.
    """
    if not cleaned_text:
        return []
    freq = {}
    for token in cleaned_text.split():
        if "_" not in token:  # skip bigrams for keyword list
            freq[token] = freq.get(token, 0) + 1
    sorted_terms = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [t for t, _ in sorted_terms[:top_n]]