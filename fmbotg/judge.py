"""/judge: an AI compliments or roasts your music taste (port of OpenAiService's judge prompts).

Runs through OpenRouter's OpenAI-compatible chat API, so any model it hosts works (Claude Opus 5.5
by default). Each call costs money, so it only runs when OPENROUTER_API_KEY is set and each person
gets a few per day.
"""

import logging
import time

import httpx

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-opus-5.5"

SYSTEM = (
    "You write short, funny takes on someone's music taste for a Telegram music bot, based on their "
    "Last.fm top artists and tracks. Mention specific artists and tracks from the list. Keep it to one "
    "paragraph of at most 120 words, in plain text without markdown, headings or lists. Be playful, not "
    "cruel: tease the music, never the person's looks, identity or group membership."
)
MODES = {
    "compliment": "Compliment this person's music taste: find what's genuinely great or interesting about it.",
    "roast": "Roast this person's music taste: tease the clichés, guilty pleasures and contradictions you see.",
}


class JudgeUnavailable(Exception):
    pass


class Judge:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None, model: str | None, daily_limit: int):
        self._http = http
        self._api_key = api_key
        self.model = model or DEFAULT_MODEL
        self._daily_limit = daily_limit
        self._uses: dict[int, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    @property
    def model_name(self) -> str:
        """'anthropic/claude-opus-5.5' -> 'claude-opus-5.5', for the footer."""
        return self.model.split("/", 1)[-1]

    def remaining(self, telegram_id: int) -> int:
        recent = [t for t in self._uses.get(telegram_id, []) if t > time.time() - 86400]
        if recent:
            self._uses[telegram_id] = recent
        else:
            self._uses.pop(telegram_id, None)  # don't keep an entry per person forever
        return max(0, self._daily_limit - len(recent))

    async def run(self, telegram_id: int, mode: str, period_label: str, artists: list[tuple[str, int]],
                  tracks: list[tuple[str, str, int]]) -> str:
        if not self._api_key:
            raise JudgeUnavailable("Judging isn't set up on this bot (it needs an OpenRouter API key).")
        if self.remaining(telegram_id) <= 0:
            raise JudgeUnavailable(f"You've used your {self._daily_limit} judgements for today. Try again tomorrow.")
        used_at = time.time()
        self._uses.setdefault(telegram_id, []).append(used_at)
        try:
            return await self._ask(mode, period_label, artists, tracks)
        except JudgeUnavailable:
            self._uses[telegram_id].remove(used_at)  # a failed call doesn't use up a judgement
            raise

    async def _ask(self, mode: str, period_label: str, artists: list[tuple[str, int]],
                   tracks: list[tuple[str, str, int]]) -> str:

        music = [f"Period: {period_label}", "Top artists (plays):"]
        music += [f"- {name} ({plays})" for name, plays in artists]
        music += ["Top tracks (plays):"]
        music += [f"- {track} by {artist} ({plays})" for track, artist, plays in tracks]
        try:
            response = await self._http.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "X-Title": "fmbotg"},
                json={
                    "model": self.model,
                    "max_tokens": 2000,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": MODES[mode] + "\n\n" + "\n".join(music)},
                    ],
                },
                timeout=90,
            )
        except httpx.HTTPError:
            raise JudgeUnavailable("Couldn't reach OpenRouter. Try again later.") from None

        if response.status_code == 429:
            raise JudgeUnavailable("The AI is busy right now. Try again in a minute.")
        if response.status_code == 402:
            raise JudgeUnavailable("The bot's OpenRouter account is out of credits.")
        if response.status_code != 200:
            log.warning("Judge request failed: %s %s", response.status_code, response.text[:300])
            raise JudgeUnavailable("The AI couldn't judge that right now. Try again later.")

        data = response.json()
        if "error" in data:  # OpenRouter can report upstream errors with a 200
            log.warning("Judge request failed: %s", data["error"])
            raise JudgeUnavailable("The AI couldn't judge that right now. Try again later.")
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "content_filter":
            raise JudgeUnavailable("The AI declined to judge this one.")
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            raise JudgeUnavailable("The AI didn't come up with anything. Try again.")
        return text
