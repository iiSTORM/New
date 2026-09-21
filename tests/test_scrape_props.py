"""The props pipeline end to end: provider payload -> props.json.

tests/test_props_match.py covers the matching logic in isolation. What was
left untested is everything either side of it: pulling (player, stat, line)
out of the provider's JSON:API shape, and the writing decisions main() makes
afterwards. Both are where a live run actually breaks -- the provider is
unreachable from CI and from any datacenter, so a fixture is the only way
these run at all, and an untested parser between two tested halves is where
a shape change would land silently.

The fixture is a trimmed copy of the real payload; every projection in it
exercises one branch, keyed by id so the assertions below can name them.
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import props_match as pm
import scrape_props as sp

FIXTURE = Path(__file__).parent / "fixtures" / "prizepicks_projections.json"

# Which included-player id stands in for which game, so the end-to-end test
# can swap in handles that are actually on today's rosters. The other ids in
# the fixture (a retired player, an NBA player, a dangling reference) are
# there to NOT match and are left alone.
MATCHABLE = {"201": "lol", "202": "lol",
             "203": "cs2", "204": "cs2",
             "205": "valorant", "206": "valorant"}


@pytest.fixture
def payload():
    with open(FIXTURE) as f:
        return json.load(f)


def by_id(props, payload, projection_id):
    """The parsed prop that came from a given fixture projection."""
    wanted = next(p for p in payload["data"] if p["id"] == projection_id)
    label = wanted["attributes"]["stat_type"]
    line = wanted["attributes"]["line_score"]
    return next(p for p in props
                if p["stat_label"] == label and p["line"] == line)


class TestFetch:
    """The request wrapper. It cannot be pointed at the real endpoint from
    here, but every way that endpoint says no is a branch that decides
    whether props.json gets overwritten, so each one is worth pinning.

    The 403 in particular is not an edge case: it is what CI gets on every
    single run, which makes its message the most-read line in this file.
    """

    class Resp:
        def __init__(self, status=200, payload=None, bad_json=False):
            self.status_code, self._payload, self._bad = status, payload, bad_json

        def json(self):
            if self._bad:
                raise ValueError("not JSON")
            return self._payload

    def session(self, resp=None, raises=None):
        class S:
            def get(inner, *a, **kw):
                if raises is not None:
                    raise raises
                return resp
        return S()

    def test_a_good_response_comes_back_whole(self):
        payload = {"data": [], "included": []}
        assert sp.fetch_prizepicks_payload(
            self.session(self.Resp(200, payload))) == payload

    def test_403_returns_nothing_rather_than_a_half_answer(self, capsys):
        assert sp.fetch_prizepicks_payload(self.session(self.Resp(403))) is None
        assert "403" in capsys.readouterr().err

    def test_the_403_message_does_not_send_anyone_to_another_network(self, capsys):
        """This message told people to run it from a machine at home until
        a machine at home produced this exact 403 while its own browser
        fetched the payload fine. The block is on the client, so a different
        network changes nothing, and saying otherwise costs someone an
        evening. It has to name the route that does work instead."""
        sp.fetch_prizepicks_payload(self.session(self.Resp(403)))
        err = capsys.readouterr().err
        assert "--fixture -" in err, "the working route is not in the message"
        assert "network will not help" in err
        for misleading in ("machine at home", "datacenter IP", "run this locally"):
            assert misleading not in err, f"still advising {misleading!r}"

    def test_an_unexpected_status_is_reported(self, capsys):
        assert sp.fetch_prizepicks_payload(self.session(self.Resp(500))) is None
        assert "500" in capsys.readouterr().err

    def test_a_body_that_is_not_json_is_not_an_exception(self, capsys):
        assert sp.fetch_prizepicks_payload(
            self.session(self.Resp(200, bad_json=True))) is None
        assert "not JSON" in capsys.readouterr().err

    def test_a_dead_connection_is_not_an_exception(self, capsys):
        """Returning None puts this down the same path as a 403 — no write,
        non-zero exit — instead of a traceback mid-run."""
        import requests
        assert sp.fetch_prizepicks_payload(
            self.session(raises=requests.RequestException("boom"))) is None
        assert "request failed" in capsys.readouterr().err


class TestParsePrizepicks:
    """Payload -> raw props. Pure shape work, no rosters involved."""

    def test_joins_projections_to_their_player(self, payload):
        props = sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])
        prop = by_id(props, payload, "1001")
        assert prop["player_name"] == "Berserker"
        assert prop["team"] == "LYON"
        assert prop["stat_label"] == "MAPS 1-2 Kills"
        assert prop["line"] == 4.5
        assert prop["provider"] == "prizepicks"
        assert prop["start_time"] == "2026-09-20T18:00:00-04:00"

    def test_accepts_both_player_types(self, payload):
        """The endpoint has returned the roster under both `new_player` and
        `player`. Reading only one of them would drop a whole game's lines
        the day it changed, and look exactly like a quiet slate."""
        included = {item["id"]: item["type"] for item in payload["included"]}
        assert included["205"] == "player" and included["206"] == "new_player"
        names = {p["player_name"] for p in sp.parse_prizepicks(payload, sp.GAMES["valorant"]["leagues"])}
        assert {"Neon", "eeiu"} <= names

    def test_filters_to_the_requested_league(self, payload):
        """One request covers every sport the provider posts. Matching is by
        handle, and handles are short -- an NBA player reaching the LoL
        roster index is a real way to invent a line for the wrong person."""
        for game in sp.GAMES:
            names = {p["player_name"]
                     for p in sp.parse_prizepicks(payload, sp.GAMES[game]["leagues"])}
            assert "LeBron James" not in names

    def test_each_league_gets_only_its_own(self, payload):
        lol = {p["player_name"] for p in sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])}
        cs2 = {p["player_name"] for p in sp.parse_prizepicks(payload, sp.GAMES["cs2"]["leagues"])}
        assert "Berserker" in lol and "Berserker" not in cs2
        assert "FalleN" in cs2 and "FalleN" not in lol

    def test_ignores_non_projection_entries(self, payload):
        """`data` carries scores and other types alongside projections."""
        assert any(item["type"] != "projection" for item in payload["data"])
        props = sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])
        assert not any(p["line"] == 99.5 for p in props)

    def test_a_dangling_player_reference_does_not_raise(self, payload):
        """Projection 1012 points at a player id that is not in `included`.

        With no player there is no league either, so it cannot be attributed
        to a game and is excluded from every one of them. That is a change
        for the better: under a loose league rule it fell through the filter
        and was counted as "player not on any roster" once per game, turning
        one unattributable row into three complaints. What has to hold
        either way is that looking it up does not raise mid-payload.
        """
        for game in sp.GAMES:
            props = sp.parse_prizepicks(payload, sp.GAMES[game]["leagues"])
            assert not any(p["player_name"] is None for p in props), game

        # With no league filter it still comes through nameless, which is
        # how the inspector sees it and how the matcher reports it.
        nameless = [p for p in sp.parse_prizepicks(payload, None)
                    if p["player_name"] is None]
        assert len(nameless) == 1
        _, unmatched = pm.match_props(nameless, {})
        assert unmatched[0]["reason"] == "player not on any roster"

    def test_keeps_lines_it_cannot_read_for_the_matcher_to_refuse(self, payload):
        """Parsing does not judge values -- "n/a" is passed along so the
        refusal is counted and printed in one place."""
        props = sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])
        assert any(p["line"] == "n/a" for p in props)

    def test_a_season_long_variant_is_not_swallowed(self, payload):
        """The provider posts season-long and part-period products under
        suffixed names -- NBASZN, NFL1H, WNBA1Q are all in a real payload.
        Projection 1013 is a LoLSZN line on a player who IS on the roster,
        so nothing downstream would reject it: a season kills total would
        be compared against a per-series projection and read as a colossal
        edge. Matching the league loosely is what lets that through."""
        props = sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])
        assert not any(p["line"] == 320.5 for p in props), \
            "a LoLSZN line reached the LoL props"

    def test_both_spellings_of_a_league_are_accepted(self):
        """The provider has used the long form and currently uses the short
        one, and which you get should not decide whether the game works."""
        def payload_in(league):
            return {"data": [{"type": "projection", "id": "1", "attributes":
                              {"stat_type": "MAPS 1-2 Kills", "line_score": 4.5},
                              "relationships": {"new_player": {"data": {"id": "9"}}}}],
                    "included": [{"type": "new_player", "id": "9", "attributes":
                                  {"display_name": "X", "league": league}}]}
        for league in ("LoL", "League of Legends"):
            assert sp.parse_prizepicks(payload_in(league),
                                       sp.GAMES["lol"]["leagues"]), league
        for league in ("VAL", "VALORANT"):
            assert sp.parse_prizepicks(payload_in(league),
                                       sp.GAMES["valorant"]["leagues"]), league

    def test_league_matching_ignores_case_and_padding(self):
        """Exact does not mean brittle about whitespace or capitalisation."""
        payload = {"data": [{"type": "projection", "id": "1", "attributes":
                             {"stat_type": "MAPS 1-2 Kills", "line_score": 4.5},
                             "relationships": {"new_player": {"data": {"id": "9"}}}}],
                   "included": [{"type": "new_player", "id": "9", "attributes":
                                 {"display_name": "X", "league": "  lol  "}}]}
        assert sp.parse_prizepicks(payload, sp.GAMES["lol"]["leagues"])

    @pytest.mark.parametrize("junk", [{}, {"data": None, "included": None},
                                      {"data": [], "included": []}])
    def test_an_empty_payload_is_not_an_exception(self, junk):
        assert sp.parse_prizepicks(junk, sp.GAMES["cs2"]["leagues"]) == []


def current_handles(game, count):
    """Real, unambiguous handles off the committed roster for a game."""
    data_file = REPO_ROOT / sp.GAMES[game]["data"]
    if not data_file.exists():
        pytest.skip(f"{data_file.name} not present")
    with open(data_file) as f:
        index, _ = pm.build_roster_index(json.load(f).get("regions", {}))
    names = sorted({e[2] for entries in index.values() for e in entries})
    if len(names) < count:
        pytest.skip(f"{data_file.name} has too few players to test against")
    return names[:count]


@pytest.fixture
def live_payload(payload):
    """The fixture with its matchable handles swapped for rostered ones.

    Rosters churn every split. Hardcoding a player here would mean this test
    starts failing the day he is benched, for a reason that has nothing to do
    with the pipeline -- so the names come off the roster files at run time
    and the fixture keeps its structure.
    """
    per_game = {}
    for item in payload["included"]:
        game = MATCHABLE.get(item["id"])
        if game is None:
            continue
        pool = per_game.setdefault(game, current_handles(game, 2))
        item["attributes"]["display_name"] = pool.pop(0)
    return payload


def run(monkeypatch, argv, stdin=None):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(sys, "argv", ["scrape_props.py"] + argv)
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return sp.main()


class TestEndToEnd:
    """Payload in, props.json out, through the real main()."""

    def test_writes_the_shape_the_frontend_reads(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        out = tmp_path / "props.json"

        assert run(monkeypatch, ["--fixture", str(saved), "--out", str(out)]) == 0

        written = json.loads(out.read_text())
        datetime.fromisoformat(written["fetched_at"])  # raises if malformed
        assert written["source"] == "prizepicks"
        # All three games, because a payload covering three and a props.json
        # covering one is the shape of a silently half-broken run.
        assert set(written["props"]) == {"lol", "cs2", "valorant"}

        total = 0
        for game, players in written["props"].items():
            for name, props in players.items():
                assert props, f"{game}/{name} has an empty list"
                for prop in props:
                    # propFor() in src/app.jsx looks up props[game][name] and
                    # then filters on these three fields. maps must stay an
                    # int: it is compared with === against the games-in-series
                    # selector, and "2" would never equal 2.
                    assert prop["player"] == name
                    assert prop["stat"] in ("kills", "deaths", "assists")
                    assert isinstance(prop["maps"], int)
                    assert isinstance(prop["line"], float)
                    total += 1
        assert total == 6

    def test_stdin_is_the_same_route_as_a_file(self, tmp_path, monkeypatch, live_payload):
        """The paste-from-a-browser route has to land on the identical
        parser and matcher, or the only usable route is the untested one."""
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        from_file, from_stdin = tmp_path / "a.json", tmp_path / "b.json"

        run(monkeypatch, ["--fixture", str(saved), "--out", str(from_file)])
        run(monkeypatch, ["--fixture", "-", "--out", str(from_stdin)],
            stdin=json.dumps(live_payload))

        a, b = json.loads(from_file.read_text()), json.loads(from_stdin.read_text())
        assert a["props"] == b["props"]

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        out = tmp_path / "props.json"
        assert run(monkeypatch, ["--fixture", str(saved), "--out", str(out),
                                 "--dry-run"]) == 0
        assert not out.exists()


class TestFetchedAt:
    """When the lines came off the provider, which is not when this ran.

    The frontend refuses to compute an edge against a line older than 90
    minutes. That protection is only as good as the timestamp: stamping the
    run time on a payload saved two hours ago tells it a stale line is
    seconds old, and it draws an edge instead of greying it out. That is
    the precise failure the window exists to prevent, so it is worth a test
    rather than a comment.
    """

    def test_a_saved_payload_is_stamped_when_it_was_saved(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        two_hours_ago = time.time() - 2 * 3600
        os.utime(saved, (two_hours_ago, two_hours_ago))
        out = tmp_path / "props.json"

        run(monkeypatch, ["--fixture", str(saved), "--out", str(out)])

        stamped = datetime.fromisoformat(json.loads(out.read_text())["fetched_at"])
        age = (datetime.now(timezone.utc) - stamped).total_seconds() / 60
        assert 110 < age < 130, f"stamped {age:.0f} minutes old, expected ~120"

    def test_an_old_payload_says_so(self, tmp_path, monkeypatch, capsys, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        old = time.time() - 3 * 3600
        os.utime(saved, (old, old))

        run(monkeypatch, ["--fixture", str(saved), "--out", str(tmp_path / "p.json")])
        err = capsys.readouterr().err
        assert "180 minutes old" in err and "90-minute window" in err

    def test_a_fresh_payload_does_not_warn(self, tmp_path, monkeypatch, capsys, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        run(monkeypatch, ["--fixture", str(saved), "--out", str(tmp_path / "p.json")])
        assert "minutes old" not in capsys.readouterr().err

    def test_stdin_falls_back_to_now(self, monkeypatch, live_payload, tmp_path):
        """A pipe has no mtime to read, so now is the only answer available."""
        before = datetime.now(timezone.utc)
        run(monkeypatch, ["--fixture", "-", "--out", str(tmp_path / "p.json")],
            stdin=json.dumps(live_payload))
        stamped = datetime.fromisoformat(
            json.loads((tmp_path / "p.json").read_text())["fetched_at"])
        assert stamped >= before.replace(microsecond=0)


class TestSeveralPayloads:
    """One file per league is the ordinary case now.

    Filtering by league_id turns a 42MB board covering every sport into a
    few hundred KB per game, so the payload arrives as three small files
    rather than one huge one.
    """

    def split(self, payload):
        """The fixture cut into one payload per league, as saving three
        filtered URLs from a browser would produce."""
        leagues = {i["id"]: (i.get("attributes") or {}).get("league")
                   for i in payload["included"]
                   if i.get("type") in ("new_player", "player")}
        out = {}
        for item in payload["data"]:
            if item.get("type") != "projection":
                continue
            rel = ((item.get("relationships") or {}).get("new_player") or {}).get("data") or {}
            league = leagues.get(str(rel.get("id")))
            if league:
                out.setdefault(league, {"data": [], "included": []})["data"].append(item)
        for item in payload["included"]:
            league = (item.get("attributes") or {}).get("league")
            if league in out:
                out[league]["included"].append(item)
        return out

    def test_several_files_give_the_same_result_as_one(self, tmp_path, monkeypatch, live_payload):
        whole = tmp_path / "whole.json"
        whole.write_text(json.dumps(live_payload))
        one_out = tmp_path / "one.json"
        run(monkeypatch, ["--fixture", str(whole), "--out", str(one_out)])

        parts = []
        for league, payload in self.split(json.loads(whole.read_text())).items():
            path = tmp_path / f"{league}.json"
            path.write_text(json.dumps(payload))
            parts.append(str(path))
        many_out = tmp_path / "many.json"

        assert run(monkeypatch, ["--fixture"] + parts + ["--out", str(many_out)]) == 0
        assert json.loads(many_out.read_text())["props"] == json.loads(one_out.read_text())["props"]

    def test_the_stalest_file_sets_the_age(self, tmp_path, monkeypatch, live_payload):
        """A set of files is only as fresh as its oldest member. Taking the
        newest would let one freshly-saved league present two-hour-old lines
        from another as current, which is the whole failure the window
        exists to prevent."""
        parts = []
        for i, (league, payload) in enumerate(self.split(live_payload).items()):
            path = tmp_path / f"{league}.json"
            path.write_text(json.dumps(payload))
            # One file three hours old, the rest saved just now.
            if i == 0:
                old = time.time() - 3 * 3600
                os.utime(path, (old, old))
            parts.append(str(path))
        out = tmp_path / "props.json"

        run(monkeypatch, ["--fixture"] + parts + ["--out", str(out)])
        stamped = datetime.fromisoformat(json.loads(out.read_text())["fetched_at"])
        age = (datetime.now(timezone.utc) - stamped).total_seconds() / 60
        assert age > 170, f"stamped {age:.0f} minutes old, expected ~180"

    def test_one_unreadable_file_fails_the_whole_set(self, tmp_path, monkeypatch, live_payload):
        """Half a slate is worse than none: the games that did load would
        look like a quiet evening for the ones that did not."""
        good = tmp_path / "good.json"
        good.write_text(json.dumps(live_payload))
        bad = tmp_path / "bad.json"
        bad.write_text("")
        assert run(monkeypatch, ["--fixture", str(good), str(bad),
                                 "--out", str(tmp_path / "props.json")]) == 1


class TestPropsHistory:
    """The append-only record of every board that has been posted.

    props.json holds only what is live, and a refresh overwrites it. Without
    this file there is no way to ever answer the question that decides
    whether the model is worth paying for: when the projection disagreed
    with the line, which one was right. Every refresh that happens before
    this exists is evidence destroyed.
    """

    def board(self, line=30.5, at="2026-09-21T01:00:00+00:00"):
        return {"fetched_at": at, "source": "test", "props": {"cs2": {"acoR": [
            {"player": "acoR", "region": "CS2", "team": "Sashi", "stat": "kills",
             "maps": 2, "line": line, "odds_type": "standard",
             "provider": "prizepicks", "start_time": "2026-09-21T06:00:00-04:00"}]}}}

    def test_the_first_board_is_recorded(self, tmp_path):
        h = tmp_path / "history.jsonl"
        assert sp.archive_props(self.board(), str(h)) == 1
        rec = json.loads(h.read_text().strip())
        assert rec["player"] == "acoR" and rec["line"] == 30.5
        assert rec["game"] == "cs2"
        assert rec["observed_at"] == "2026-09-21T01:00:00+00:00"

    def test_refreshing_an_unchanged_board_records_nothing(self, tmp_path):
        h = tmp_path / "history.jsonl"
        sp.archive_props(self.board(), str(h))
        # A later refresh of the same slate: same line, new observation time.
        assert sp.archive_props(self.board(at="2026-09-21T02:30:00+00:00"), str(h)) == 0
        assert len(h.read_text().strip().splitlines()) == 1

    def test_a_line_that_moves_is_a_new_observation(self, tmp_path):
        """Where a line opened and where it closed is signal in itself: an
        edge that survives the market moving toward it is a different thing
        from one that evaporates."""
        h = tmp_path / "history.jsonl"
        sp.archive_props(self.board(line=30.5), str(h))
        assert sp.archive_props(self.board(line=29.5), str(h)) == 1
        lines = [json.loads(l) for l in h.read_text().strip().splitlines()]
        assert [r["line"] for r in lines] == [30.5, 29.5]

    def test_a_truncated_last_line_does_not_stop_the_record(self, tmp_path):
        """An interrupted append leaves half a row. Refusing to record
        anything further because of it would turn a cosmetic problem into
        permanent data loss."""
        h = tmp_path / "history.jsonl"
        sp.archive_props(self.board(), str(h))
        with open(h, "a") as f:
            f.write('{"game":"cs2","player":"trunc')
        assert sp.archive_props(self.board(line=28.5), str(h)) == 1

    def test_a_missing_file_is_simply_created(self, tmp_path):
        assert sp.archive_props(self.board(), str(tmp_path / "nope.jsonl")) == 1

    def test_an_empty_board_records_nothing(self, tmp_path):
        h = tmp_path / "history.jsonl"
        assert sp.archive_props({"fetched_at": "x", "props": {}}, str(h)) == 0

    def test_a_dry_run_records_nothing(self, tmp_path, monkeypatch, live_payload):
        """--dry-run means change nothing, and the history is a file like
        any other."""
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        h = tmp_path / "history.jsonl"
        run(monkeypatch, ["--fixture", str(saved), "--out", str(tmp_path / "p.json"),
                          "--history", str(h), "--dry-run"])
        assert not h.exists()

    def test_a_real_run_records_the_whole_board(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        h = tmp_path / "history.jsonl"
        out = tmp_path / "p.json"
        run(monkeypatch, ["--fixture", str(saved), "--out", str(out), "--history", str(h)])
        written = json.loads(out.read_text())
        total = sum(len(v) for game in written["props"].values() for v in game.values())
        assert len(h.read_text().strip().splitlines()) == total


class TestLeagueIds:
    def test_every_game_carries_one(self):
        """Without it the request is the whole board across every sport."""
        for game, cfg in sp.GAMES.items():
            assert isinstance(cfg.get("league_id"), int), game

    def test_they_are_distinct(self):
        ids = [cfg["league_id"] for cfg in sp.GAMES.values()]
        assert len(set(ids)) == len(ids)


class TestRefusesToWipeGoodLines:
    """A payload that parses but matches nothing is what a renamed stat
    label looks like. Writing it out deletes real lines on the strength of
    a guess that tonight is quiet."""

    EMPTY = {"data": [], "included": []}

    def _empty_payload(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps(self.EMPTY))
        return path

    def test_a_first_run_with_nothing_posted_is_fine(self, tmp_path, monkeypatch):
        out = tmp_path / "props.json"
        assert run(monkeypatch, ["--fixture", str(self._empty_payload(tmp_path)),
                                 "--out", str(out)]) == 0
        assert json.loads(out.read_text())["props"] == {
            "cs2": {}, "lol": {}, "valorant": {}}

    def test_wiping_an_existing_file_fails_the_run(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        out = tmp_path / "props.json"
        run(monkeypatch, ["--fixture", str(saved), "--out", str(out)])
        before = out.read_text()

        assert run(monkeypatch, ["--fixture", str(self._empty_payload(tmp_path)),
                                 "--out", str(out)]) == 1
        assert out.read_text() == before, "the good file was overwritten anyway"

    def test_allow_empty_is_the_way_to_say_the_slate_is_bare(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        out = tmp_path / "props.json"
        run(monkeypatch, ["--fixture", str(saved), "--out", str(out)])

        assert run(monkeypatch, ["--fixture", str(self._empty_payload(tmp_path)),
                                 "--out", str(out), "--allow-empty"]) == 0
        assert json.loads(out.read_text())["props"]["lol"] == {}

    def test_a_dry_run_still_says_nothing_matched(self, tmp_path, monkeypatch, capsys):
        """The smoke check in CI is a dry run, and it is the only place the
        provider is reached at all. A dry run that exits quietly on zero
        matches reads there as a clean bill of health, which is the exact
        opposite of what it means."""
        assert run(monkeypatch, ["--fixture", str(self._empty_payload(tmp_path)),
                                 "--out", str(tmp_path / "props.json"),
                                 "--dry-run"]) == 0
        assert "No props matched" in capsys.readouterr().err

    def test_an_unusable_payload_never_reaches_the_file(self, tmp_path, monkeypatch):
        """The provider refusing outright (403, or a non-JSON body) is the
        other route to the same wipe, and it already returns before writing."""
        out = tmp_path / "props.json"
        out.write_text(json.dumps({"props": {"lol": {"X": [{"line": 1.0}]}}}))
        monkeypatch.setattr(sp, "PROVIDERS", {"prizepicks": lambda session: None})
        assert run(monkeypatch, ["--out", str(out)]) == 1
        assert json.loads(out.read_text())["props"]["lol"]["X"]


class TestRequestsIsOnlyNeededToFetch:
    """The paste-a-payload route is documented as needing no install, and it
    is the only route that works on a machine the provider does not block.
    A module-level import of requests broke it with a traceback before it
    read the file — on exactly the machine where it is the only option.
    """

    def test_parsing_a_payload_works_without_requests(self, tmp_path, monkeypatch, live_payload):
        saved = tmp_path / "payload.json"
        saved.write_text(json.dumps(live_payload))
        out = tmp_path / "props.json"
        monkeypatch.setattr(sp, "requests", None)

        assert run(monkeypatch, ["--fixture", str(saved), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["props"]["lol"]

    def test_fetching_without_requests_says_so_plainly(self, tmp_path, monkeypatch, capsys):
        """A name error out of the middle of a run is a worse answer than a
        sentence naming the package."""
        monkeypatch.setattr(sp, "requests", None)
        assert run(monkeypatch, ["--out", str(tmp_path / "props.json")]) == 1
        err = capsys.readouterr().err
        assert "pip install requests" in err
        assert "--fixture" in err, "it should point at the route that does work"


class TestReadPayload:
    """What arrives instead of a payload, and whether the message says so.

    These are not hypotheticals: the empty-file case and the
    file-is-on-the-other-machine case both happened while following the
    README, and each surfaced as a traceback naming a line and column in a
    file the reader could not see.
    """

    def test_a_good_payload_comes_back_parsed(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text('{"data": [], "included": []}')
        assert sp.read_payload(str(path)) == {"data": [], "included": []}

    def test_a_missing_file_points_at_the_other_filesystem(self, tmp_path, capsys):
        assert sp.read_payload(str(tmp_path / "nope.json")) is None
        assert "Codespace" in capsys.readouterr().err

    @pytest.mark.parametrize("content", ["", "\n", "   \n\n  "])
    def test_a_paste_that_did_not_land_says_empty(self, tmp_path, capsys, content):
        """A file holding one newline is what a terminal paste leaves when it
        does not register, and json.loads calls that 'Expecting value: line 2
        column 1', which explains nothing."""
        path = tmp_path / "p.json"
        path.write_text(content)
        assert sp.read_payload(str(path)) is None
        assert "empty" in capsys.readouterr().err

    def test_html_is_named_as_html(self, tmp_path, capsys):
        """Saving from a browser can store the rendered page, and a block or
        login page lands here too — both are HTML, neither is a JSON error
        worth reading as one."""
        path = tmp_path / "p.json"
        path.write_text("<!DOCTYPE html>\n<html>blocked</html>")
        assert sp.read_payload(str(path)) is None
        assert "HTML" in capsys.readouterr().err

    def test_a_truncated_paste_shows_where_it_stops(self, tmp_path, capsys):
        path = tmp_path / "p.json"
        path.write_text('{"data": [{"id": "1", "attributes": {"stat_ty')
        assert sp.read_payload(str(path)) is None
        err = capsys.readouterr().err
        assert "not valid JSON" in err and "starts with" in err
        # The tail is the diagnosis: a cut-off copy stops mid-token instead
        # of on a closing brace, and seeing that is what tells them the copy
        # was short rather than the payload malformed.
        assert "ends with" in err and "stat_ty" in err

    def test_stdin_is_read_the_same_way(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"data": []}'))
        assert sp.read_payload("-") == {"data": []}

    def test_an_empty_stdin_is_reported_as_stdin(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert sp.read_payload("-") is None
        assert "stdin is empty" in capsys.readouterr().err


class TestExistingPropCount:
    def test_counts_every_prop_across_games(self, tmp_path):
        path = tmp_path / "props.json"
        path.write_text(json.dumps({"props": {
            "lol": {"A": [{}, {}], "B": [{}]}, "cs2": {"C": [{}]}}}))
        assert sp.existing_prop_count(str(path)) == 4

    @pytest.mark.parametrize("content", [
        "", "not json", "{}", '{"props": {}}',
        # Shapes a hand-edited or half-written file can take. None of these
        # may crash the run: an unreadable file is one the frontend cannot
        # read either, so there is nothing in it worth refusing to replace.
        "[1, 2, 3]", '{"props": "garbage"}', '{"props": {"lol": "garbage"}}',
        '{"props": {"lol": {"A": 7}}}', "null",
    ])
    def test_nothing_to_lose_reads_as_zero(self, tmp_path, content):
        path = tmp_path / "props.json"
        path.write_text(content)
        assert sp.existing_prop_count(str(path)) == 0

    def test_a_missing_file_reads_as_zero(self, tmp_path):
        assert sp.existing_prop_count(str(tmp_path / "nope.json")) == 0
