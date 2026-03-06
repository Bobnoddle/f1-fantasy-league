from __future__ import annotations

import discord

# Colour palette
_RED = 0xE74C3C
_GREEN = 0x2ECC71
_BLUE = 0x3498DB


def error_embed(message: str) -> discord.Embed:
    """Return a red embed suitable for error responses."""
    return discord.Embed(description=message, colour=_RED)


def success_embed(message: str) -> discord.Embed:
    """Return a green embed suitable for success confirmations."""
    return discord.Embed(description=message, colour=_GREEN)


def info_embed(title: str, description: str) -> discord.Embed:
    """Return a blue embed suitable for informational responses."""
    return discord.Embed(title=title, description=description, colour=_BLUE)
