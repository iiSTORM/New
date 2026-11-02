"""Oracle's Elixir: finding the files, trimming them, and reading them point in time."""
import csv
import gzip
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

pytest.importorskip("requests")
import scrape_oracles_elixir as oe  # noqa: E402
import oracles_elixir_probe as probe  # noqa: E402

FOLDER = """
<div class="flip-entry" id="entry-1AbC_2026xyz" tabindex="0" role="link">
  <div class="flip-entry-info"><a href="https://drive.google.com/file/d/1AbC_2026xyz/view">
  <div class="flip-entry-title">2026_LoL_esports_match_data_from_OraclesElixir.csv</div></a></div></div>
<div class="flip-entry" id="entry-9zz-2025" tabindex="0" role="link">
  <div class="flip-entry-info"><div class="flip-entry-title">2025_LoL_esports_match_data_from_OraclesElixir.csv</div></div></div>
<div class="flip-entry" id="entry-readme1" tabindex="0" role="link">
  <div class="flip-entry-title">README.txt</div></div>
"""


def test_the_folder_listing_finds_each_years_file_by_name():
    assert oe.parse_folder(FOLDER) == {"2026": "1AbC_2026xyz", "2025": "9zz-2025"}
    assert oe.parse_folder("<html>changed</html>") == {}


def test_only_tracked_leagues_and_named_columns_are_kept():
    src = io.StringIO("gameid,league,date,kills,junk\n"
                      "g1,LCK,2026-05-01,3,x\ng2,LJL,2026-05-01,9,y\ng3,WLDs,2026-10-20,5,z\n")
    header, rows = oe.filter_rows(src, columns=["gameid", "league", "date", "kills", "missing"])
    assert header == ["gameid", "league", "date", "kills"]
    assert rows == [["g1", "LCK", "2026-05-01", "3"], ["g3", "WLDs", "2026-10-20", "5"]]


def test_features_only_see_games_before_the_prop(tmp_path):
    path = tmp_path / "oe.csv.gz"
    cols = ["date", "position", "playername", "teamname", "kills", "teamkills", "golddiffat15",
            "ckpm", "gamelength", "team kpm"]
    with gzip.open(path, "wt") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for day in range(1, 9):
            gd = 100 * day
            w.writerow([f"2026-05-{day:02d}", "mid", "Faker", "T1", 4, 16, gd, "", "", ""])
            w.writerow([f"2026-05-{day:02d}", "team", "", "T1", "", 16, gd, 0.9, 1800, 0.5])
            w.writerow([f"2026-05-{day:02d}", "team", "", "Gen.G", "", 12, -gd, 0.9, 1800, 0.4])
    players, teams = probe.load(str(path))
    got = probe.features_for(players, teams, "Faker", "T1", "Gen.G", "2026-05-07")
    assert got["player_golddiffat15"] == pytest.approx(350)      # days 1-6 only
    assert got["player_kill_share"] == pytest.approx(0.25)
    assert got["pace_both"] == pytest.approx(27.0)               # 0.9 kpm x 30 min
    assert probe.features_for(players, teams, "Faker", "T1", "Gen.G", "2026-05-03") == {}
