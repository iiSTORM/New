#!/usr/bin/env python3
"""
Aggregates real per-game, per-player, per-champion K/D/A into champion-
level stat baselines — the foundational piece any champion-aware feature
(draft-based projection adjustments, champion pool analysis, Fearless
Draft ban-pool value estimation) needs first. Reads data.json directly;
no scraping of its own, since scrape_lcs.py's per_game field (added
alongside picks/bans/Fearless capture) already has everything needed.

Two levels of aggregation, since they answer different questions:
  - champion-level: "how many kills does a Rumble pick tend to produce,
    league-wide" — useful as a general prior, high sample size.
  - player+champion-level: "how does THIS player specifically perform on
    Rumble vs their overall average" — lower sample size per entry (a
    given player might have only played a given champion a handful of
    times), but captures real player-specific champion affinity that a
    league-wide average would wash out.

Usage:
    python scripts/aggregate_champion_stats.py
Writes champion_stats.json: {
  "champions": {champion_name: {g, k, d, a}},
  "player_champions": {"player_name|champion_name": {g, k, d, a}}
}
"""
import json
import sys
from pathlib import Path

DATA_PATH = "data.json"
OUTPUT_PATH = "champion_stats.json"


def main():
    if not Path(DATA_PATH).exists():
        print(f"! {DATA_PATH} not found — run scrape_lcs.py first.", file=sys.stderr)
        sys.exit(1)

    with open(DATA_PATH) as f:
        data = json.load(f)

    champion_totals = {}  # champion -> {g, k, d, a}
    player_champion_totals = {}  # "player|champion" -> {g, k, d, a}
    matches_with_per_game = 0
    matches_total = 0

    for region_key, region_data in data.get("regions", {}).items():
        for m in region_data.get("past_matches", []):
            matches_total += 1
            per_game = m.get("per_game")
            if not per_game:
                continue
            matches_with_per_game += 1
            for game in per_game:
                if not game:
                    continue
                for team, players in game.items():
                    for player_name, stats in players.items():
                        champion = stats.get("champion")
                        if not champion:
                            continue
                        k, d, a = stats["k"], stats["d"], stats["a"]

                        c = champion_totals.setdefault(champion, {"g": 0, "k": 0, "d": 0, "a": 0})
                        c["g"] += 1
                        c["k"] += k
                        c["d"] += d
                        c["a"] += a

                        pc_key = f"{player_name}|{champion}"
                        pc = player_champion_totals.setdefault(pc_key, {"g": 0, "k": 0, "d": 0, "a": 0})
                        pc["g"] += 1
                        pc["k"] += k
                        pc["d"] += d
                        pc["a"] += a

    def to_rates(totals):
        return {
            key: {
                "g": t["g"],
                "k": t["k"] / t["g"],
                "d": t["d"] / t["g"],
                "a": t["a"] / t["g"],
            }
            for key, t in totals.items()
        }

    output = {
        "champions": to_rates(champion_totals),
        "player_champions": to_rates(player_champion_totals),
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"{matches_with_per_game}/{matches_total} matches had per_game data to aggregate from")
    print(f"Wrote {OUTPUT_PATH}: {len(output['champions'])} champions, "
          f"{len(output['player_champions'])} player+champion combos")

    if champion_totals:
        # Sample a few high-sample-size champions as a sanity check —
        # real numbers should look plausible (e.g. a marksman/mage carry
        # champion averaging noticeably more kills than a pure support
        # pick), not flag anything on their own, just something worth
        # eyeballing before trusting this data further.
        top_sampled = sorted(champion_totals.items(), key=lambda kv: -kv[1]["g"])[:5]
        print("\nMost-sampled champions (sanity check):")
        for champ, t in top_sampled:
            print(f"  {champ}: {t['g']} games, {t['k']/t['g']:.1f}/{t['d']/t['g']:.1f}/{t['a']/t['g']:.1f} (K/D/A per game)")


if __name__ == "__main__":
    main()