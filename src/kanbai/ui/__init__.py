_AVATAR_PALETTE = [
    "#1f6feb", "#388bfd", "#58a6ff",  # blues
    "#6e40c9", "#8957e5", "#a371f7",  # purples
    "#1a7f37", "#2ea043", "#3fb950",  # greens
    "#9e6a03", "#d29922", "#e3b341",  # yellows
    "#b94040", "#cf4444", "#f85149",  # reds
    "#0d7a7a", "#1b9090", "#39b0b0",  # teals
]


def avatar_color(name: str) -> str:
    """Return a stable color for a given name based on first + second letter ordinals."""
    if not name:
        return _AVATAR_PALETTE[0]
    first = ord(name[0].lower())
    second = ord(name[1].lower()) if len(name) > 1 else 0
    idx = (first + second) % len(_AVATAR_PALETTE)
    return _AVATAR_PALETTE[idx]
