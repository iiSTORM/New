"""The news feed: parsing, who an item names, and how it is graded.

No network: the feeds are built inline in both shapes the sources use
(RSS 2.0 and Atom), and the entities are a small hand-made roster.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_news as sn  # noqa: E402

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)

ENTS = {"lol": {"teams": {"T1": sn.team_aliases("T1"), "Gen.G": sn.team_aliases("Gen.G"),
                          "Team Liquid": sn.team_aliases("Team Liquid")},
                "players": {"Faker": "T1", "Kiin": "Gen.G", "Light": "T1", "APA": "Team Liquid"}},
        "cs2": {"teams": {"Team Vitality": sn.team_aliases("Team Vitality")},
                "players": {"ZywOo": "Team Vitality"}}}

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>T1 to field a stand-in as Faker deals with wrist pain</title>
<link>https://x/1</link><pubDate>Sun, 12 Oct 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;The mid laner is &lt;b&gt;out&lt;/b&gt;.&lt;/p&gt;</description></item>
<item><title>Patch notes roundup</title><link>https://x/2</link>
<pubDate>Sun, 12 Oct 2026 09:00:00 GMT</pubDate><description>No teams here.</description></item>
<item><title>Old story about T1</title><link>https://x/3</link>
<pubDate>Mon, 01 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>ZywOo returns to Vitality lineup after illness</title>
<link href="https://y/1"/><published>2026-10-11T08:00:00Z</published>
<summary>Cleared to play.</summary></entry></feed>"""


def test_rss_and_atom_parse_and_html_is_stripped():
    rss = sn.parse_feed(RSS, "lol", "Dot Esports")
    assert [i["link"] for i in rss] == ["https://x/1", "https://x/2", "https://x/3"]
    assert rss[0]["summary"] == "The mid laner is out ."
    atom = sn.parse_feed(ATOM, "cs2", "HLTV")
    assert atom[0]["link"] == "https://y/1" and atom[0]["published"].startswith("2026-10-11")
    assert sn.parse_feed("not xml", "lol", "x") == []


def test_google_news_titles_lose_the_publisher_suffix():
    xml = ("<rss><channel><item><title>Gen.G benches Kiin - Some Site</title><link>https://g/1</link>"
           "<pubDate>Sun, 12 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    it = sn.parse_feed(xml, "lol", "Google News")[0]
    assert it["title"] == "Gen.G benches Kiin" and it["source"] == "Some Site"


def test_only_items_naming_a_tracked_team_are_kept_and_old_ones_age_out():
    items = sn.build(sn.parse_feed(RSS, "lol", "Dot Esports"), ENTS, NOW)
    assert [i["link"] for i in items] == ["https://x/1"]
    assert items[0]["teams"] == ["T1"] and items[0]["players"] == [{"name": "Faker", "team": "T1"}]


def test_a_stand_in_and_an_injury_are_negative_with_their_rules_named():
    it = sn.build(sn.parse_feed(RSS, "lol", "Dot Esports"), ENTS, NOW)[0]
    assert it["grade"] == "negative" and it["score"] == -4
    assert it["rules"] == ["stand-in", "illness/injury"]


def test_a_return_from_illness_is_positive_not_negative():
    it = sn.build(sn.parse_feed(ATOM, "cs2", "HLTV"), ENTS, NOW)[0]
    assert it["grade"] == "positive" and "illness/injury" not in it["rules"]


def test_a_handle_that_is_also_a_word_needs_its_team_named():
    assert sn.mentions("The light was poor at the venue.", ENTS["lol"]) == ([], [])
    assert sn.mentions("Light is out for T1 this week.", ENTS["lol"])[1] == [{"name": "Light", "team": "T1"}]
    # Short all-caps handles stay case-sensitive.
    assert sn.mentions("apa", ENTS["lol"]) == ([], [])


def test_team_aliases_and_signals():
    assert "Liquid" in sn.team_aliases("Team Liquid")
    items = sn.build(sn.parse_feed(RSS, "lol", "Dot Esports"), ENTS, NOW)
    sig = sn.team_signals(items, NOW)
    assert sig == {"lol": {"T1": {"score": -4, "items": 1, "rules": ["stand-in", "illness/injury"]}}}


def test_duplicate_titles_across_feeds_are_kept_once():
    twice = sn.parse_feed(RSS, "lol", "A") + sn.parse_feed(RSS, "lol", "B")
    assert len(sn.build(twice, ENTS, NOW)) == 1


def test_the_real_data_files_give_every_game_teams_and_players():
    import os
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        ents = sn.load_entities()
    finally:
        os.chdir(cwd)
    for game in ("lol", "valorant", "cs2"):
        assert len(ents[game]["teams"]) > 10 and len(ents[game]["players"]) > 50


def test_an_ordinary_word_after_an_article_is_not_a_player():
    ents = {"teams": {"Procyon": ["Procyon"]}, "players": {"next": "Procyon", "ropz": "Procyon"}}
    assert sn.mentions("Procyon look ahead to the next event", ents)[1] == []
    assert sn.mentions("Procyon: ropz on form", ents)[1] == [{"name": "ropz", "team": "Procyon"}]


def test_the_first_live_run_grades():
    """Headlines from the first real run, graded as a reader would."""
    def g(title):
        return sn.grade(title)
    assert g("siuhy Free Agent After Liquid Exit")[0] < 0
    assert g("Envy Tease CS2 Return as 3DMAX Roster Deal Reportedly Nears")[0] == 0
    assert g("TOBIZ returns to MOUZ as head of talent development")[0] == 0
    assert g("Falcons announce m0NESY contract extension")[1] == ["extension"]
    assert "signing" in g("Team Heretics Add Jamelinho213 to Complete 2027 VCT Roster")[1]
    assert g("ZywOo returns from illness")[0] > 0


def test_second_live_run_grades():
    g = lambda t: sn.grade(t)[0]
    assert g("200% Cuteness: T1 Universal Minions Collection Released") == 0
    assert g("Paper Rex Hold Unfortunate Record After Valorant Champions Exit") == 0
    assert g("TES Top Laner ZUIAN Denied Visa for Worlds 2026: 369 Steps In") < 0
    assert g("Astralis Roster Change: HooXi and jabbi Benched") < 0


def test_one_story_in_many_outlets_counts_once_per_team():
    xml = "<rss><channel>" + "".join(
        f"<item><title>T1 visa trouble, report {i}</title><link>https://v/{i}</link>"
        f"<pubDate>Sun, 12 Oct 2026 0{i}:00:00 GMT</pubDate></item>" for i in range(5)) + "</channel></rss>"
    items = sn.build(sn.parse_feed(xml, "lol", "x"), ENTS, NOW)
    assert len(items) == 5
    assert sn.team_signals(items, NOW)["lol"]["T1"] == {"score": -2, "items": 5, "rules": ["visa"]}


def test_shouted_handles_match_and_live_score_pages_are_dropped():
    ents = {"teams": {"Top Esports": ["Top Esports"]}, "players": {"Zuian": "Top Esports"}}
    assert sn.mentions("TES ZUIAN misses Worlds", ents)[1] == [{"name": "Zuian", "team": "Top Esports"}]
    xml = ("<rss><channel><item><title>Top Esports - Foo - Live score</title><link>https://l/1</link>"
           "<pubDate>Sun, 12 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    dropped = {}
    assert sn.build(sn.parse_feed(xml, "lol", "x"), {"lol": ents}, NOW, dropped) == []
    assert dropped == {"live-score or stream page": 1}
