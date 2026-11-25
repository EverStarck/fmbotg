"""Plain rows passed between the play index and the rest of the bot."""

from dataclasses import dataclass

from ..names import name_key


@dataclass(frozen=True)
class PlayRow:
    played_at: int
    artist: str
    album: str | None
    track: str

    def as_insert(self, user_id: int) -> dict:
        return {
            "user_id": user_id,
            "played_at": self.played_at,
            "artist": self.artist[:512],
            "album": (self.album or None) and self.album[:512],
            "track": self.track[:512],
            "artist_key": name_key(self.artist),
            "album_key": name_key(self.album) or None,
            "track_key": name_key(self.track),
        }


@dataclass(frozen=True)
class AggregateRow:
    name: str
    artist: str | None
    playcount: int
