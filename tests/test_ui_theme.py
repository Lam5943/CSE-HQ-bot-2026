import discord

from cse_hq_bot.ui_theme import (
    BRAND_COLOR,
    list_entry,
    metric_value,
    set_surface_footer,
    surface_embed,
)


def test_surface_embed_uses_shared_cse_hq_shell():
    embed = surface_embed(
        "tasks",
        description="Queue",
    )

    assert embed.title == "✅ CSE-HQ • Tasks"
    assert embed.description == "Queue"
    assert embed.color == BRAND_COLOR


def test_surface_embed_allows_semantic_status_color():
    embed = surface_embed(
        "health",
        color=discord.Color.red(),
    )

    assert embed.title == "🩺 CSE-HQ • System Health"
    assert embed.color == discord.Color.red()


def test_surface_footer_and_readable_helpers_are_consistent():
    embed = surface_embed("meetings")
    set_surface_footer(embed, "meetings", detail="Page 2/3")

    assert embed.footer.text == "CSE-HQ • Meetings • Page 2/3"
    assert metric_value(7, "Open") == "**7**\nOpen"
    assert list_entry("TASK-007 · Fix auth", "Blocked · P5") == (
        "**TASK-007 · Fix auth**\n> Blocked · P5"
    )
