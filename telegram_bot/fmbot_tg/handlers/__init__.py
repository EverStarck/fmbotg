from aiogram import Router

from . import chart, crowns, friends, info, insights, play, plays, recap, settings, top, user, whoknows


def all_routers() -> list[Router]:
    return [user.router, settings.router, play.router, plays.router, insights.router, top.router, info.router,
            whoknows.router, friends.router, crowns.router, chart.router, recap.router]
