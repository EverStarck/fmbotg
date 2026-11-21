from aiogram import Router

from . import chart, info, play, plays, settings, top, user, whoknows


def all_routers() -> list[Router]:
    return [user.router, settings.router, play.router, plays.router, top.router, info.router, whoknows.router,
            chart.router]
