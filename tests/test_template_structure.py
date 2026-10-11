"""Template structure.

Every test here corresponds to a defect that was only visible in a screenshot.

The tab bar is shared by six templates and rendered from `{% block content %}`
in five of them. In two it was emitted at top level instead, and Jinja puts
that ahead of the base template — so on "My team" the tab bar appeared *above*
the site header. Nothing in the suite read the rendered HTML in document order,
so nothing caught it.
"""

from __future__ import annotations

import pytest

from tests.helpers import close_all, make_league, post, sign_in

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _league_with_team(client) -> str:
    await sign_in(client, "Admin")
    return await make_league(client)


# ── 1. Document order ───────────────────────────────────────────────────────


@pytest.mark.parametrize("page", ["", "/draft"])
async def test_the_tab_bar_is_below_the_site_header(client, page):
    """The tab bar must not render above the header.

    team.html and join.html included _league_tabs.html at top level, outside
    `{% block content %}`. With `{% extends %}` that output is emitted ahead of
    the base template, so the tabs appeared above the header on those pages —
    a defect invisible to every other test here.
    """
    code = await _league_with_team(client)

    body = (await client.get(f"/l/{code}{page}")).text

    header = body.find('class="topbar"')
    tabs = body.find('class="tabs"')

    assert header != -1, "no site header rendered"
    assert tabs != -1, f"no tab bar on /l/{code}{page}"
    assert header < tabs, f"the tab bar renders above the site header on /l/{code}{page}"


async def test_the_join_page_has_its_tabs_in_order(client):
    """join.html also included the tabs at top level. Its route is /join/{code},
    not /l/{code}/join — getting that wrong returns a 404 whose "header" is
    nowhere, which is how this page slipped past the check above."""
    code = await _league_with_team(client)

    body = (await client.get(f"/join/{code}")).text
    assert 'class="tabs"' in body, "no tab bar on the join page"
    assert body.find('class="topbar"') < body.find('class="tabs"'), (
        "the join page's tab bar renders above the site header"
    )


async def test_the_team_page_has_its_tabs_in_order(client):
    """The page the screenshot showed as broken."""
    code = await _league_with_team(client)

    from tests.helpers import add_players

    await post(client, f"/l/{code}/admin/action", {"action": "open-signup"})
    players = await add_players(client, ["Sam"], code)

    body = (await client.get(f"/l/{code}")).text
    team_id = body.split("/team/")[1].split('"')[0]
    body = (await client.get(f"/l/{code}/team/{team_id}")).text

    assert body.find('class="topbar"') < body.find('class="tabs"')

    await close_all(players)


# ── 2. Duplicated navigation ────────────────────────────────────────────────


async def test_the_header_does_not_repeat_the_league_nav(client):
    """The header and the tab bar both carried Standings/My team/Draft/Admin."""
    code = await _league_with_team(client)

    body = (await client.get(f"/l/{code}")).text
    header = body[: body.find("</header>")]
    tabs = body[body.find('class="tabs"') :]

    for label in ("Standings", "Draft"):
        assert header.count(f">{label}<") <= 1, f"{label!r} appears twice in the header alone"
    # The two nav regions must not both contain the same link.
    assert not (">Standings<" in header and ">Standings<" in tabs), (
        "the league nav is duplicated between the header and the tab bar"
    )


async def test_the_tab_bar_still_works_on_its_own(client):
    """Removing the header links must not remove league navigation."""
    code = await _league_with_team(client)

    tabs = (await client.get(f"/l/{code}")).text
    section = tabs[tabs.find('class="tabs"') :]
    assert f'href="/l/{code}/draft"' in section
    assert f'href="/l/{code}"' in section


# ── 3. Discord promises ─────────────────────────────────────────────────────


async def test_no_discord_promise_when_the_league_has_no_webhook(client):
    """With no Discord configured, the draft page promised a Discord message.

    discord_enabled exists as a Jinja global and login.html uses it; these two
    templates did not, so a self-host with no Discord was told its turns would
    be announced somewhere they never go.
    """
    code = await _league_with_team(client)

    body = (await client.get(f"/l/{code}")).text.lower()
    assert "discord" not in body or "post to discord" not in body
    assert "no races scored yet" in body


async def test_the_standing_copy_does_not_claim_results_will_post(client):
    code = await _league_with_team(client)

    body = (await client.get(f"/l/{code}")).text
    assert "Results post automatically" not in body, (
        "results are not posted automatically — no webhook is configured"
    )


async def test_the_standing_copy_announces_posting_when_a_webhook_exists(client):
    """And it must still say so when the league *is* wired to Discord."""
    code = await _league_with_team(client)

    # Wire a webhook up, as the admin panel would.
    app = client._transport.app
    from sqlalchemy import update

    from app.models import League

    async with app.state.db_factory() as db:
        await db.execute(
            update(League)
            .where(League.code == code)
            .values(webhook_id="123456", webhook_token="tok")
        )
        await db.commit()

    body = (await client.get(f"/l/{code}")).text
    assert "Results post to Discord automatically" in body


# ── 4. Layout ───────────────────────────────────────────────────────────────


def test_the_stylesheet_pins_the_footer_to_the_bottom():
    """Without this the footer floats mid-page, leaving hundreds of pixels of
    dead space on short pages. Asserted on the rule because there is no browser
    in the unit suite; the screenshot check is what proves it visually."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "app.css").read_text()
    assert "min-height: 100vh" in css
    assert "flex-direction: column" in css


def test_a_button_inside_a_stack_is_not_stretched():
    """.stack is a column flex container with default align-items: stretch,
    which overrode `.btn`'s shrink-to-fit — "Back to standings" rendered the
    full width of the panel."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "app.css").read_text()

    assert ".stack > .btn" in css and "align-self: flex-start" in css
    # The explicit block modifier must still be able to span the container.
    assert ".stack > .btn--block" in css and "align-self: stretch" in css


def test_the_topbar_wraps_at_phone_width():
    """Five items wider than a phone, in a bar with a fixed height and no wrap:
    they overflowed the bar instead of joining a second row.

    Measured at 390px before the fix: the nav was 84px tall across a 57px bar
    (y=-14 to y=70) and reached x=390 with no gutter, while the league pill's
    text wrapped to two lines inside a one-line bar. Nothing in the suite had a
    viewport narrower than a desktop.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "app.css").read_text()

    rule = re.search(r"@media \(max-width: 780px\) \{\s*\.topbar__inner \{(?P<body>[^}]*)\}", css)
    assert rule, "the topbar has no phone-width rule"
    assert "flex-wrap: wrap" in rule.group("body")
    assert "height: auto" in rule.group("body")


def _luminance(css_color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(css_color[i : i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_muted_body_text_clears_wcag_aa_on_every_surface():
    """Review guessed `--muted` sat "near the AA floor". It does not — but
    nothing stops a future palette edit from dropping it under 4.5:1, and a
    contrast failure is invisible to every other test in this file.

    Checked against all three surfaces muted text actually lands on: panels,
    the raised panel-2 headers, and the page background.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "app.css").read_text()
    var = dict(re.findall(r"^\s*(--[\w-]+):\s*(#[0-9a-fA-F]{6});", css, re.M))

    for surface in ("--panel", "--panel-2", "--bg"):
        ratio = _contrast(var["--muted"], var[surface])
        assert ratio >= 4.5, f"muted text on {surface} is {ratio:.2f}:1, under AA"


# ── 5. Draft hub ─────────────────────────────────────────────────────────────


def _chunk(body: str, title: str, until: str | None = None) -> str:
    """The markup between one panel title and the next."""
    chunk = body.split(f">{title}<", 1)
    assert len(chunk) == 2, f"no panel titled {title!r}"
    return chunk[1].split(f">{until}<", 1)[0] if until else chunk[1]


async def _drafting_league(client, names: list[str]):
    from tests.test_draft_flow import start_draft

    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, names)
    return code, players


async def test_the_draft_order_panel_is_in_draft_order(client):
    """It was titled "Draft order" and sorted by team name — so it listed
    Bot 1..Bot 6 while Bot 4 was picking, and the slot column (4, 7, 3, 2, 5,
    6, 1) read as noise instead of the snake."""
    import re

    code, players = await _drafting_league(client, ["Zoe", "Amy", "Mick"])

    table = _chunk((await client.get(f"/l/{code}/draft")).text, "Draft order", "Teams so far")
    slots = [int(n) for n in re.findall(r'width:40px" class="muted">(\d+)<', table)]

    # One client per team — the admin who made the league is a team too.
    assert len(slots) == len(players), f"expected {len(players)} rows, saw slots {slots}"
    assert slots == sorted(slots), f"the panel listed slots {slots} — that is not draft order"

    await close_all(players)


async def test_the_draft_order_table_labels_its_columns(client):
    """Two bare numeric columns, no headers: the "You" row read `1 … 1`."""
    code, players = await _drafting_league(client, ["Zoe", "Amy", "Mick"])

    table = _chunk((await client.get(f"/l/{code}/draft")).text, "Draft order", "Teams so far")

    assert "<thead>" in table, "the draft order table has no header row"
    for label in ("#", "Team", "Picks"):
        assert f">{label}<" in table, f"the draft order table never says what {label!r} means"

    await close_all(players)


async def test_teams_so_far_lists_every_team_not_only_the_picked_ones(client):
    """One row for one team read as a bug — it was every team without a driver
    simply being omitted."""
    code, players = await _drafting_league(client, ["Zoe", "Amy", "Mick"])

    panel = _chunk((await client.get(f"/l/{code}/draft")).text, "Teams so far")

    assert panel.count("No picks yet") == len(players), (
        "teams with no drivers must still appear, marked rather than missing"
    )
    for name in ("Zoe", "Amy", "Mick", "Admin"):
        assert name in panel, f"{name} is missing from Teams so far"

    await close_all(players)


async def test_the_pick_confirmation_does_not_tell_you_to_close_the_tab(client):
    """The confirmation banner used to say "This tab can be closed" — advice
    that loses the player their next turn, shown while a clock is still running."""
    code = await _league_with_team(client)

    body = (await client.get(f"/l/{code}/draft?picked=1")).text.lower()

    assert "pick recorded" in body
    assert "keep this tab open" in body
    assert "close this tab" not in body


async def test_the_countdown_says_what_it_is_counting_down_to(client):
    """A bare `7:16` answers neither "whose clock" nor "what happens at zero"."""
    code, players = await _drafting_league(client, ["Zoe", "Amy"])

    body = (await client.get(f"/l/{code}/draft")).text

    assert "Auto-pick in" in body, "the countdown is unlabelled"
    assert "picks made" in body, "the progress line still conflates two counters"

    await close_all(players)


async def test_your_own_turn_is_flagged_in_the_panel_and_the_tab_title(client):
    from tests.test_draft_flow import on_the_clock

    code, players = await _drafting_league(client, ["Zoe", "Amy"])
    name, on_clock = await on_the_clock(players, code)
    assert on_clock is not None

    yours = (await on_clock.get(f"/l/{code}/draft")).text
    assert "panel--on-clock" in yours, "your own clock is not visually distinguished"
    assert "state-tag--you" in yours
    assert "var yourTurn = true" in yours, "the tab title cannot signal your turn"

    watcher = next(c for n, c in players.items() if n != name)
    theirs = (await watcher.get(f"/l/{code}/draft")).text
    assert "var yourTurn = false" in theirs
    assert "state-tag--you" not in theirs

    await close_all(players)


async def test_the_hub_polls_for_changes_and_the_pulse_reports_the_pick(client):
    """Server-rendered pages go stale silently: while you watched, Bot 4 picked
    and your countdown kept ticking from a deadline that no longer exists."""
    code, players = await _drafting_league(client, ["Zoe", "Amy"])

    body = (await client.get(f"/l/{code}/draft")).text
    assert "/draft/pulse" in body, "the hub has no way to notice the board changed"

    pulse = await client.get(f"/l/{code}/draft/pulse")
    assert pulse.status_code == 200
    data = pulse.json()
    assert data["status"] == "drafting"
    assert data["current_pick"] == 0
    assert data["on_the_clock"], "the pulse does not say who is picking"

    await close_all(players)
