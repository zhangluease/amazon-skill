"""Literal model identifiers shared by extraction and Listing validation."""
import re
import unicodedata


MODEL_DASHES = "-‐‑‒–—−－"
MODEL_BOUNDARY = r"A-Za-z0-9Ａ-Ｚａ-ｚ０-９_.+＿．＋\-‐‑‒–—−－"


def normalize_model(value):
    return unicodedata.normalize("NFKC", value).translate(str.maketrans({c: "-" for c in MODEL_DASHES})).upper()


def model_pattern(models):
    """Match intact model IDs, accepting width/case/dash variants, never prefixes.

    Match original text so editorial length checks do not normalize or rewrite prose.
    """
    alternatives = []
    for model in sorted(set(models), key=len, reverse=True):
        characters = []
        for char in normalize_model(model):
            if char == "-":
                characters.append("[" + re.escape(MODEL_DASHES) + "]")
            elif "!" <= char <= "~":
                characters.append("[" + re.escape(char + chr(ord(char) + 0xFEE0)) + "]")
            else:
                characters.append(re.escape(char))
        alternatives.append("".join(characters))
    return re.compile(r"(?<![" + MODEL_BOUNDARY + r"])(?:" + "|".join(alternatives)
                      + r")(?![" + MODEL_BOUNDARY + r"])", re.I) if alternatives else None


def valid_model_list(value):
    """Identifiers may be numeric, named, spaced or non-Latin; roles supply meaning."""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(model, str) and model == model.strip() and bool(model)
                    and any(char.isalnum() for char in model)
                    and not re.search(r"[\n\r\t*?~〜～,，、;；]|\.\.\.|…", model) for model in value))
