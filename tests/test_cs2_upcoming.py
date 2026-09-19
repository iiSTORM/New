"""CS2 upcoming-match collection.

The scraper used to find fixtures beyond today only through the schedules
of teams it had discovered in that run's finished matches, so a match
tomorrow between two teams outside that set did not exist as far as the app
was concerned. These cover the pure logic of the replacement; the HTTP call
itself is exercised against the live API by the scheduled run.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("cs2api")

import scrape_cs2


class TestParseMatchStart:
    def test_parses_the_offset_format_bo3_returns(self):
        got = scrape_cs2.parse_match_start({"start_date": "2026-09-21T10:00:00.000+00:00"})
        assert got == datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

    def test_parses_a_space_separated_timestamp(self):
        got = scrape_cs2.parse_match_start({"start_date": "2026-09-21 10:00:00+00:00"})
        assert got == datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

    def test_naive_timestamps_are_treated_as_utc(self):
        got = scrape_cs2.parse_match_start({"start_date": "2026-09-21T10:00:00"})
        assert got.tzinfo is not None

    def test_falls_back_to_the_date_field(self):
        assert scrape_cs2.parse_match_start({"date": "2026-09-21T10:00:00+00:00"}) is not None

    @pytest.mark.parametrize("value", [None, "", "tomorrow", 12345, {}])
    def test_unparseable_values_give_none_rather_than_raising(self, value):
        assert scrape_cs2.parse_match_start({"start_date": value}) is None

    def test_missing_entirely_gives_none(self):
        assert scrape_cs2.parse_match_start({}) is None


class TestUpcomingWindowQuery:
    """The window is the whole point: it has to actually span tomorrow."""

    @staticmethod
    def _captured_params(monkeypatch, days=None):
        captured = {}

        async def fake_get(session, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"results": []}

        monkeypatch.setattr(scrape_cs2, "bo3_get", fake_get)
        import asyncio
        kwargs = {} if days is None else {"days": days}
        asyncio.run(scrape_cs2.fetch_upcoming_window(None, **kwargs))
        return captured

    def test_queries_the_matches_endpoint_for_upcoming_cs2_matches(self, monkeypatch):
        cap = self._captured_params(monkeypatch)
        assert cap["path"] == "/matches"
        assert cap["params"]["filter[matches.status][in]"] == "upcoming,current"
        assert cap["params"]["filter[matches.discipline_id][eq]"] == "1"

    def test_the_window_includes_tomorrow(self, monkeypatch):
        """The actual regression: a one-day filter hid the next day's games."""
        cap = self._captured_params(monkeypatch)
        upper = cap["params"]["filter[matches.start_date][lt]"]
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        assert upper[:10] > tomorrow or upper[:10] == tomorrow

    def test_window_length_is_configurable(self, monkeypatch):
        cap = self._captured_params(monkeypatch, days=3)
        expected = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
        assert cap["params"]["filter[matches.start_date][lt]"].startswith(expected)

    def test_pages_until_a_short_batch(self, monkeypatch):
        calls = []

        async def fake_get(session, path, params=None):
            calls.append(int(params["page[offset]"]))
            # two full pages, then a short one
            n = scrape_cs2.UPCOMING_PAGE_LIMIT if len(calls) < 3 else 1
            return {"results": [{"id": i} for i in range(n)]}

        monkeypatch.setattr(scrape_cs2, "bo3_get", fake_get)
        import asyncio
        rows = asyncio.run(scrape_cs2.fetch_upcoming_window(None))
        assert calls == [0, scrape_cs2.UPCOMING_PAGE_LIMIT, scrape_cs2.UPCOMING_PAGE_LIMIT * 2]
        assert len(rows) == scrape_cs2.UPCOMING_PAGE_LIMIT * 2 + 1

    def test_a_failed_fetch_returns_nothing_rather_than_raising(self, monkeypatch):
        """This step is no longer continue-on-error, so it must not throw."""
        async def fake_get(session, path, params=None):
            return None

        monkeypatch.setattr(scrape_cs2, "bo3_get", fake_get)
        import asyncio
        assert asyncio.run(scrape_cs2.fetch_upcoming_window(None)) == []


class TestBackfillPrioritisation:
    """Roster backfill is rate-limit bound, so when it has to choose it must
    choose the teams playing soonest. This mirrors the selection the scraper
    performs inline."""

    @staticmethod
    def _rank(upcoming, covered, cap):
        soonest = {}
        for m in upcoming:
            for name in (m["teamA"], m["teamB"]):
                if name in covered:
                    continue
                when = m.get("date") or ""
                if name not in soonest or when < soonest[name]:
                    soonest[name] = when
        ranked = sorted(soonest, key=lambda n: (soonest[n], n))
        return ranked[:cap], ranked[cap:]

    def test_soonest_opponents_come_first(self):
        upcoming = [
            {"teamA": "Known", "teamB": "Later", "date": "2026-09-25T10:00:00+00:00"},
            {"teamA": "Known", "teamB": "Sooner", "date": "2026-09-20T10:00:00+00:00"},
        ]
        picked, skipped = self._rank(upcoming, {"Known"}, cap=1)
        assert picked == ["Sooner"] and skipped == ["Later"]

    def test_teams_with_a_roster_are_not_backfilled(self):
        upcoming = [{"teamA": "Known", "teamB": "AlsoKnown", "date": "2026-09-20T10:00:00+00:00"}]
        picked, skipped = self._rank(upcoming, {"Known", "AlsoKnown"}, cap=25)
        assert picked == [] and skipped == []

    def test_a_team_is_ranked_by_its_earliest_fixture(self):
        upcoming = [
            {"teamA": "Known", "teamB": "Team", "date": "2026-09-30T10:00:00+00:00"},
            {"teamA": "Known", "teamB": "Team", "date": "2026-09-20T10:00:00+00:00"},
        ]
        picked, _ = self._rank(upcoming, {"Known"}, cap=25)
        assert picked == ["Team"]

    def test_cap_is_respected(self):
        upcoming = [
            {"teamA": "Known", "teamB": f"T{i:02d}", "date": f"2026-09-{20 + i % 5:02d}T10:00:00+00:00"}
            for i in range(40)
        ]
        picked, skipped = self._rank(upcoming, {"Known"}, cap=25)
        assert len(picked) == 25 and len(skipped) == 15
        assert not set(picked) & set(skipped)
