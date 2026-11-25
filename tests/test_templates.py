import httpx

from fmbotg.domain import Target
from fmbotg.handlers.top import rank_change
from fmbotg.lastfm import LastFmClient, Scrobble
from fmbotg.templates import TemplateContext, render, unknown_variables


def _context(loved: bool = False, album: str = "Kid A") -> TemplateContext:
    def lastfm_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"track": {"userplaycount": "12"}})

    current = Scrobble("Idioteque", "Radiohead", album, "https://x/<t>", None, True, None, loved)
    lastfm = LastFmClient("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(lastfm_api)))
    return TemplateContext(Target("alice", None, True), "Alice <3", current, None, 1234, lastfm, db=None)


async def test_template_escapes_text_and_values_and_formats_options():
    text = await render("$$title: {{track.name}} <b>by</b> {{track.artist}}\n"
                        "{{user.display-name}}: {{track.plays}} plays of {{lastfm.total-scrobbles}}\n"
                        "$$footer: {{track.url}}\n$$color: #ff0000", _context())
    assert text.split("\n") == [
        "<b>Idioteque &lt;b&gt;by&lt;/b&gt; Radiohead</b>",
        "Alice &lt;3: 12 plays of 1,234",
        "<i>https://x/&lt;t&gt;</i>",
    ]


async def test_lines_whose_variables_are_all_empty_are_dropped():
    text = await render("{{track.name}}\nLoved: {{track.loved}}\n{{previous-track.name}} {{track.album}}\nplain line",
                        _context(loved=False, album=""))
    assert text == "Idioteque\nplain line"


def test_unknown_variables_are_reported():
    assert unknown_variables("{{track.name}} {{track.bpm}} {{nope}}") == ["nope", "track.bpm"]


def test_billboard_rank_markers():
    assert [rank_change(1, None), rank_change(1, 0), rank_change(2, 5), rank_change(4, 1), rank_change(3, 3)] == \
        ["", " 🆕", " ▲3", " ▼3", " ＝"]
