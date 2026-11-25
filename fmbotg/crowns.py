"""Crowns: the person with the most plays of an artist in a group holds its crown
(port of CrownService.GetAndUpdateCrownForArtist).

Crowns change hands when /whoknows runs for an artist, so they only move when someone looks.
"""

from dataclasses import dataclass

from .db import DEFAULT_CROWN_THRESHOLD, Database, User
from .formatting import esc, plural


@dataclass(frozen=True)
class CrownDecision:
    action: str  # "none", "keep", "claim" or "steal"
    user_id: int | None = None
    plays: int | None = None


def decide(holder_id: int | None, counts: dict[int, int | None], threshold: int) -> CrownDecision:
    """What happens to an artist's crown given everyone's current playcount (None = couldn't fetch).

    The holder keeps it on a tie, and a crown is never taken from someone whose count we don't know.
    A holder who isn't in `counts` (left the chat or disconnected Last.fm) loses it to the top listener.
    """
    known = [(user_id, plays) for user_id, plays in counts.items() if plays]
    top_id, top_plays = max(known, key=lambda pair: pair[1], default=(None, 0))

    if holder_id is None:
        if top_id is not None and top_plays >= threshold:
            return CrownDecision("claim", top_id, top_plays)
        return CrownDecision("none")

    if holder_id in counts:
        holder_plays = counts[holder_id]
        if holder_plays is None:
            return CrownDecision("keep", holder_id, None)
        if top_id != holder_id and top_plays > holder_plays and top_plays >= threshold:
            return CrownDecision("steal", top_id, top_plays)
        return CrownDecision("keep", holder_id, holder_plays)

    if top_id is not None and top_plays >= threshold:
        return CrownDecision("steal", top_id, top_plays)
    return CrownDecision("keep", holder_id, None)


def display_name(user: User | None) -> str:
    if user is None:
        return "someone who left"
    return user.display_name or user.lastfm_username or "someone"


@dataclass(frozen=True)
class CrownChange:
    """What a /whoknows run did to an artist's crown."""

    holder_id: int | None = None  # who holds it now
    action: str = "none"  # "none", "keep", "claim" or "steal"
    winner: User | None = None  # for claim/steal
    previous: User | None = None  # for steal
    previous_plays: int = 0  # for steal

    def announcement(self) -> str | None:
        if self.action == "claim":
            return f"👑 Crown claimed by <b>{esc(display_name(self.winner))}</b>!"
        if self.action == "steal":
            return (f"👑 Crown stolen by <b>{esc(display_name(self.winner))}</b> from "
                    f"{esc(display_name(self.previous))} ({plural(self.previous_plays, 'play')})!")
        return None


async def update_crown(db: Database, chat_id: int, artist: str, results: list[tuple[User, int | None]]) -> CrownChange:
    """Apply a /whoknows result to the artist's crown."""
    settings = await db.chat_settings(chat_id)
    if settings.crowns_disabled:
        return CrownChange()
    threshold = settings.crown_threshold or DEFAULT_CROWN_THRESHOLD
    current = await db.active_crown(chat_id, artist)
    users = {user.id: user for user, _ in results}
    decision = decide(current.user_id if current else None, {user.id: plays for user, plays in results}, threshold)

    if decision.action == "none":
        return CrownChange()
    if decision.action == "keep":
        if decision.plays is not None and decision.plays != current.playcount:
            await db.update_crown_playcount(current.id, decision.plays)
        return CrownChange(current.user_id, "keep")

    await db.set_crown(chat_id, artist, decision.user_id, decision.plays)
    winner = users.get(decision.user_id)
    if decision.action == "claim":
        return CrownChange(decision.user_id, "claim", winner)
    previous = users.get(current.user_id) or await db.get_user_by_id(current.user_id)
    return CrownChange(decision.user_id, "steal", winner, previous, current.playcount)
