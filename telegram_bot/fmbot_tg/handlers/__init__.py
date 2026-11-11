from aiogram import Router

from . import chart, info, play, top, user, whoknows


def all_routers() -> list[Router]:
    return [user.router, play.router, top.router, info.router, whoknows.router, chart.router]
