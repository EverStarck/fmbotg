"""/start, /help, /help <command> and /faq (port of HelpService and FaqService)."""

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..commands import COMMANDS
from ..domain import UserFacingError
from ..formatting import esc
from ..toggles import CommandToggles

router = Router(name="help")

HELP_TEXT = """<b>FMBot for Telegram</b> — Last.fm stats in your chats.

<b>Account</b>
/login — connect your Last.fm account (private chat)
/logout — disconnect it
/profile — your Last.fm profile stats
/update — fetch new scrobbles (<code>/update full</code> re-imports everything)
/settings — time zone, /fm style and your own /fm template
/shortcut — your own commands, e.g. /mine for <code>/fm lfm:you</code>
/privacy — <code>global</code> shows your name in global who knows, <code>chat</code> (default) hides it

<b>Listening</b>
/fm — what you're playing now
/recent — your latest scrobbles
/plays — scrobble count, optionally for a period
/streak — how many times in a row you've played the current artist, album and track (/streaks for saved ones)
/pace — when you'll reach a scrobble goal, e.g. <code>/pace 100k</code>
/milestone — your Nth scrobble, e.g. <code>/milestone 10000</code> or <code>random</code>
/overview — your last few days, day by day
/love /unlove /loved — love tracks on Last.fm
/scrobble — scrobble a track, e.g. <code>/scrobble Radiohead - Idioteque</code>
/year — your year in review, e.g. <code>/year 2023</code>
/discoveries — artists you discovered in a period
/taste — compare your taste with someone (reply or <code>@handle</code>)
/gaps — artists, albums or tracks you came back to after 90+ days
/search — search your listening history

<b>Top lists</b> (add a period: today, yesterday, 3d, weekly, monthly, quarterly, half, yearly, 2y, alltime or a year like 2019)
/topartists /topalbums /toptracks — add <code>bb</code> to compare with the previous period
/recap — a period at a glance, with buttons for top lists, discoveries and a chart
/receipt — your top tracks as a store receipt

<b>Info</b> (defaults to what you're playing)
/artist /album /track — e.g. <code>/album Radiohead - OK Computer</code>
/artistplays /albumplays /trackplays — your plays, optionally for a period
/artisttracks /artistalbums — your top tracks and albums by an artist
/artistpace — when you'll reach a play goal on an artist
/artistoverview — you and one artist: plays, top tracks and albums, plays per year
/albumtracks /cover — an album's track list with your plays, and its cover
/discovery /albumdiscovery /trackdiscovery — when you first and last listened to something

<b>Groups</b>
/whoknows /whoknowsalbum /whoknowstrack — who listens to it the most here
/affinity — who here has the most similar taste to you
/crowns — your crowns here: the artists you have the most plays of (/crown for one artist's history, /crownleaderboard for everyone)
/groupartists /groupalbums /grouptracks — top lists across everyone here with an imported history
/members — who here has connected Last.fm
Admins: /crownthreshold, /togglecrowns, /crownseeder (hand out crowns from imported histories), /killcrown
Admins: /autopost — post group charts every week or month
Admins: /block /unblock — leave someone out of this group's leaderboards; /disable /enable — turn commands off here (/groupsettings shows it all)

<b>Genres and countries</b> (genres are Last.fm tags, countries come from MusicBrainz)
/topgenres /topcountries — your top genres or countries, optionally for a period
/genre /country — your top artists in a genre or from a country, or an artist's genres or country
/whoknowsgenre /whoknowscountry — who here listens to it the most (/fwkg, /fwkc among your friends)

<b>Games</b> (reply to the game message with your guess)
/jumble — guess one of your artists from scrambled letters and hints
/pixelation — guess one of your albums from a pixelated cover (<code>/pixelation artist</code> or /pxa: guess the artist)

<b>Friends</b> (people you follow on Last.fm, plus any you add)
/friendsfm — what your friends are playing
/friendwhoknows /fwka /fwkt — who of your friends listens to it the most
/friends /addfriend /removefriend — see and change your friends

<b>Global</b>
/globalwhoknows /gwka /gwkt — top listeners among everyone using this bot

<b>Charts</b>
/chart — album collage, e.g. <code>/chart 4x4 monthly</code>; add <code>artists</code> for artist photos, <code>notitles</code> to hide titles or <code>skip</code> to leave out ones without an image
/iceberg — your artists from mainstream (tip) to obscure (bottom), by Last.fm listeners

<b>More</b>
/playtime — how long you've listened, optionally for a period (/timeleaderboard for the group)
/lyrics — lyrics of what you're playing, or <code>/lyrics Artist - Track</code>
/links /spotify /youtube /deezer — links to a track
/judge — an AI compliments or roasts your taste (/roast, /compliment)

<b>Help</b>: <code>/help command</code> explains one command; /faq answers common questions.

<b>Other users</b>: reply to their message, or add <code>@handle</code> or <code>lfm:username</code>."""

FAQ = """<b>FAQ</b>

<b>The bot doesn't answer in my group.</b>
Telegram's privacy mode only lets bots see commands. Use <code>/command@botname</code>, or make the bot a group admin. Answers to games are replies to the game message.

<b>Why am I missing from /whoknows?</b>
The bot can't list a group's members, so you show up after connecting with /login (in a private chat) and sending any message in that group.

<b>Some commands say I need an imported history.</b>
After /login the bot imports your whole Last.fm history in the background; big libraries take a few minutes. /update fetches new scrobbles, /update full imports everything again.

<b>Who sees my name in global who knows?</b>
Only groups you're in, unless you choose <code>/privacy global</code>.

<b>How do I look at someone else?</b>
Reply to their message, or add their <code>@handle</code>, <code>lfm:username</code> or a group nickname.

<b>Where do photos, lyrics and track lengths come from?</b>
Deezer (photos, lengths), LRCLIB (lyrics), MusicBrainz (countries, release dates) and Last.fm (everything else)."""


@router.message(CommandStart())
async def start(message: Message) -> None:
    # Telegram messages hold 4096 characters, so the help goes out in two parts
    first, second = HELP_TEXT.split("\n\n<b>Groups</b>", 1)
    await message.answer(first)
    await message.answer("<b>Groups</b>" + second)


@router.message(Command("help"))
async def help_command(message: Message, command: CommandObject, toggles: CommandToggles) -> None:
    if command.args:
        await message.reply(command_help(command.args.split()[0], toggles))
        return
    await start(message)


@router.message(Command("faq"))
async def faq(message: Message) -> None:
    await message.reply(FAQ)


def command_help(name: str, toggles: CommandToggles) -> str:
    """One command's description and aliases (port of HelpService's per-command help)."""
    canonical = toggles.resolve(name)
    if canonical is None:
        raise UserFacingError(f"I don't know <code>/{esc(name.lstrip('/'))}</code>. /help lists everything.")
    aliases = sorted(alias for alias, main in toggles.canonical.items() if main == canonical and alias != canonical)
    description = next((c.description for c in COMMANDS if c.command == canonical), None)
    lines = [f"<b>/{canonical}</b>"]
    if description:
        lines.append(esc(description))
    if aliases:
        lines.append("Also: " + ", ".join(f"/{a}" for a in aliases))
    lines.append("\nMost commands take a period (weekly, monthly, 2023…) and someone else "
                 "(reply, @handle, lfm:name or a nickname). /help lists everything.")
    return "\n".join(lines)
