#!/usr/bin/env python3
"""One team, one name.

bo3.gg does not spell a team the same way twice. The CS2 scraper has known
this for a while -- it tracks every name variant it sees per team_id and logs
"N team(s) have inconsistent naming across matches in the source data itself"
-- but nothing acted on the finding, and cs2_data.json accumulates across
runs. The result, measured on the committed file: 35 pairs among 301 teams,
about one team in eight, holding two entries with the same players and their
match history divided between them.

That is not cosmetic. Rosters, history pools, league rates and standings are
all keyed by team NAME, so a split team projects off half its games. 'The
Huns' had six matches and 'The Huns Esports' two, same six players, and the
board inference put one of them in front of a paying reader.

The rule here is deliberately narrow, because the cost of a wrong merge is
higher than the cost of a missed one. Two entries are the same team only when
BOTH hold:

  - their names reduce to the same key under alias_key below -- case,
    accents, punctuation and a leading "Team" or trailing org word are
    spelling, not identity;
  - and their rosters actually overlap, or one of them is a stub with no
    players at all.

Requiring both is what keeps a normalisation collision between two genuinely
different orgs from silently pooling their histories.

What it therefore does NOT touch, and should not: 'BBL' and 'Echo', 'A Great
Chaos' and 'FAFO', 'MARKandLARRY' and 'MARKnLARRY', 'NemNemesis' and
'Nemesis', '5STR' and '5star'. Every one of those shares a full roster and
none is a respelling -- they are rebrands, abbreviations and typos, and
deciding which is which is a judgement this file has no business making
silently. They stay visible in the report instead.
"""
import collections
import re
import unicodedata

# Stripped only as whole tokens, and deliberately a short list. "club" was in
# it and came out: stripping it turned "AIM Club" into "aim" while "AIMCLUB",
# the spelling the source actually uses, stayed "aimclub" -- so the one rule
# meant to reconcile them drove them apart. Leaving it in place reconciles
# both, and keeps "Club" where it is part of a real name.
ORG_WORDS = {"esports", "esport", "gaming"}
LEADING_WORDS = {"team"}

# Rewritten wherever a team name appears in a region payload. per_game is a
# LIST of per-map dicts, each keyed by team, which is the one that would be
# missed by a rewrite that only looked at the obvious fields.
NAME_FIELDS = ("teamA", "teamB", "winner")


def alias_key(name):
    """The spelling-insensitive identity of a team name, or '' if there is
    nothing left of it."""
    # NFKD, then the alphanumeric tokeniser below, is the whole of the accent
    # handling: decomposing "Grêmio" turns the ê into e + a combining
    # circumflex, and the tokeniser drops the combining mark as punctuation.
    # An explicit combining-mark filter here was doing nothing the tokeniser
    # was not already doing. Without the NFKD, though, a precomposed ê is
    # itself dropped and "Grêmio" reduces to "grmio".
    folded = unicodedata.normalize("NFKD", str(name or "")).lower()
    # Before tokenising, because splitting on punctuation would turn
    # "e-sports" into "e" + "sports" and neither is an org word on its own.
    folded = re.sub(r"\be[\s\-_]?sports\b", "esports", folded)
    # Tokenised on ANY non-alphanumeric, not just whitespace: the source
    # writes "G2!Esports" with no space in it, and a whitespace-only split
    # leaves that as one token where no org word can come off the end.
    tokens = [t for t in re.split(r"[^0-9a-z]+", folded) if t]
    while tokens and tokens[0] in LEADING_WORDS:
        tokens = tokens[1:]
    while tokens and tokens[-1] in ORG_WORDS:
        tokens = tokens[:-1]
    return "".join(tokens)


def match_counts(matches):
    """How many matches each team name appears in."""
    counts = collections.Counter()
    for m in matches or ():
        for field in ("teamA", "teamB"):
            name = m.get(field)
            if name:
                counts[name] += 1
    return counts


def alias_groups(teams, matches):
    """{canonical name: [the other spellings]} for every group that qualifies.

    The canonical is the spelling with the most matches behind it, then the
    fullest roster, then the lexicographically first -- so the choice does not
    depend on dict order and two runs over the same file agree.
    """
    rosters = {name: {p.get("name") for p in (data.get("players") or [])
                      if p.get("name")}
               for name, data in (teams or {}).items()}
    counts = match_counts(matches)
    by_key = collections.defaultdict(list)
    for name in rosters:
        key = alias_key(name)
        # A name that reduces to nothing -- a team called "Esports", or
        # "Team" -- would otherwise put every such entry in one group and fold
        # unrelated orgs together. Order within a group does not matter: it is
        # ranked below.
        if key:
            by_key[key].append(name)

    groups = {}
    for key, names in sorted(by_key.items()):
        if len(names) < 2:
            continue
        ranked = sorted(names, key=lambda n: (-counts[n], -len(rosters[n]), n))
        canonical = ranked[0]
        kept = []
        for other in ranked[1:]:
            # A stub entry has nothing to contradict; anything with a roster
            # has to actually share players with the one it folds into.
            if not rosters[other] or not rosters[canonical] \
                    or rosters[other] & rosters[canonical]:
                kept.append(other)
        if kept:
            groups[canonical] = kept
    return groups


def rename_map(groups):
    """{old name: canonical name}, flattened from alias_groups' output."""
    return {alias: canonical
            for canonical, aliases in groups.items()
            for alias in aliases}


def apply_aliases(region, renames):
    """Rewrites every team name in a region payload. Returns a counts dict.

    Mutates `region` in place, because the caller is about to write it.
    """
    counts = collections.Counter()
    if not renames:
        return counts

    teams = region.get("teams") or {}
    for old, new in renames.items():
        losing = teams.pop(old, None)
        if losing is None:
            continue
        counts["teams_folded"] += 1
        winning = teams.setdefault(new, {"players": []})
        # Union by player name, keeping the entry already under the canonical
        # name: it is the one whose career_games and cur/hist were computed
        # against the history that is about to absorb the rest.
        have = {p.get("name") for p in (winning.get("players") or [])}
        for player in losing.get("players") or []:
            if player.get("name") not in have:
                winning.setdefault("players", []).append(player)
                have.add(player.get("name"))
                counts["players_moved"] += 1
        for field, value in losing.items():
            if field != "players":
                winning.setdefault(field, value)

    def rewrite_keyed(mapping):
        """A {team: ...} dict, rebuilt under canonical names."""
        if not isinstance(mapping, dict):
            return mapping
        out = {}
        for team, value in mapping.items():
            target = renames.get(team, team)
            if target in out and isinstance(out[target], dict) and isinstance(value, dict):
                out[target] = {**value, **out[target]}
            else:
                out[target] = value
        return out

    for bucket in ("past_matches", "upcoming_matches"):
        for m in region.get(bucket) or ():
            for field in NAME_FIELDS:
                if m.get(field) in renames:
                    m[field] = renames[m[field]]
                    counts["fields_rewritten"] += 1
            if isinstance(m.get("actual"), dict):
                m["actual"] = rewrite_keyed(m["actual"])
            if isinstance(m.get("per_game"), list):
                m["per_game"] = [rewrite_keyed(g) for g in m["per_game"]]
    return counts


def reconcile(region):
    """Fold every aliased team in a region into one name. Returns (renames, counts)."""
    groups = alias_groups(region.get("teams") or {}, region.get("past_matches") or ())
    renames = rename_map(groups)
    return renames, apply_aliases(region, renames)
