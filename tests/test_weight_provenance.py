"""Every shipped weight is one that was validated AS A SET.

The hazard this exists for has a name in this repo's history: a weight
measured against one tier configuration is invalidated when another tier
changes underneath it. CS2's shrink was worth +9.85% with the career tier off
and +0.60% with it on -- the same number, two different meanings, and nothing
recorded which one the shipped value belonged to. The same shape appeared
twice more: a "+149.84% for career 1.0" that turned out to be an artifact of
the headshots career tier being locked out by `g[statKey] || 0`, and a
Valorant kp weight fitted around a parameter the scraper had written as a
literal 0 for every player.

The prose comments in src/app.jsx say all of that, and nothing can check a
comment. scripts/dev/validated_weights.json records the full weight set as it
stood when it was last validated, so changing any single weight without
re-validating makes the shipped set differ from a validated one and this goes
red -- naming the weight, which forces either a re-run or a revert.

Recording the SET rather than a per-weight list of companions is the point:
"which other weights were in force" is exactly "all of them", and a set either
was measured together or was not.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")  # diagnose_calibration imports the optimizer chain

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

import diagnose_calibration as dc

RECORD_PATH = REPO_ROOT / "scripts/dev/validated_weights.json"


@pytest.fixture(scope="module")
def shipped():
    return dc.shipped_weights()


@pytest.fixture(scope="module")
def recorded():
    return json.loads(RECORD_PATH.read_text())


def pairs(table):
    return {(game, stat) for game, stats in table.items() for stat in stats}


class TestEveryShippedSetHasProvenance:
    def test_the_record_exists_and_is_not_empty(self, recorded):
        assert recorded, f"{RECORD_PATH.name} is empty"

    def test_every_shipped_game_and_stat_is_recorded(self, shipped, recorded):
        missing = sorted(pairs(shipped) - pairs(recorded))
        assert not missing, (
            f"shipped with no provenance: {missing}. Add a record to "
            f"{RECORD_PATH.name} saying what was measured and how.")

    def test_no_record_survives_for_a_set_the_app_no_longer_ships(self, shipped, recorded):
        """A record for a stat that has gone is a claim about nothing, and it
        would keep looking like coverage."""
        stale = sorted(pairs(recorded) - pairs(shipped))
        assert not stale, f"provenance for weights the app does not ship: {stale}"


class TestTheShippedWeightsAreTheValidatedOnes:
    def test_every_weight_matches_the_set_it_was_measured_in(self, shipped, recorded):
        drifted = []
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                live = shipped.get(game, {}).get(stat) or {}
                for name, value in sorted(record["weights"].items()):
                    if live.get(name) != value:
                        drifted.append(
                            f"{game}/{stat}.{name}: ships {live.get(name)!r}, "
                            f"validated at {value!r}")
        assert not drifted, (
            "a weight changed without the validated set being re-recorded, so "
            "nothing behind the shipped model was measured against the model "
            "that is shipped:\n  " + "\n  ".join(drifted)
            + f"\n\nRe-run the validation, then update {RECORD_PATH.name} — or "
              f"revert the weight.")

    def test_the_recorded_set_covers_every_weight_the_app_has(self, shipped, recorded):
        """A record holding a subset would let a weight change unnoticed."""
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                live = set(shipped.get(game, {}).get(stat) or {})
                assert set(record["weights"]) == live, (
                    f"{game}/{stat}: recorded {sorted(record['weights'])}, "
                    f"the app has {sorted(live)}")


class TestEachRecordSaysSomething:
    def test_every_record_says_what_was_measured(self, recorded):
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                note = record.get("measured", "")
                assert len(note) > 30, (
                    f"{game}/{stat}: 'measured' is {note!r}. A set recorded "
                    f"without what it bought is a pin with no reason to keep it.")

    def test_a_reachable_record_names_a_date_and_a_command(self, recorded):
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                if record.get("unreachable"):
                    continue
                assert record.get("validated", "").count("-") == 2, \
                    f"{game}/{stat}: no ISO date in 'validated'"
                assert record.get("command", "").startswith("python "), \
                    f"{game}/{stat}: 'command' does not name a runnable script"

    def test_every_command_names_a_script_that_exists(self, recorded):
        """A reproduction line pointing at a renamed script is worse than
        none: it reads as checkable and is not."""
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                command = record.get("command")
                if not command:
                    continue
                script = command.split()[1]
                assert (REPO_ROOT / script).exists(), \
                    f"{game}/{stat}: {script} does not exist"

    def test_an_unreachable_set_claims_no_measurement(self, recorded):
        """headshots on LoL and Valorant cannot be selected — STAT_TYPES makes
        it CS2-only — so a validation date on one would be a fiction."""
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                if record.get("unreachable"):
                    assert "validated" not in record and "command" not in record, \
                        f"{game}/{stat}: marked unreachable but claims a measurement"

    def test_the_only_unreachable_sets_are_headshots_outside_cs2(self, recorded, shipped):
        unreachable = {(game, stat) for game, stats in recorded.items()
                       for stat, record in stats.items() if record.get("unreachable")}
        assert unreachable == {("lol", "headshots"), ("valorant", "headshots")}, \
            f"unexpected set marked unreachable: {sorted(unreachable)}"


class TestTheRecordAgreesWithThePythonPort:
    def test_the_optimizer_mirror_is_the_validated_set_too(self, recorded):
        """SHIPPED_WEIGHTS is hand-mirrored from src/app.jsx, and a
        measurement made with a stale mirror describes a model nobody runs.
        test_model_calibration pins the mirror to the app; this pins it to
        what was actually validated, so all three have to move together."""
        import optimize_weights as ow
        for game, stats in sorted(recorded.items()):
            for stat, record in sorted(stats.items()):
                mirrored = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
                assert mirrored is not None, f"{game}/{stat} missing from SHIPPED_WEIGHTS"
                for name, value in sorted(record["weights"].items()):
                    assert mirrored.get(name) == value, (
                        f"{game}/{stat}.{name}: optimize_weights says "
                        f"{mirrored.get(name)!r}, validated at {value!r}")
