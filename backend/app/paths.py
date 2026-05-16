import re


UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')


def safe_path_name(value: str, fallback: str = "playlist") -> str:
    cleaned = UNSAFE_PATH_CHARS.sub("_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return fallback
    return cleaned[:120].rstrip(" .") or fallback
