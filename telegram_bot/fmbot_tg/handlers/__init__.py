from aiogram import Router

from . import (
    autoposts, chart, crowns, friends, games, genres, group, info, insights, judge, music, owner, play, plays, recap,
    settings, shortcuts, top, user, whoknows,
)


def all_routers() -> list[Router]:
    # games last: its guess handler takes plain text messages in chats with a running game
    return [user.router, settings.router, play.router, plays.router, insights.router, top.router, info.router,
            whoknows.router, friends.router, crowns.router, genres.router, group.router, autoposts.router,
            shortcuts.router, music.router, judge.router, owner.router, chart.router, recap.router, games.router]
