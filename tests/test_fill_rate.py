"""How much of the posted record we can actually grade.

This is the number the scraping work is steered by -- "keep going until
it stops improving" is a decision made off this one figure -- so what it
counts has to be right, and the split that matters most is the cheapest
to get wrong: a fixture that has not been played yet is not a fixture we
failed to scrape.

It was being split on the CALENDAR. A fixture at 20:00 today counted as
settleable at 03:00 today, so 325 lines for matches that did not exist
yet sat in "what another scrape buys". The headline read 62.9% when the
honest figure against fixtures that had actually happened was 90.9%, and
it made the number look stuck while nothing was wrong.
"""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "fill_rate", ROOT / "scripts" / "dev" / "fill_rate.py")
fill_rate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fill_rate)

import score_props as sp  # noqa: E402


def at(offset_hours):
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=offset_hours)
    return when.isoformat()


def line(start_time, team="A", player="p", stat="kills", maps=2, value=8.5):
    return {"game": "cs2", "team": team, "player": player, "stat": stat,
            "maps": maps, "line": value, "start_time": start_time}


def run(tmp_path, monkeypatch, rows, data=None, capsys=None):
    """The real main(), against files in a temp directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "props_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    (tmp_path / "cs2_data.json").write_text(json.dumps(data or {"regions": {}}))
    fill_rate.main()
    return capsys.readouterr().out


def counted(output, label):
    for row in output.splitlines():
        if label in row:
            return int(row.split(label)[1].split()[0])
    raise AssertionError(f"{label!r} not in output:\n{output}")


class TestNotPlayedYet:
    def test_a_fixture_later_today_is_not_something_a_scrape_could_buy(
            self, tmp_path, monkeypatch, capsys):
        """The whole bug, at its smallest. Same calendar day, still in
        the future."""
        out = run(tmp_path, monkeypatch, [line(at(+6))], capsys=capsys)
        assert counted(out, "fixture not played yet") == 1
        assert counted(out, "settleable now") == 0

    def test_a_fixture_earlier_today_is(self, tmp_path, monkeypatch, capsys):
        out = run(tmp_path, monkeypatch, [line(at(-6))], capsys=capsys)
        assert counted(out, "fixture not played yet") == 0
        assert counted(out, "settleable now") == 1

    def test_tomorrow_is_still_not_played(self, tmp_path, monkeypatch, capsys):
        out = run(tmp_path, monkeypatch, [line(at(+30))], capsys=capsys)
        assert counted(out, "fixture not played yet") == 1

    def test_yesterday_is_still_settleable(self, tmp_path, monkeypatch, capsys):
        out = run(tmp_path, monkeypatch, [line(at(-30))], capsys=capsys)
        assert counted(out, "settleable now") == 1

    def test_an_unreadable_start_time_is_counted_not_excused(
            self, tmp_path, monkeypatch, capsys):
        """Treating it as "not played yet" would hide a malformed row in
        the one bucket nobody looks at. It belongs in the refusals."""
        out = run(tmp_path, monkeypatch, [line("not a timestamp")], capsys=capsys)
        assert counted(out, "fixture not played yet") == 0
        assert counted(out, "settleable now") == 1


class TestTheSplitAddsUp:
    def test_every_settleable_line_lands_in_exactly_one_bucket(
            self, tmp_path, monkeypatch, capsys):
        rows = [line(at(-6), player=f"p{i}") for i in range(5)] + [line(at(+6))]
        out = run(tmp_path, monkeypatch, rows, capsys=capsys)
        settleable = counted(out, "settleable now")
        total = (counted(out, "graded") + counted(out, "ungradeable by nature")
                 + counted(out, "still recoverable"))
        assert settleable == total == 5
