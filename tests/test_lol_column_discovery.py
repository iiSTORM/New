"""The wider gol.gg players/list table, now that the run has named it.

The labels had never been seen from this environment, so the scraper
printed them rather than guessing. It offers 26 columns and five were
being read. The two that matter are "FB %" and "FB Victim": the LoL
analogues of the Valorant opening-duel columns, which were the only
signal to survive an honest point-in-time test there.

These run against the real header, verbatim from the run that reported
it, rather than an invented one -- a fixture that spells a label
differently from gol.gg would pass while the scraper read nothing.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "scrape_lcs.py"
pytestmark = pytest.mark.skipif(not SRC.exists(), reason="scraper not present")

# Verbatim from the run: "[columns] gol.gg players/list offers 26: [...]"
OFFERED = ['Player', 'Country', 'Games', 'Win rate', 'KDA', 'Avg kills',
           'Avg deaths', 'Avg assists', 'CSM', 'GPM', 'KP%', 'DMG%', 'Gold%',
           'VS%', 'DPM', 'VSPM', 'Avg WPM', 'Avg WCPM', 'Avg VWPM', 'GD@15',
           'CSD@15', 'XPD@15', 'FB %', 'FB Victim', 'Penta Kills', 'Solo Kills']

ROW = ['Faker', 'KR', '30', '60%', '4.1', '3.2', '1.8', '6.4', '9.1', '420',
       '68%', '25%', '23%', '18%', '520', '1.2', '0.6', '0.3', '0.2', '-120',
       '-4', '150', '12%', '8%', '1', '4']


def table(header=OFFERED, rows=(ROW,)):
    head = "".join(f"<th>{h}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="table_list"><tr>{head}</tr>{body}</table>'


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


@pytest.fixture
def scraper(monkeypatch):
    import types
    sys.modules.setdefault("requests", types.ModuleType("requests"))
    sys.path.insert(0, str(ROOT / "scripts"))
    pytest.importorskip("bs4")
    import scrape_lcs as sl
    monkeypatch.setattr(sl, "_HEADER_LABELS_REPORTED", False)
    return sl


def run(sl, html, tournament="FAKE 2026 Summer"):
    import io
    sl.get = lambda url: html
    err = io.StringIO()
    real, sys.stderr = sys.stderr, err
    try:
        players = sl.parse_player_list(tournament)
    finally:
        sys.stderr = real
    return players, err.getvalue()


class TestTheColumnsAreRead:
    def test_every_column_asked_for_is_one_gol_gg_offers(self, scraper):
        """The label is the binding. A key whose label is misspelled
        here is simply never read, silently, for every player forever."""
        missing = [label for label in scraper.EXTRA_COLUMNS if label not in OFFERED]
        assert missing == [], f"not on gol.gg's table: {missing}"

    def test_the_opening_duel_columns_are_among_them(self, scraper):
        """The reason this was worth doing at all."""
        assert scraper.EXTRA_COLUMNS["FB %"] == "fb_pct"
        assert scraper.EXTRA_COLUMNS["FB Victim"] == "fb_victim"

    def test_a_real_row_yields_every_value(self, scraper):
        players, _ = run(scraper, table())
        got = players["Faker"]
        assert got["k"] == 3.2 and got["d"] == 1.8 and got["a"] == 6.4
        assert got["fb_pct"] == 12.0
        assert got["fb_victim"] == 8.0
        assert got["dpm"] == 520.0
        assert got["solo_kills"] == 4.0
        assert got["csm"] == 9.1
        assert got["gpm"] == 420.0

    def test_a_negative_differential_keeps_its_sign(self, scraper):
        """GD@15 is a differential and is negative for half the league.
        A parser that strips the minus reads every deficit as a lead."""
        players, _ = run(scraper, table())
        assert players["Faker"]["gd15"] == -120.0
        assert players["Faker"]["csd15"] == -4.0
        assert players["Faker"]["xpd15"] == 150.0

    def test_a_percentage_loses_its_sign_not_its_value(self, scraper):
        players, _ = run(scraper, table())
        assert players["Faker"]["win_rate"] == 60.0
        assert players["Faker"]["dmg_pct"] == 25.0

    def test_values_come_from_the_label_not_the_position(self, scraper):
        """gol.gg reordering its table must move the values with it. If
        this binds by index, every number silently becomes another
        column's number -- which is worse than reading nothing."""
        order = list(reversed(OFFERED))
        row = [ROW[OFFERED.index(label)] for label in order]
        players, _ = run(scraper, table(order, (row,)))
        got = players["Faker"]
        assert got["fb_pct"] == 12.0 and got["dpm"] == 520.0 and got["gd15"] == -120.0
        assert got["k"] == 3.2

    def test_columns_that_are_deliberately_left_are_left(self, scraper):
        """KDA is a function of k/d/a, which are already stored, and
        Country is not a number. Carrying them is weight for nothing."""
        players, _ = run(scraper, table())
        assert "kda" not in players["Faker"]
        assert "country" not in players["Faker"]
        assert "penta_kills" not in players["Faker"]


class TestAMissingValueIsNotZero:
    def test_an_empty_cell_leaves_the_key_off(self, scraper):
        row = list(ROW)
        row[OFFERED.index("FB %")] = ""
        players, _ = run(scraper, table(rows=(row,)))
        assert "fb_pct" not in players["Faker"], (
            "a cell that said nothing must not become a player who never "
            "took a first blood")

    def test_a_dash_leaves_the_key_off(self, scraper):
        row = list(ROW)
        row[OFFERED.index("DPM")] = "-"
        players, _ = run(scraper, table(rows=(row,)))
        assert "dpm" not in players["Faker"]

    def test_a_not_a_number_is_refused(self, scraper):
        """float() accepts "nan" and "inf". A NaN that gets in poisons
        every mean and every comparison downstream of it, silently."""
        for junk in ("nan", "NaN", "inf", "-Infinity"):
            row = list(ROW)
            row[OFFERED.index("DPM")] = junk
            players, _ = run(scraper, table(rows=(row,)))
            assert "dpm" not in players["Faker"], f"{junk!r} got through"

    def test_a_genuine_zero_is_kept(self, scraper):
        """The other half of the same rule. Never taking a first blood
        is a real fact about a player and must survive."""
        row = list(ROW)
        row[OFFERED.index("FB %")] = "0%"
        players, _ = run(scraper, table(rows=(row,)))
        assert players["Faker"]["fb_pct"] == 0.0

    def test_an_unreadable_extra_cell_does_not_cost_the_row_its_kills(self, scraper):
        """k/d/a is what the model is actually built on. A junk DPM must
        not take a whole player out of the payload with it."""
        row = list(ROW)
        row[OFFERED.index("DPM")] = "n/a!!"
        players, _ = run(scraper, table(rows=(row,)))
        assert players["Faker"]["k"] == 3.2
        assert "dpm" not in players["Faker"]

    def test_a_column_gol_gg_stops_offering_is_simply_absent(self, scraper):
        narrow = [h for h in OFFERED if h != "FB %"]
        row = [ROW[OFFERED.index(h)] for h in narrow]
        players, out = run(scraper, table(narrow, (row,)))
        assert "fb_pct" not in players["Faker"]
        assert players["Faker"]["fb_victim"] == 8.0, "its neighbours must not shift"
        assert "asked for but NOT OFFERED" in out and "FB %" in out


class TestTheTiersCarryThem:
    def test_a_tier_holds_the_extras_alongside_kda(self, scraper):
        tier = scraper.tier_stats(
            {"g": 30, "k": 3.2, "d": 1.8, "a": 6.4, "kp": 68.0, "fb_pct": 12.0,
             "name": "Faker", "team": None, "role": None})
        assert tier["fb_pct"] == 12.0 and tier["k"] == 3.2

    def test_a_tier_does_not_repeat_the_player_s_own_identity(self, scraper):
        tier = scraper.tier_stats(
            {"g": 1, "k": 1, "d": 1, "a": 1, "kp": 1, "name": "Faker",
             "team": "T1", "role": "MID"})
        assert set(tier) == {"g", "k", "d", "a", "kp"}

    def test_a_player_with_no_historical_split_still_has_a_tier_of_none(self, scraper):
        assert scraper.tier_stats(None) is None

    def test_the_payload_carries_them_end_to_end(self, scraper):
        cur = {"Faker": {"g": 30, "k": 3.2, "d": 1.8, "a": 6.4, "kp": 68.0,
                         "fb_pct": 12.0, "dpm": 520.0}}
        hist = {"Faker": {"g": 20, "k": 2.9, "d": 2.1, "a": 5.0, "kp": 64.0,
                          "fb_pct": 9.0}}
        roster = {"Faker": {"team": "T1", "role": "MID"}}
        teams = scraper.build_teams_payload(cur, hist, roster)
        player = teams["T1"]["players"][0]
        assert player["cur"]["fb_pct"] == 12.0 and player["cur"]["dpm"] == 520.0
        assert player["hist"]["fb_pct"] == 9.0, (
            "the historical split is the tier a point-in-time test can "
            "actually use -- it is a different tournament from the matches "
            "being scored")
        assert "dpm" not in player["hist"]


class TestTheReportStillTellsTheTruth:
    def test_it_no_longer_calls_the_new_columns_unread(self, scraper):
        _, out = run(scraper, table())
        unread = out.split("not read:", 1)[1]
        for now_read in ("DPM", "FB %", "FB Victim", "Solo Kills", "GD@15"):
            assert now_read not in unread, f"{now_read} is read now"

    def test_it_still_names_what_is_genuinely_left(self, scraper):
        _, out = run(scraper, table())
        unread = out.split("not read:", 1)[1]
        for left in ("Country", "KDA", "Penta Kills", "Avg WPM"):
            assert left in unread

    def test_it_says_how_many_rows_carried_each_column(self, scraper):
        _, out = run(scraper, table())
        assert "extra columns on 1 row(s)" in out
        assert "fb_pct=1" in out

    def test_it_names_a_column_that_came_back_empty_for_everyone(self, scraper):
        """The quiet failure: a column that parses to nothing on every
        row looks exactly like a column nobody has a value for."""
        row = list(ROW)
        row[OFFERED.index("DPM")] = "-"
        players, out = run(scraper, table(rows=(row,)))
        assert "EMPTY for every row" in out and "dpm" in out.split("EMPTY for every row")[1]

    def test_it_reports_once_across_tournaments(self, scraper):
        """Seven regions times two tournaments would be fourteen copies."""
        _, first = run(scraper, table(), "FAKE 2026 Summer")
        _, second = run(scraper, table(), "FAKE 2026 Spring")
        assert "[columns]" in first and "[columns]" not in second

    def test_a_table_with_no_header_says_nothing_rather_than_crashing(self, scraper):
        players, out = run(scraper, "<table class='table_list'></table>")
        assert players == {} and "[columns]" not in out


class TestTheOldGuarantees:
    def test_columns_are_still_found_by_header_text(self, source):
        for label in ("Games", "Avg kills", "Avg deaths", "Avg assists", "KP%"):
            assert f'col("{label}")' in source

    def test_it_reports_before_any_row_is_parsed(self, source):
        """A table whose rows all fail is exactly when the labels matter
        most, so the report must not sit behind the parse loop."""
        report = source.index("[columns] gol.gg players/list offers")
        loop = source.index("for i, row in enumerate(rows[1:], start=1):")
        assert report < loop

    def test_the_report_goes_to_stderr_with_the_other_diagnostics(self, source):
        idx = source.index("[columns] gol.gg players/list offers")
        assert "file=sys.stderr" in source[idx:idx + 400]
