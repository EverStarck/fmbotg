"""Registering families of commands that differ only by artist/album/track."""

import inspect
from collections.abc import Awaitable, Callable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


def add_kind_commands(router: Router, run: Callable[..., Awaitable[None]], commands: dict[str, tuple[str, ...]]) -> None:
    """Register `run(message, kind=..., <services>)` once per kind, e.g.
    {"artist": ("whoknows", "wk"), "album": ("whoknowsalbum", "wka")}.

    `run` names the services it needs (command, db, stats, ...) as keyword parameters, and gets
    exactly those from aiogram, so adding a dependency means changing one signature, not one per kind.
    """
    wanted = [name for name in inspect.signature(run).parameters if name not in ("message", "kind")]

    def bind(kind: str):
        async def handler(message: Message, **data) -> None:
            await run(message, kind=kind, **{name: data[name] for name in wanted})
        handler.__name__ = f"{run.__name__.lstrip('_')}_{kind}"
        return handler

    for kind, names in commands.items():
        router.message(Command(*names))(bind(kind))
