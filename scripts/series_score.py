"""A series score, written teamA-teamB like every other game's.

bo3.gg gives the score in its own team1/team2 order, but the CS2 scraper
names teamA and teamB in the order the teams turn up in the box score, so
about half of stored CS2 matches read backwards: "MOUZ v Vitality, MOUZ
won, 0-2". The winner is reliable (it agrees with the kill totals), so the
score is turned to fit it.
"""


def orient(score, team_a, team_b, winner):
    """`score` as teamA-teamB, using the winner to tell which side is which.

    Left alone when it cannot be read, is level, or has no winner to check.
    """
    try:
        left, right = (int(x) for x in str(score).split("-")[:2])
    except (TypeError, ValueError):
        return score
    if left == right or winner not in (team_a, team_b):
        return score
    a_won = winner == team_a
    if (left > right) == a_won:
        return score
    return f"{right}-{left}"


def orient_all(matches):
    """Fix every stored match in place; returns how many were turned."""
    turned = 0
    for m in matches or []:
        fixed = orient(m.get("score"), m.get("teamA"), m.get("teamB"), m.get("winner"))
        if fixed != m.get("score"):
            m["score"] = fixed
            turned += 1
    return turned
