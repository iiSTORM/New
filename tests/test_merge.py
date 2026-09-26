"""Tests for the LoL merge step.

The team-name mapping is hand-maintained knowledge about two sources that
spell the same team differently (gol.gg vs the LoL Esports API). A wrong
entry does not crash anything — it silently drops a fixture from the
upcoming list — so it is worth pinning down.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import merge


class TestNormalize:
    def test_maps_a_known_alias_to_the_golgg_spelling(self):
        assert merge.normalize("Cloud9 Kia") == "Cloud9"

    def test_is_case_insensitive(self):
        assert merge.normalize("CLOUD9 KIA") == "Cloud9"
        assert merge.normalize("cloud9 kia") == "Cloud9"

    def test_passes_through_a_name_it_does_not_know(self):
        assert merge.normalize("T1") == "T1"

    def test_every_mapping_target_is_stable(self):
        """Mapping to another key would mean a name normalizes in two hops
        depending on which spelling arrived first."""
        for alias, target in merge.TEAM_NAME_MAP.items():
            assert merge.normalize(target) == target, (
                f"{alias!r} maps to {target!r}, which is itself remapped"
            )


class TestBuildLookup:
    def test_resolves_api_casing_to_golgg_casing(self):
        lookup = merge.build_lookup({"Kiwoom DRX", "T1"})
        assert lookup["kiwoom drx"] == "Kiwoom DRX"

    def test_empty_roster_gives_empty_lookup(self):
        assert merge.build_lookup(set()) == {}


class TestLoadSchedule:
    """scrape_schedule.py is allowed to fail, so merge.py falls back to
    whatever schedule.json is on disk. It must not fall back to an old one:
    those fixtures have already been played."""

    @staticmethod
    def _write(tmp_path, payload):
        (tmp_path / "schedule.json").write_text(json.dumps(payload))

    def test_uses_a_fresh_schedule(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        self._write(tmp_path, {"generated_at": now, "regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule()["regions"]["LCS"] == [{"teamA": "T1"}]

    def test_drops_a_schedule_past_the_age_limit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=merge.MAX_SCHEDULE_AGE_DAYS + 1)
        self._write(tmp_path, {"generated_at": old.isoformat(), "regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule() == {"regions": {}}

    def test_keeps_a_schedule_just_inside_the_age_limit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        recent = datetime.now(timezone.utc) - timedelta(days=merge.MAX_SCHEDULE_AGE_DAYS - 0.5)
        self._write(tmp_path, {"generated_at": recent.isoformat(), "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}

    def test_uses_an_unstamped_file_rather_than_regressing(self, tmp_path, monkeypatch):
        """Files written before generated_at existed must keep working."""
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, {"regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule()["regions"]["LCS"] == [{"teamA": "T1"}]

    def test_tolerates_an_unparseable_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, {"generated_at": "last tuesday", "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}

    def test_missing_file_is_an_empty_schedule(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert merge.load_schedule() == {"regions": {}}

    def test_naive_timestamp_is_treated_as_utc(self, tmp_path, monkeypatch):
        """Not currently written, but must not raise if it ever is."""
        monkeypatch.chdir(tmp_path)
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._write(tmp_path, {"generated_at": naive, "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}


class TestMergeEndToEnd:
    """Runs merge.py the way the workflow does — as a script, against files
    in the working directory — rather than poking at its internals."""

    @staticmethod
    def _fixture(tmp_path, schedule_age_days=0.0):
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regions": {
                "LCS": {
                    "teams": {"Cloud9": {"color": "#fff",
                                         "players": [{"name": "p1", "role": "TOP",
                                                      "cur": {}, "hist": {}}]}},
                    "past_matches": [],
                    "upcoming_matches": [],
                }
            },
        }
        (tmp_path / "data.json").write_text(json.dumps(data))
        stamp = datetime.now(timezone.utc) - timedelta(days=schedule_age_days)
        (tmp_path / "schedule.json").write_text(json.dumps({
            "generated_at": stamp.isoformat(),
            # "Cloud9 Kia" is the API spelling; it must resolve to gol.gg's "Cloud9".
            "regions": {"LCS": [{"teamA": "Cloud9 Kia", "teamB": "Cloud9 Kia",
                                 "date": "2026-09-20", "week": "W1"}]},
        }))
        (tmp_path / "career_data.json").write_text(json.dumps({}))

    def test_writes_minified_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path)
        merge.main()
        raw = (tmp_path / "data.json").read_bytes()
        assert b"\n  " not in raw, "merge.py should write minified JSON"
        json.loads(raw)  # still valid

    def test_resolves_api_team_names_into_upcoming(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path)
        merge.main()
        merged = json.loads((tmp_path / "data.json").read_text())
        assert len(merged["regions"]["LCS"]["upcoming_matches"]) == 1

    def test_stale_schedule_yields_no_upcoming(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path, schedule_age_days=merge.MAX_SCHEDULE_AGE_DAYS + 1)
        merge.main()
        merged = json.loads((tmp_path / "data.json").read_text())
        assert merged["regions"]["LCS"]["upcoming_matches"] == []


class TestAnUndecidedOpponentIsStillAFixture:
    """One side decided and the other a bracket slot is a real fixture.

    LYON played at 20:00 on 2026-09-26 against a slot that had not
    resolved, and the provider posted 13 maps 1-3 lines on their
    players. Every one had nowhere to land, because the merge required
    BOTH sides to resolve -- during playoffs, which is exactly when half
    a bracket is undecided and when the lines are busiest. Two LCS teams
    accounted for 26 of the 70 LoL lines on that board.

    The app was already built for this: collectEdges needs only the
    player's own team rostered and falls back to a neutral opponent term,
    deliberately, because a CS2 board stranded five lines the same way.
    """

    @staticmethod
    def _merge(schedule_rows, known_teams):
        """The real resolver, through the real lookup builder."""
        upcoming, _ = merge.resolve_upcoming(
            schedule_rows, merge.build_lookup(set(known_teams)), "LCS")
        return upcoming

    @staticmethod
    def _counts(schedule_rows, known_teams):
        _, counts = merge.resolve_upcoming(
            schedule_rows, merge.build_lookup(set(known_teams)), "LCS")
        return counts

    def row(self, a, b, date="2026-09-26T20:00:00Z"):
        return {"date": date, "teamA": a, "teamB": b, "block": "Playoffs"}

    def test_a_tbd_opponent_keeps_the_fixture(self):
        got = self._merge([self.row("TBD", "LYON")], ["LYON"])
        assert len(got) == 1
        assert got[0]["teamB"] == "LYON" and got[0]["teamA"] == "TBD"

    def test_either_side_works(self):
        got = self._merge([self.row("LYON", "TBD")], ["LYON"])
        assert len(got) == 1 and got[0]["teamA"] == "LYON" and got[0]["teamB"] == "TBD"

    def test_tbd_against_tbd_is_still_dropped(self):
        """No side to project, so nothing to show."""
        assert self._merge([self.row("TBD", "TBD")], ["LYON"]) == []

    def test_a_blank_side_counts_as_undecided(self):
        got = self._merge([self.row("", "LYON")], ["LYON"])
        assert len(got) == 1 and got[0]["teamA"] == "TBD"

    def test_an_UNRECOGNISED_named_team_is_still_dropped_and_reported(self):
        """The important boundary. A real team this scraper failed to
        resolve must NOT be relabelled TBD and quietly kept -- that
        hides a name-mapping bug behind a feature."""
        assert self._merge([self.row("Some Real Team", "LYON")], ["LYON"]) == []

    def test_a_fully_resolved_fixture_is_unaffected(self):
        got = self._merge([self.row("LYON", "Cloud9")], ["LYON", "Cloud9"])
        assert len(got) == 1 and got[0]["teamA"] == "LYON" and got[0]["teamB"] == "Cloud9"

    def test_the_outcomes_are_counted_separately(self):
        """A playoff bracket full of undecided slots must not read the same as
        a scraper that stopped recognising team names -- and neither must a
        team this app simply does not cover. All three were 'dropped' once,
        and the middle one is the only bug among them."""
        counts = self._counts([
            self.row("TBD", "LYON"),             # kept, one side decided
            self.row("TBD", "TBD"),              # dropped, nothing to show
            self.row("Some Real Team", "LYON"),  # dropped, not tracked anywhere
            self.row("LYON", "Cloud9"),          # fully resolved
        ], ["LYON", "Cloud9"])
        assert counts == {"half": 1, "tbd": 1, "uncovered": 1, "unknown": 0}

    def test_one_uncovered_side_does_not_excuse_a_real_bug_on_the_other(self):
        """Classified on ALL of a row's unresolved sides, not any of them.
        A fixture between a qualifier team we do not cover and a name we do
        track one region over is still a name to look at, and reading the
        row as a coverage gap is how it would stop being reported."""
        elsewhere = merge.tracked_by_key({"LCK": {"teams": {"T1": {}}}})
        teams = {"LYON", "Cloud9"}
        _, counts = merge.resolve_upcoming(
            [self.row("Maryville University", "T1")], merge.build_lookup(teams),
            "LCS", merge.build_alias_lookup(teams), elsewhere)
        assert counts["unknown"] == 1 and counts["uncovered"] == 0


class TestResolvingASide:
    """The three tries, and what each unresolved case is called.

    Eight names came out of one real merge run as "UNKNOWN TEAM", which read
    as eight missing TEAM_NAME_MAP entries. Every one was an LCS Regional
    Qualifier or an LLA/CBLOL-Academy side that gol.gg's league pages do not
    list at all, so no entry could have pointed anywhere. A count that cannot
    tell a coverage gap from a mapping bug sends you to fix the wrong file.
    """

    LCS = ["Cloud9", "Team Liquid", "LYON", "Nongshim RedForce"]

    def resolve(self, name, teams=None, elsewhere=None):
        teams = set(teams or self.LCS)
        return merge.resolve_side(name, merge.build_lookup(teams),
                                  merge.build_alias_lookup(teams), elsewhere)

    def test_an_exact_name_resolves(self):
        assert self.resolve("Cloud9") == ("Cloud9", None)

    def test_case_only_resolves(self):
        assert self.resolve("CLOUD9") == ("Cloud9", None)

    def test_the_name_map_still_resolves_a_sponsor_suffix(self):
        assert self.resolve("Cloud9 Kia") == ("Cloud9", None)
        assert self.resolve("Team Liquid Alienware") == ("Team Liquid", None)

    @pytest.mark.parametrize("api_name,golgg_name", [
        ("Gen.G Esports", "Gen.G"),
        ("Nongshim Red Force", "Nongshim RedForce"),
        ("Thunder Talk Gaming", "ThunderTalk Gaming"),
        ("Anyone's Legend", "Anyone s Legend"),
        ("LEVIATÁN", "Leviatan"),
    ])
    def test_a_pure_spelling_difference_needs_no_map_entry(self, api_name, golgg_name):
        """Each of these HAD an entry. They are punctuation, spacing and an
        accent, which the alias fallback reduces away -- and it resolves the
        accent whichever way gol.gg happens to write it, where the entry was
        a guess inferred from a page title."""
        assert golgg_name.lower() not in merge.TEAM_NAME_MAP, \
            f"{golgg_name} still has a hand-maintained entry"
        assert api_name.lower() not in merge.TEAM_NAME_MAP
        assert self.resolve(api_name, teams=[golgg_name]) == (golgg_name, None)

    def test_a_team_tracked_nowhere_is_a_coverage_gap(self):
        name, why = self.resolve("Maryville University")
        assert name is None and why == merge.UNCOVERED

    def test_a_team_tracked_in_another_region_says_which(self):
        elsewhere = merge.tracked_by_key({"LCK": {"teams": {"T1": {}}}})
        name, why = self.resolve("T1", elsewhere=elsewhere)
        assert name is None and merge.ELSEWHERE in why and "LCK/T1" in why

    def test_two_tracked_teams_sharing_a_spelling_are_not_guessed_between(self):
        name, why = self.resolve("pain", teams=["paiN", "PAIN"])
        assert name is None and why == merge.AMBIGUOUS

    def test_a_name_with_no_letters_at_all_is_a_coverage_gap(self):
        assert self.resolve("!!!") == (None, merge.UNCOVERED)


class TestBuildLookup:
    def test_resolves_api_casing_to_golggs(self):
        assert merge.build_lookup({"Kiwoom DRX"})["kiwoom drx"] == "Kiwoom DRX"

    def test_leaves_out_two_teams_differing_only_in_case(self):
        """Answered with whichever was iterated last before."""
        assert merge.build_lookup({"paiN", "PAIN"}) == {}


class TestBuildAliasLookup:
    def test_maps_a_spelling_key_to_its_team(self):
        assert merge.build_alias_lookup({"Gen.G"})["geng"] == "Gen.G"

    def test_a_key_two_teams_share_resolves_to_neither(self):
        """Resolving to either would be the normalisation collision the
        fallback exists to avoid. Recorded as None rather than omitted, so
        "several teams look like this" can be told from "none does"."""
        assert merge.build_alias_lookup({"paiN", "PAIN"}) == {"pain": None}

    def test_leaves_out_a_name_that_reduces_to_nothing(self):
        assert merge.build_alias_lookup({"Esports"}) == {}
