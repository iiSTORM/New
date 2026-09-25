"""Keeping every map, so every posted window can be settled on its own.

A map-1 line, a maps-1-2 line and a maps-1-3 line on one series are three
different markets. Both CS2 and Valorant used to throw the split away and
store one maps-1-2 total, which meant the only window either could ever
settle was maps 1-2 -- 221 posted lines on a real board were refused for
no reason other than that, and a maps-1-3 line could never have been
graded at all.

Both scrapers keep the per-map breakdown now. What these guard is the
pair of properties that makes that safe:

  the breakdown is COMPLETE  -- every map played is in it, including the
                                third map of a Bo3 that went the distance,
                                which is the map both scrapers discarded.
  the total is UNCHANGED     -- `actual` still sums exactly maps 1-2. It
                                is what the model's history is built from,
                                and widening it would silently rewrite
                                every per-map rate the model has been
                                fitted against.
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


# --------------------------------------------------------------------- CS2

pytest.importorskip("aiohttp")
pytest.importorskip("cs2api")
import scrape_cs2 as sc  # noqa: E402


def map_row(team, k, d, a, hs, team_total_k):
    return {"k": k, "d": d, "a": a, "hs": hs, "team": team,
            "kp": (k + a) / team_total_k, "team_total_k": team_total_k}


def cs2_series(monkeypatch, maps):
    """Runs the real fetch_match_actuals against N maps of box score.

    `maps` is a list of per-map {player: row} dicts for one player per
    side, which is all the window arithmetic needs.
    """
    async def fake_bo3_get(session, path, params=None):
        return {"games": [{"id": i, "number": i + 1} for i in range(len(maps))],
                "winner_team_id": 1, "team1_score": 2, "team2_score": 1,
                "start_date": "2026-09-21T18:00:00+00:00"}

    async def fake_map_stats(session, game_id, canonical):
        return maps[game_id], {1: "Sashi", 2: "FOKUS"}

    monkeypatch.setattr(sc, "bo3_get", fake_bo3_get)
    monkeypatch.setattr(sc, "fetch_map_player_stats", fake_map_stats)
    return asyncio.run(sc.fetch_match_actuals(None, "a-vs-b", {}))


def three_map_series():
    return [
        {"acoR": map_row("Sashi", 14, 10, 2, 7, 70),
         "jabbi": map_row("FOKUS", 11, 14, 4, 5, 62)},
        {"acoR": map_row("Sashi", 14, 12, 3, 6, 68),
         "jabbi": map_row("FOKUS", 16, 13, 2, 9, 74)},
        {"acoR": map_row("Sashi", 11, 15, 5, 4, 55),
         "jabbi": map_row("FOKUS", 19, 10, 3, 11, 80)},
    ]


class TestCs2:
    def test_every_map_played_is_kept(self, monkeypatch):
        """Including map 3, which the old slice dropped before it was even
        fetched -- the request for it was never made."""
        totals, per_game, *_ = cs2_series(monkeypatch, three_map_series())
        assert len(per_game) == 3
        assert [g["Sashi"]["acoR"]["k"] for g in per_game] == [14, 14, 11]

    def test_the_series_total_still_stops_at_two_maps(self, monkeypatch):
        """The load-bearing assertion. `actual` feeds every per-map rate
        the model has ever been fitted against; a third map leaking into
        it would move every one of them without a single test failing
        anywhere else."""
        totals, per_game, maps_played, *_ = cs2_series(monkeypatch, three_map_series())
        assert totals["Sashi"]["acoR"]["k"] == 28   # 14 + 14, not 39
        assert totals["Sashi"]["acoR"]["d"] == 22
        assert maps_played == 2

    def test_kill_participation_still_covers_exactly_those_two_maps(self, monkeypatch):
        """kp is a ratio of two running sums, so a third map leaking into
        one of them and not the other would corrupt it in a way no total
        would show."""
        totals, *_ = cs2_series(monkeypatch, three_map_series())
        row = totals["Sashi"]["acoR"]
        assert row["kp_numerator"] == (14 + 2) + (14 + 3)
        assert row["kp_denominator"] == 70 + 68

    def test_a_two_map_series_reports_two(self, monkeypatch):
        totals, per_game, maps_played, *_ = cs2_series(monkeypatch, three_map_series()[:2])
        assert len(per_game) == 2 and maps_played == 2

    def test_a_one_map_series_is_not_claimed_to_be_two(self, monkeypatch):
        """A Bo1's total covers one map. Reporting two would tell the
        grader to settle a maps-1-2 line on a series that never played
        one."""
        totals, per_game, maps_played, *_ = cs2_series(monkeypatch, three_map_series()[:1])
        assert len(per_game) == 1 and maps_played == 1

    def test_the_per_map_rows_carry_what_a_line_can_name(self, monkeypatch):
        _, per_game, *_ = cs2_series(monkeypatch, three_map_series())
        assert per_game[0]["Sashi"]["acoR"] == {"k": 14, "d": 10, "a": 2, "hs": 7}

    def test_a_missing_stat_is_left_out_rather_than_zeroed(self):
        """The grader reads an absent stat as "not recorded" and refuses.
        A zero would settle the line as a real 0, which is a result rather
        than a gap."""
        row = map_row("Sashi", 14, 10, 2, 0, 70)
        del row["hs"]
        assert "hs" not in sc.per_map_entry(row)
        assert sc.per_map_entry(row) == {"k": 14, "d": 10, "a": 2}

    def test_both_views_agree_on_the_window_they_share(self, monkeypatch):
        """If the breakdown and the total disagree about maps 1-2, one of
        them is wrong and everything built on either is suspect."""
        totals, per_game, *_ = cs2_series(monkeypatch, three_map_series())
        for stat in ("k", "d", "a"):
            assert (sum(g["Sashi"]["acoR"][stat] for g in per_game[:2])
                    == totals["Sashi"]["acoR"][stat])


# ---------------------------------------------------------------- Valorant

pytest.importorskip("requests")
pytest.importorskip("bs4")
import scrape_valorant as sv  # noqa: E402


def vlr_row(name, tag, k, d, a):
    """One stat row in the all/attack/defend shape the real page uses."""
    return (f'<div class="ovw-row"><a href="/player/1/{name}">'
            f'<div class="ovw-player-name">{name}</div>'
            f'<div class="ovw-player-tag">{tag}</div></a>'
            f'<span>1.10 1.10 1.10 200 200 200 '
            f'{k} 0 0 / {d} 0 0 / {a} 0 0 +0 +0 +0 '
            f'80% 80% 80% 150 150 150 30% 30% 30% 0 0 0 0 0 0 0 +0 +0</span></div>')


def vlr_page(maps, include_all_maps=True):
    """A match page carrying one section per map, plus the combined "All
    Maps" view that sits in the same HTML and must not be counted."""
    sections = []
    if include_all_maps:
        rows = "".join(vlr_row(n, t, k * len(maps), d * len(maps), a * len(maps))
                       for n, t, k, d, a in maps[0])
        sections.append(f'<div data-game-id="all">{rows}</div>')
    for i, one_map in enumerate(maps):
        rows = "".join(vlr_row(n, t, k, d, a) for n, t, k, d, a in one_map)
        sections.append(f'<div data-game-id="{100 + i}">{rows}</div>')
    return (
        '<html><body>'
        '<a href="/team/1/leviatan">LEV</a><a href="/team/2/furia">FUR</a>'
        '<div>LEV final 2 : 1 vs. FUR</div>'
        '<div data-utc-ts="2026-09-21 18:00:00"></div>'
        + "".join(sections) +
        '</body></html>'
    )


def three_map_valorant():
    return [
        [("Neon", "LEV", 19, 10, 2), ("mwzera", "FUR", 15, 14, 3)],
        [("Neon", "LEV", 15, 13, 11), ("mwzera", "FUR", 18, 12, 4)],
        [("Neon", "LEV", 11, 16, 6), ("mwzera", "FUR", 21, 9, 5)],
    ]


def parse(monkeypatch, maps, **kw):
    monkeypatch.setattr(sv, "get", lambda url, retries=3: vlr_page(maps, **kw))
    return sv.parse_match(706349, "/706349/lev-vs-fur")


class TestValorant:
    def test_every_map_played_is_kept(self, monkeypatch):
        result = parse(monkeypatch, three_map_valorant())
        assert result["played"] is True
        assert len(result["per_game"]) == 3
        assert [g["LEV"]["Neon"]["k"] for g in result["per_game"]] == [19, 15, 11]

    def test_the_series_total_still_stops_at_two_maps(self, monkeypatch):
        result = parse(monkeypatch, three_map_valorant())
        assert result["actual"]["LEV"]["Neon"]["k"] == 34   # 19 + 15, not 45
        assert result["maps_played"] == 2

    def test_the_combined_all_maps_view_is_still_excluded(self, monkeypatch):
        """It sits in the same HTML and appears FIRST in document order.
        Counting it would make map 1 the whole series and push every real
        map one slot down -- the bug that made totals look like set
        totals before any of this existed."""
        with_all = parse(monkeypatch, three_map_valorant())
        without = parse(monkeypatch, three_map_valorant(), include_all_maps=False)
        assert with_all["per_game"] == without["per_game"]
        assert with_all["actual"] == without["actual"]

    def test_both_views_agree_on_the_window_they_share(self, monkeypatch):
        result = parse(monkeypatch, three_map_valorant())
        for stat in ("k", "d", "a"):
            assert (sum(g["LEV"]["Neon"][stat] for g in result["per_game"][:2])
                    == result["actual"]["LEV"]["Neon"][stat])

    def test_a_two_map_series_has_two_maps(self, monkeypatch):
        result = parse(monkeypatch, three_map_valorant()[:2])
        assert len(result["per_game"]) == 2

    def test_an_unplayed_match_carries_no_breakdown(self, monkeypatch):
        monkeypatch.setattr(sv, "get", lambda url, retries=3:
                            '<html><body><a href="/team/1/lev">LEV</a>'
                            '<a href="/team/2/fur">FUR</a></body></html>')
        result = sv.parse_match(1, "/1/lev-vs-fur")
        assert result["played"] is False and result["per_game"] is None


class TestGradingReadsThem:
    """The point of all of the above: windows that used to be refused.

    Grading lives in score_props.py and is tested there against
    hand-built records. This runs the two real scrapers' own output
    through it, so the record SHAPE the scrapers produce is checked
    against the shape the grader reads rather than assumed to match.
    """

    def match_from_cs2(self, monkeypatch):
        totals, per_game, maps_played, *_ = cs2_series(monkeypatch, three_map_series())
        return {"date": "2026-09-21", "teamA": "Sashi", "teamB": "FOKUS",
                "actual": totals, "games": maps_played, "per_game": per_game}

    @pytest.mark.parametrize("maps,expected", [(1, 14), (2, 28), (3, 39)])
    def test_each_cs2_window_settles_on_its_own_maps(self, monkeypatch, maps, expected):
        import score_props as sp
        value, reason = sp.actual_over_window(
            self.match_from_cs2(monkeypatch), "Sashi", "acoR", "kills", maps, "cs2")
        assert (value, reason) == (expected, None)

    @pytest.mark.parametrize("maps,expected", [(1, 19), (2, 34), (3, 45)])
    def test_each_valorant_window_settles_on_its_own_maps(self, monkeypatch, maps, expected):
        import score_props as sp
        result = parse(monkeypatch, three_map_valorant())
        match = {"date": "2026-09-21", "teamA": "LEV", "teamB": "FUR",
                 "actual": result["actual"], "games": result["maps_played"],
                 "per_game": result["per_game"]}
        value, reason = sp.actual_over_window(match, "LEV", "Neon", "kills", maps, "valorant")
        assert (value, reason) == (expected, None)
