"""Reading the agent off a vlr.gg player-map row.

The icon is the only place a match page says which agent a player was on,
and the Record tab groups Valorant by its class. These rows are built in
the two shapes vlr.gg has used for the overview table (the older
td.mod-agents cell and the ovw-row layout this scraper parses), since the
reader looks for the icon anywhere in the row rather than in a fixed cell.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("requests")
bs4 = pytest.importorskip("bs4")

import scrape_valorant as sv  # noqa: E402


def row(inner):
    return bs4.BeautifulSoup(f'<div class="ovw-row">{inner}</div>', "html.parser").div


PLAYER = '<a href="/player/1/x"><div class="ovw-player-name">x</div><div class="ovw-player-tag">T</div></a>'


@pytest.mark.parametrize("inner,expected", [
    # the classic overview cell
    ('<td class="mod-agents"><span class="stats-sq mod-agent small">'
     '<img src="/img/vlr/game/agents/jett.png" alt="jett" title="Jett"></span></td>', ("jett", "duelist")),
    # icon only, no title, absolute URL and webp
    ('<img src="https://www.vlr.gg/img/vlr/game/agents/omen.webp">', ("omen", "controller")),
    # the slash in KAY/O, by title when the file name is unusual
    ('<img src="/img/vlr/game/agents/" title="KAY/O">', ("kayo", "initiator")),
    ('<img src="/img/vlr/game/agents/kayo.png">', ("kayo", "initiator")),
    ('<img src="/img/vlr/game/agents/killjoy.png">', ("killjoy", "sentinel")),
])
def test_agent_and_class(inner, expected):
    assert sv.agent_from_row(row(PLAYER + inner)) == expected


def test_a_flag_or_team_logo_is_never_an_agent():
    r = row(PLAYER + '<img src="/img/icons/flags/16/us.png"><img src="//owcdn.net/img/team-logo.png">')
    assert sv.agent_from_row(r) == (None, None)


def test_an_agent_without_a_class_is_kept_and_reported(monkeypatch):
    monkeypatch.setattr(sv, "AGENT_COUNTS", {"rows": 0, "found": 0, "unknown": {}})
    assert sv.agent_from_row(row('<img src="/img/vlr/game/agents/newagent.png">')) == ("newagent", None)
    assert sv.AGENT_COUNTS["unknown"] == {"newagent": 1}


def test_every_class_is_one_of_the_four():
    assert set(sv.AGENT_CLASS.values()) == {"duelist", "initiator", "controller", "sentinel"}
