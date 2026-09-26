"""Tests for folding duplicate team spellings into one team.

The asymmetry to hold this against: a MISSED merge leaves a team projecting
off half its history, which is bad. A WRONG merge pools two different orgs'
histories under one name and prints the result as one team's form, which is
worse and much harder to notice. So most of what follows is about what the
rule refuses.
"""
import json
from pathlib import Path

import pytest

import team_aliases as ta

REPO_ROOT = Path(__file__).resolve().parent.parent


def team(players, color="#fff"):
    return {"color": color, "players": [{"name": p} for p in players]}


def match(a, b, winner=None, actual=None, per_game=None):
    return {"teamA": a, "teamB": b, "winner": winner or a,
            "actual": actual if actual is not None else {a: {}, b: {}},
            "per_game": per_game if per_game is not None else [{a: {}, b: {}}]}


class TestAliasKey:
    @pytest.mark.parametrize("a,b", [
        ("The Huns", "The Huns Esports"),
        ("Arcade", "Arcade Esports"),
        ("Drama", "Drama eSports"),
        ("Yawara", "Yawara Esports"),
        ("RBLS", "RBLS Gaming"),
        ("XDM", "Team XDM"),
        ("FlyQuest", "FLYQUEST"),
        ("Misa", "MISA"),
        ("Gremio", "Grêmio"),
        ("Honved", "Honvéd"),
        ("95 Vikings", "95_Vikings"),
        ("bLight blue", "bLight!blue"),
        ("JiJieHao", "JiJie Hao"),
        ("QUINTESSENCIA", "QUINTESSÊNCIA"),
        # "club" is deliberately NOT an org word: stripping it separated
        # "AIM Club" from "AIMCLUB", which is the spelling the source uses.
        ("AIM Club", "AIMCLUB"),
        ("Some Team e-sports", "Some Team"),
    ])
    def test_spellings_of_one_team_reduce_alike(self, a, b):
        assert ta.alias_key(a) == ta.alias_key(b) != ""

    def test_punctuation_joined_org_word_still_comes_off(self):
        """The source writes "G2!Esports" with no space, and a
        whitespace-only split leaves it one token with no word to strip."""
        assert ta.alias_key("G2!Esports") == ta.alias_key("G2")

    @pytest.mark.parametrize("a,b", [
        # Rebrands and roster moves: the same five players, a different org.
        ("BBL", "Echo"),
        ("A Great Chaos", "FAFO"),
        ("BORRACHEIROS", "Juventus"),
        # Abbreviations and typos, which is a judgement, not a respelling.
        ("MARKandLARRY", "MARKnLARRY"),
        ("NemNemesis", "Nemesis"),
        ("5STR", "5star"),
        ("NT", "Nice Try"),
        ("EX-Z10", "ex-Zero Tenacity"),
        # A qualifier that may be a real distinction.
        ("Orgless", "Orgless (Aus)"),
        ("Banda Chuya", "Banda chuya LFO"),
        # An id leaking into a team name, which needs its own fix.
        ("WBT Academy", "WBT Academy_2NMK3fBkP7gb7JK1"),
        # Genuinely different teams that merely look alike.
        ("Team Liquid", "Team Secret"),
        ("paiN", "paiN Academy"),
    ])
    def test_refuses_what_is_not_a_respelling(self, a, b):
        assert ta.alias_key(a) != ta.alias_key(b)

    def test_a_name_that_is_only_an_org_word_has_no_key(self):
        """Every bare 'Esports' entry would otherwise collapse together."""
        assert ta.alias_key("Esports") == ""
        assert ta.alias_key("Team") == ""

    @pytest.mark.parametrize("raw", [None, "", "   ", "!!!", 0])
    def test_survives_a_name_that_is_not_one(self, raw):
        assert ta.alias_key(raw) == ""


class TestAliasGroups:
    def test_groups_two_spellings_that_share_players(self):
        teams = {"The Huns": team(["Cozen", "nin9"]),
                 "The Huns Esports": team(["Cozen", "bexyz"])}
        matches = [match("The Huns", "Kaleido"), match("The Huns", "CW"),
                   match("The Huns Esports", "Kaleido")]
        assert ta.alias_groups(teams, matches) == {"The Huns": ["The Huns Esports"]}

    def test_the_spelling_with_the_most_matches_wins(self):
        teams = {"Arcade": team(["Kras"]), "Arcade Esports": team(["Kras"])}
        matches = [match("Arcade Esports", "X")] * 3 + [match("Arcade", "X")]
        assert ta.alias_groups(teams, matches) == {"Arcade Esports": ["Arcade"]}

    def test_a_tie_on_matches_falls_to_the_fuller_roster(self):
        teams = {"Omega": team(["a"]), "OMEGA": team(["a", "b"])}
        matches = [match("Omega", "X"), match("OMEGA", "X")]
        assert ta.alias_groups(teams, matches) == {"OMEGA": ["Omega"]}

    def test_a_tie_on_both_is_broken_the_same_way_every_run(self):
        """Not by dict order: two runs over one file have to agree, or the
        canonical name flips back and forth and splits the history again."""
        forward = ta.alias_groups({"Omega": team(["a"]), "OMEGA": team(["a"])}, [])
        backward = ta.alias_groups({"OMEGA": team(["a"]), "Omega": team(["a"])}, [])
        assert forward == backward == {"OMEGA": ["Omega"]}

    def test_refuses_two_spellings_whose_rosters_do_not_overlap(self):
        """A normalisation collision between two real orgs. The names alone
        are not enough to pool two histories."""
        teams = {"Phoenix": team(["a", "b"]), "Phoenix Esports": team(["c", "d"])}
        assert ta.alias_groups(teams, [match("Phoenix", "X")]) == {}

    def test_folds_in_a_stub_with_no_players(self):
        """Nothing to contradict, and a stub is how a name that only ever
        appeared in a fixture list gets into the file."""
        teams = {"Phantom": team(["a"]), "Phantom Esports": team([])}
        assert ta.alias_groups(teams, [match("Phantom", "X")]) == \
            {"Phantom": ["Phantom Esports"]}

    def test_folds_three_spellings_at_once(self):
        teams = {"QUINTESSÊNCIA": team(["a"]), "QUINTESSENCIA": team(["a"]),
                 "Quintessencia": team(["a"])}
        matches = [match("QUINTESSÊNCIA", "X")] * 3
        groups = ta.alias_groups(teams, matches)
        assert groups == {"QUINTESSÊNCIA": ["QUINTESSENCIA", "Quintessencia"]}

    def test_names_that_reduce_to_nothing_are_not_a_group(self):
        """"Esports" and "Team" both reduce to an empty key. Grouped on that,
        every such entry in the file folds into one team."""
        teams = {"Esports": team(["a"]), "Team": team(["a"]), "esport": team(["a"])}
        assert ta.alias_groups(teams, [match("Esports", "X")]) == {}

    def test_an_accent_survives_as_its_base_letter(self):
        """Not dropped: the tokeniser treats a precomposed ê as punctuation,
        so without the NFKD decomposition "Grêmio" reduces to "grmio" and
        stops matching "Gremio" from the other direction too."""
        assert ta.alias_key("Grêmio") == "gremio"
        assert ta.alias_key("QUINTESSÊNCIA") == "quintessencia"
        assert ta.alias_key("Honvéd") == "honved"

    def test_a_single_spelling_is_not_a_group(self):
        assert ta.alias_groups({"T1": team(["Faker"])}, []) == {}

    def test_survives_an_empty_region(self):
        assert ta.alias_groups({}, []) == {}


class TestApplyAliases:
    def build(self):
        return {
            "teams": {"The Huns": team(["Cozen", "nin9"], color="#111"),
                      "The Huns Esports": team(["Cozen", "bexyz"], color="#222")},
            "past_matches": [
                match("The Huns Esports", "Kaleido", winner="The Huns Esports",
                      actual={"The Huns Esports": {"Cozen": {"k": 20}}, "Kaleido": {}},
                      per_game=[{"The Huns Esports": {"Cozen": {"k": 11}}, "Kaleido": {}},
                                {"The Huns Esports": {"Cozen": {"k": 9}}, "Kaleido": {}}]),
                match("CW", "The Huns", winner="The Huns"),
            ],
            "upcoming_matches": [{"teamA": "The Huns Esports", "teamB": "K27"}],
        }

    def test_the_losing_team_entry_is_gone(self):
        region = self.build()
        ta.reconcile(region)
        assert sorted(region["teams"]) == ["Kaleido", "The Huns"] or \
            "The Huns Esports" not in region["teams"]
        assert "The Huns Esports" not in region["teams"]

    def test_its_players_move_across(self):
        region = self.build()
        ta.reconcile(region)
        names = {p["name"] for p in region["teams"]["The Huns"]["players"]}
        assert names == {"Cozen", "nin9", "bexyz"}

    def test_a_player_on_both_is_not_duplicated(self):
        region = self.build()
        ta.reconcile(region)
        names = [p["name"] for p in region["teams"]["The Huns"]["players"]]
        assert len(names) == len(set(names))

    def test_the_canonical_entry_keeps_its_own_fields(self):
        region = self.build()
        ta.reconcile(region)
        assert region["teams"]["The Huns"]["color"] == "#111"

    def test_match_sides_are_rewritten(self):
        region = self.build()
        ta.reconcile(region)
        sides = [(m["teamA"], m["teamB"]) for m in region["past_matches"]]
        assert sides == [("The Huns", "Kaleido"), ("CW", "The Huns")]

    def test_the_winner_is_rewritten(self):
        """Missed, and standings credit the win to a team that no longer
        exists in the roster list."""
        region = self.build()
        ta.reconcile(region)
        assert region["past_matches"][0]["winner"] == "The Huns"

    def test_the_actual_stats_block_is_rekeyed(self):
        region = self.build()
        ta.reconcile(region)
        actual = region["past_matches"][0]["actual"]
        assert set(actual) == {"The Huns", "Kaleido"}
        assert actual["The Huns"]["Cozen"]["k"] == 20

    def test_every_per_map_block_is_rekeyed(self):
        """per_game is a LIST of per-map dicts, each keyed by team — the one a
        rewrite that only looked at the obvious fields would miss, and the
        one every maps-1-2 and maps-1-3 line is settled from."""
        region = self.build()
        ta.reconcile(region)
        for game in region["past_matches"][0]["per_game"]:
            assert set(game) == {"The Huns", "Kaleido"}
        assert [g["The Huns"]["Cozen"]["k"] for g in region["past_matches"][0]["per_game"]] \
            == [11, 9]

    def test_upcoming_fixtures_are_rewritten(self):
        region = self.build()
        ta.reconcile(region)
        assert region["upcoming_matches"][0]["teamA"] == "The Huns"

    def test_the_whole_history_ends_up_under_one_name(self):
        """The point of the exercise: both matches now count as this team's."""
        region = self.build()
        ta.reconcile(region)
        counts = ta.match_counts(region["past_matches"])
        assert counts["The Huns"] == 2 and "The Huns Esports" not in counts

    def test_a_second_pass_changes_nothing(self):
        region = self.build()
        ta.reconcile(region)
        once = json.dumps(region, sort_keys=True)
        renames, counts = ta.reconcile(region)
        assert not renames and not counts
        assert json.dumps(region, sort_keys=True) == once

    def test_nothing_to_do_leaves_the_region_alone(self):
        region = {"teams": {"T1": team(["Faker"])},
                  "past_matches": [match("T1", "GEN")], "upcoming_matches": []}
        before = json.dumps(region, sort_keys=True)
        renames, counts = ta.reconcile(region)
        assert not renames and not counts
        assert json.dumps(region, sort_keys=True) == before

    def test_a_match_between_two_spellings_of_one_team_does_not_crash(self):
        """Nonsense in the source, but it collapses both sides onto one name
        and the stats blocks then collide."""
        region = {
            "teams": {"Misa": team(["a"]), "MISA": team(["a"])},
            "past_matches": [match("Misa", "MISA",
                                   actual={"Misa": {"a": {"k": 1}}, "MISA": {"a": {"k": 2}}},
                                   per_game=[{"Misa": {}, "MISA": {}}])],
            "upcoming_matches": [],
        }
        ta.reconcile(region)
        m = region["past_matches"][0]
        assert m["teamA"] == m["teamB"] == "MISA"
        assert set(m["actual"]) == {"MISA"}


class TestTheCommittedFile:
    """Run against the real cs2_data.json, because the shapes that made this
    necessary are ones nobody would have invented."""

    def region(self):
        path = REPO_ROOT / "cs2_data.json"
        if not path.exists():
            pytest.skip("no cs2_data.json committed")
        return json.loads(path.read_text())["regions"]["CS2"]

    def test_every_group_it_finds_shares_a_player(self):
        region = self.region()
        teams = region["teams"]
        groups = ta.alias_groups(teams, region["past_matches"])
        for canonical, aliases in groups.items():
            mine = {p["name"] for p in teams[canonical].get("players") or []}
            for alias in aliases:
                theirs = {p["name"] for p in teams[alias].get("players") or []}
                assert not theirs or not mine or (mine & theirs), \
                    f"{alias!r} folds into {canonical!r} with no player in common"

    def test_no_team_name_survives_that_it_meant_to_fold(self):
        region = self.region()
        renames, _ = ta.reconcile(region)
        assert renames, "the committed file has no duplicate spellings — has it been fixed?"
        remaining = set(region["teams"])
        assert not (remaining & set(renames)), \
            f"folded names still in the roster: {sorted(remaining & set(renames))}"
        for m in region["past_matches"] + region["upcoming_matches"]:
            for field in ta.NAME_FIELDS:
                assert m.get(field) not in renames, f"{field}={m.get(field)!r} not rewritten"
            for block in [m.get("actual")] + list(m.get("per_game") or []):
                if isinstance(block, dict):
                    assert not (set(block) & set(renames)), \
                        f"stats still keyed by a folded name: {sorted(set(block) & set(renames))}"

    def test_it_recovers_the_history_it_set_out_to(self):
        region = self.region()
        before = ta.match_counts(region["past_matches"])
        renames, _ = ta.reconcile(region)
        after = ta.match_counts(region["past_matches"])
        gained = {canonical: after[canonical] - before[canonical]
                  for canonical in set(renames.values())}
        # Not "every fold gains matches": folding a stub that only ever
        # appeared in a fixture list recovers a roster entry and no matches,
        # which is still worth doing. Losing matches is the failure.
        assert all(n >= 0 for n in gained.values()), \
            f"a fold LOST matches: {dict((k, v) for k, v in gained.items() if v < 0)}"
        assert sum(gained.values()) >= 20, \
            f"only {sum(gained.values())} match(es) recovered across {len(gained)} team(s)"
        assert sum(1 for n in gained.values() if n > 0) >= 15, \
            f"only {sum(1 for n in gained.values() if n > 0)} team(s) gained anything"

    def test_the_total_number_of_matches_is_unchanged(self):
        """A rewrite, not a merge of records: folding two spellings must not
        drop a match or invent one."""
        region = self.region()
        before = len(region["past_matches"])
        ta.reconcile(region)
        assert len(region["past_matches"]) == before
