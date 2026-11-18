"""Read a short bullet heading without rewriting its original text."""
import re


def split_bullet(text):
    """Return (heading, body) for colon or legacy bracket style, else None."""
    if not isinstance(text, str):
        return None
    for pattern in (r"【([^】\r\n]+)】\s*(\S[\s\S]*)",
                    r"([^:：\r\n【】]+)[:：]\s*(\S[\s\S]*)"):
        match = re.fullmatch(pattern, text)
        if match and match[1].strip():
            return match[1].strip(), match[2]
    return None
