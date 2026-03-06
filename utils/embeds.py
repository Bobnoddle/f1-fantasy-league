from __future__ import annotations

import discord

# Colour palette (Tier 1 Vanguard - Minimalist)
_RED = 0x8A2B2B
_GREEN = 0x2B4A33
_BLUE = 0x1E293B

def error_embed(message: str) -> discord.Embed:
    """Return an error embed with a muted, high-contrast palette."""
    return discord.Embed(description=message, colour=_RED)

def success_embed(message: str) -> discord.Embed:
    """Return a success embed with a deep forest tone."""
    return discord.Embed(description=message, colour=_GREEN)

def info_embed(title: str, description: str) -> discord.Embed:
    """Return an informational embed anchored in deep navy tones."""
    return discord.Embed(title=title.upper(), description=description, colour=_BLUE)
