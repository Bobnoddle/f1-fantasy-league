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
