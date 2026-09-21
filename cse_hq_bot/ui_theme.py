from dataclasses import dataclass

import discord


BRAND_COLOR = discord.Color.from_rgb(139, 92, 246)


@dataclass(frozen=True)
class SurfaceTheme:
    icon: str
    label: str
    color: discord.Color = BRAND_COLOR


SURFACES = {
    "project": SurfaceTheme("🛰️", "Project"),
    "tasks": SurfaceTheme("✅", "Tasks"),
    "bugs": SurfaceTheme("🐞", "Bugs"),
    "meetings": SurfaceTheme("🗓️", "Meetings"),
    "decisions": SurfaceTheme("🧭", "Decisions"),
    "standup": SurfaceTheme("☀️", "Standup"),
    "ai": SurfaceTheme("🤖", "AI Workspace"),
    "github": SurfaceTheme("🐙", "GitHub"),
    "health": SurfaceTheme("🩺", "System Health"),
}


def surface_embed(
    surface: str,
    *,
    title: str | None = None,
    description: str | None = None,
    color: discord.Color | None = None,
    timestamp=None,
    url: str | None = None,
) -> discord.Embed:
    theme = SURFACES[surface]
    label = title or theme.label
    return discord.Embed(
        title=f"{theme.icon} CSE-HQ • {label}",
        description=description,
        color=color or theme.color,
        timestamp=timestamp,
        url=url,
    )


def set_surface_footer(
    embed: discord.Embed,
    surface: str,
    *,
    detail: str | None = None,
) -> None:
    theme = SURFACES[surface]
    text = f"CSE-HQ • {theme.label}"
    if detail:
        text += f" • {detail}"
    embed.set_footer(text=text)


def metric_value(value: object, label: str) -> str:
    return f"**{value}**\n{label}"


def list_entry(title: str, metadata: str) -> str:
    return f"**{title}**\n> {metadata}"
