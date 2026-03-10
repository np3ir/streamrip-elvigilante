import os
import re
import unicodedata
from pathvalidate import sanitize_filename, sanitize_filepath


def truncate_str(text: str) -> str:
    """
    Safely truncate a string to 240 bytes to stay within Windows path limits.
    """
    str_bytes = text.encode("utf-8")
    if len(str_bytes) > 240:
        str_bytes = str_bytes[:240]
    return str_bytes.decode("utf-8", errors="ignore")


def remove_zalgo(text: str) -> str:
    """
    Remove *excessive* combining marks (Zalgo) WITHOUT stripping normal accents.
    """
    s = unicodedata.normalize("NFC", str(text))

    out = []
    combining_run = 0
    last_base_is_letter = False

    for ch in s:
        cat = unicodedata.category(ch)

        if cat == "Mn":
            combining_run += 1
            if last_base_is_letter and combining_run == 1:
                out.append(ch)
            continue

        combining_run = 0
        last_base_is_letter = cat.startswith("L")
        out.append(ch)

    return unicodedata.normalize("NFC", "".join(out))


def get_alpha_bucket(name: str) -> str:
    if not name:
        return "#"
    s = str(name).strip()
    if not s:
        return "#"
    ch = s[0].upper()
    decomposed = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn").upper()
    return base if ("A" <= base <= "Z") else "#"


def _normalize_initial_folder_component(component: str) -> str:
    if not component: return component
    comp = str(component).strip()
    if not comp or comp == "#": return "#"
    if len(comp) == 1: return get_alpha_bucket(comp)
    return component


def clean_filename(fn: str, restrict: bool = False) -> str:
    """
    Clean a track filename for safe filesystem usage.
    """
    fn = remove_zalgo(fn)
    fn = unicodedata.normalize("NFC", fn)

    replacements = {
        ":": "：", "/": "／", "\\": "＼", "<": "＜", ">": "＞",
        '"': "＂", "|": "｜", "?": "？", "*": "＊",
    }
    for char, replacement in replacements.items():
        fn = fn.replace(char, replacement)

    path = str(sanitize_filename(fn))
    path = truncate_str(path)
    path = re.sub(r"\s+", " ", path).strip()
    path = path.rstrip(". ")

    return path or "Unknown_Name"


def clean_filepath(fn: str, restrict: bool = False) -> str:
    """
    Clean a full directory path for safe filesystem usage.
    """
    fn = remove_zalgo(fn)
    fn = unicodedata.normalize("NFC", fn)

    replacements = {
        ":": "：", "<": "＜", ">": "＞", '"': "＂",
        "|": "｜", "?": "？", "*": "＊",
    }
    for char, replacement in replacements.items():
        fn = fn.replace(char, replacement)

    path = str(sanitize_filepath(fn))
    path = re.sub(r"\s+", " ", path).strip()
    path = path.rstrip(". ")

    parts = re.split(r"[\\/]+", path)
    if parts:
        parts[0] = _normalize_initial_folder_component(parts[0])

    path = os.sep.join(parts)
    return path


def truncate_filepath_to_max(path: str, max_length: int = 240) -> str:
    """
    Truncate *path* so that its UTF-8 byte length does not exceed *max_length*.
    Uses byte counting (like tiddl) instead of character counting, which is
    correct for Unicode-heavy filenames on Windows / Linux.
    Default: 240 bytes — safe margin below the 255-byte NTFS component limit.
    """
    if len(path.encode("utf-8")) <= max_length:
        return path

    dir_path, filename = os.path.split(path)
    base, ext = os.path.splitext(filename)
    dir_path = dir_path.rstrip(os.sep)

    dir_bytes = len(dir_path.encode("utf-8"))
    ext_bytes = len(ext.encode("utf-8"))
    sep_byte = 1  # os.sep is always one byte on supported platforms
    allowed_base_bytes = max_length - dir_bytes - sep_byte - ext_bytes

    if allowed_base_bytes <= 0:
        # Directory alone is too long — best-effort character truncation
        return path.encode("utf-8")[:max_length].decode("utf-8", errors="ignore")

    # Truncate base to allowed bytes, then decode safely
    base_truncated = base.encode("utf-8")[:allowed_base_bytes].decode("utf-8", errors="ignore")
    return os.path.join(dir_path, base_truncated + ext)


# ---------------------------------------------------------------------------
# clean_track_title — port of tiddl's comprehensive implementation.
# Handles multilingual feat. keywords, parentheses AND dash-separated forms,
# partial artist lists, and word-boundary matching.
# ---------------------------------------------------------------------------

_FEAT_KEYWORDS = (
    # English / Universal
    r"f(?:ea)?t(?:\.|uring)?|with|w/|starring|"
    # Spanish
    r"con|junto a|"
    # German / French
    r"mit|avec|et"
)

_RE_ANTI_FEAT = re.compile(
    # Option 1: (feat. X) / [with X] / {starring X} — requires closing bracket
    r"(?:\s*(?:[\(\[\{])\s*"
    r"(?:" + _FEAT_KEYWORDS + r")"
    r"\s+([^)\}\]]+?)\s*(?:[\)\]\}]))"
    r"|"
    # Option 2: " - feat. X" or " – with X" — consumes rest of string
    r"(?:\s+[-\u2013]\s+"
    r"(?:" + _FEAT_KEYWORDS + r")"
    r"\s+(.*))",
    flags=re.IGNORECASE,
)


def clean_track_title(track_title: str, artist_name: str) -> str:
    """
    Remove (feat. X) / (with X) / " - feat. X" from a track title when X is
    already captured in the artist string.  Handles partial lists: if a feat.
    block contains both known and unknown artists only the unknown ones are kept.
    Multilingual keywords: English, Spanish, German, French.
    """
    # Build a normalised list of artist names to compare against
    meta_artists = [a.strip().lower() for a in artist_name.split(",") if a.strip()]

    def is_known(name: str) -> bool:
        n = name.strip().lower()
        if not n:
            return True
        if n in meta_artists:
            return True
        # Word-boundary match: "Lil" matches inside "Lil Wayne" but not "Lily Allen"
        pattern = rf"\b{re.escape(n)}\b"
        return any(re.search(pattern, ma) for ma in meta_artists)

    def replacement(match: re.Match) -> str:
        full_match = match.group(0)
        content = match.group(1) or match.group(2)
        if not content:
            return full_match
        # Split by common list separators
        parts = re.split(
            r"\s*(?:,|&|\+| and | y | et | und | con | with )\s*",
            content,
            flags=re.IGNORECASE,
        )
        unknown = [p.strip() for p in parts if p.strip() and not is_known(p)]
        if not unknown:
            return ""                              # all artists known → remove block
        if len(unknown) == len(parts):
            return full_match                      # none known → keep block intact
        # Partial match → reconstruct with only unknown artists
        return full_match.replace(content, ", ".join(unknown))

    return _RE_ANTI_FEAT.sub(replacement, track_title).strip()