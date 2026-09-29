#!/usr/bin/env python3
"""PropEdge on the command line, until the phone form exists.

    python scripts/propedge_cli.py balance
    python scripts/propedge_cli.py deposit 19.19 --note "starting bankroll"
    python scripts/propedge_cli.py place --mode power --stake 1 --multiplier 6 \
        --leg "sport=cs2 player=Fuzenko team=Aurora stat=headshots line=15.5 side=over maps=2" \
        --leg "sport=cs2 player=djay team=FlyQuest stat=headshots line=15 side=under maps=2"
    python scripts/propedge_cli.py autograde
    python scripts/propedge_cli.py grade slip_abc123 --player Lammens --actual 5
    python scripts/propedge_cli.py settle slip_abc123 --payout 0
    python scripts/propedge_cli.py size --mode power --multiplier 6 \
        --prob 0.58 --prob 0.57 --prob 0.55

The store lives at $PROPEDGE_DATA, or ~/.propedge/store.json. It is never
inside this repo: the repo is public.
"""
import argparse
import json
import os
import sys
from datetime import datetime

from . import analytics
from . import autograde as ag
from . import builder as bld
from .model import Projection, Simulations
from .money import fmt, to_cents
from .ledger import DEPOSIT, WITHDRAWAL
from .sizing import outcome_table, recommend
from .slips import Leg, Slip
from .store import Tracker


def parse_leg(spec):
    """"sport=cs2 player=djay line=15 side=under" -> Leg."""
    fields = {}
    for token in spec.split():
        if "=" not in token:
            raise SystemExit(f"! leg field {token!r} is not key=value")
        key, value = token.split("=", 1)
        fields[key] = value.replace("_", " ")
    for required in ("sport", "player", "stat", "line", "side"):
        if required not in fields:
            raise SystemExit(f"! leg is missing {required}=: {spec!r}")
    for number in ("line", "model_prob", "line_at_placement", "closing_line", "actual"):
        if number in fields:
            fields[number] = float(fields[number])
    if "maps" in fields:
        fields["maps"] = int(fields["maps"])
    return Leg(**fields)


def show_balance(tracker):
    balance, exposure = tracker.balance(), tracker.exposure()
    print(f"balance {fmt(balance)}   at risk on {len(tracker.pending())} pending "
          f"slip(s) {fmt(exposure)}")
    return balance


def cmd_balance(tracker, args):
    show_balance(tracker)
    for at, running in tracker.ledger.history()[-args.tail:]:
        entry = next(e for e in tracker.ledger.entries if e["at"] == at)
        print(f"  {at}  {entry['kind']:<10} {fmt(tracker.ledger.signed(entry)):>9}"
              f"  -> {fmt(running):>9}  {entry['note']}")
    return 0


def cmd_money(tracker, args):
    kind = DEPOSIT if args.command == "deposit" else WITHDRAWAL
    tracker.ledger.add(kind, to_cents(args.amount), note=args.note)
    tracker.save()
    show_balance(tracker)
    return 0


def cmd_place(tracker, args):
    slip = Slip(mode=args.mode, stake_cents=to_cents(args.stake),
                multiplier=args.multiplier, notes=args.note,
                placed_at=args.at or datetime.now().astimezone().isoformat(timespec="seconds"),
                legs=[parse_leg(spec) for spec in args.leg])
    problems = slip.problems()
    if problems:
        print("this slip breaks PrizePicks rules:")
        for problem in problems:
            print(f"  - {problem}")
        if not args.force:
            print("  (pass --force to log it anyway)")
            return 1
    tracker.place(slip, force=args.force)
    tracker.save()
    print(f"{slip.id}  {slip.mode} {slip.n_legs}-pick  stake {fmt(slip.stake_cents)}"
          + (f"  {slip.multiplier}x" if slip.multiplier else ""))
    show_balance(tracker)
    return 0


def cmd_list(tracker, args):
    slips = tracker.pending() if args.pending else tracker.slips
    if not slips:
        print("no slips" + (" pending" if args.pending else ""))
        return 0
    for slip in slips:
        paid = "" if slip.payout_cents is None else f"  paid {fmt(slip.payout_cents)}"
        print(f"{slip.id}  {slip.placed_at[:16]}  {slip.mode} {slip.n_legs}-pick  "
              f"stake {fmt(slip.stake_cents)}  {slip.status}{paid}")
        for leg in slip.legs:
            actual = "" if leg.actual is None else f"  actual {leg.actual:g}"
            odds = "" if leg.odds_type == "standard" else f" [{leg.odds_type}]"
            print(f"    {leg.player:<22} {leg.side:<5} {leg.line:<6g} {leg.stat}{odds}"
                  f"  {leg.result}{actual}")
    return 0


def cmd_grade(tracker, args):
    slip = tracker.find(args.slip)
    matches = [l for l in slip.legs if args.player.lower() in l.player.lower()]
    if len(matches) != 1:
        raise SystemExit(f"! {args.player!r} matches {len(matches)} legs on {slip.id}")
    leg = matches[0]
    leg.grade(args.actual, dnp=args.dnp)
    tracker.save()
    print(f"{leg.player} {leg.side} {leg.line:g} {leg.stat} -> {leg.result}")
    remaining = [l for l in slip.legs if l.result == "pending"]
    print(f"  {len(remaining)} leg(s) still pending on {slip.id}" if remaining
          else f"  {slip.id} is ready to settle")
    return 0


def cmd_autograde(tracker, args):
    graded, skipped = ag.autograde(tracker, args.results)
    for line in graded:
        print(f"  graded  {line}")
    for line in skipped:
        print(f"  left    {line}")
    if graded:
        tracker.save()
    print(f"{len(graded)} leg(s) graded, {len(skipped)} left alone")
    return 0


def cmd_settle(tracker, args):
    got = tracker.settle(args.slip, args.payout)
    tracker.save()
    slip = tracker.find(args.slip)
    print(f"{slip.id}  {got.status}  payout {fmt(got.payout_cents)}"
          + ("  (estimated)" if got.estimated else ""))
    for reason in got.reasons:
        print(f"  - {reason}")
    show_balance(tracker)
    return 0


def cmd_report(tracker, args):
    print("\n".join(analytics.report(tracker, args.history)))
    return 0


def load_projections(path):
    """A model's rows from JSON: either a bare list or {"projections": [...]}."""
    with open(path) as f:
        blob = json.load(f)
    rows = blob.get("projections") if isinstance(blob, dict) else blob
    if not rows:
        raise SystemExit(f"! {path} holds no projections")
    return [Projection.from_json(row) for row in rows]


def load_simulations(path):
    """{scenario: {prop_id: [value, ...]}} -- one simulated game per index."""
    with open(path) as f:
        blob = json.load(f)
    sims = Simulations()
    for scenario, by_prop in blob.items():
        for prop_id, values in by_prop.items():
            sims.add(scenario, prop_id, values)
    return sims


def project_board(args):
    """Project a posted board with the esports model, rather than reading rows.

    Imported lazily: this pulls in the shipped projection model, and `balance`
    should not pay for that.
    """
    from .esports import EsportsModel
    with open(args.board) as f:
        board = json.load(f)
    graded = []
    if args.results and os.path.exists(args.results):
        with open(args.results) as f:
            graded = json.load(f).get("graded") or []
    model = EsportsModel.from_files(args.root, graded)
    made = model.project_board(board)
    print(f"projected {len(made)} of the board's props "
          f"({len(graded)} graded props behind the market prior)")
    if args.write_projections:
        with open(args.write_projections, "w") as f:
            json.dump({"projections": [p.as_json() for p in made]}, f, indent=1)
            f.write("\n")
        print(f"wrote {args.write_projections}")
    return made


def cmd_nfl(tracker, args):
    """Simulate a game, optionally calibrate it to the book, and write the rows.

    Kept separate from `slate` because it is the slow half -- tens of thousands
    of simulated games, and calibration runs that many times over -- and because
    its output is worth keeping: the same simulations price every slip shape,
    so re-running them per slate would be paying twice for one answer.
    """
    from .nfl import GameSetup, calibrate, projections_from, simulate

    with open(args.game) as f:
        game = GameSetup.from_json(json.load(f))
    with open(args.board) as f:
        blob = json.load(f)
    board = blob.get("props") if isinstance(blob, dict) else blob
    if isinstance(board, dict):                      # {player: [rows]} like props.json
        board = [row for rows in board.values() for row in rows]

    usage = simulate(game, args.runs, seed=args.seed)
    market_sims = None
    if args.market:
        with open(args.market) as f:
            lines = [tuple(row) if isinstance(row, list) else
                     (row["player"], row["stat"], row["line"])
                     for row in json.load(f)]
        print(f"calibrating to {len(lines)} book line(s)…")
        tuned, history = calibrate(game, lines, rounds=args.rounds, runs=args.runs,
                                  report=lambda r: print(
                                      f"  round {r['round']:>2}  mean "
                                      f"|P(over)-0.5| = {r['mean_error']:.3f}  "
                                      f"max {r['max_error']:.3f}"))
        market_sims = simulate(tuned, args.runs, seed=args.seed + 1000)
        if args.write_game:
            with open(args.write_game, "w") as f:
                json.dump(tuned.as_json(), f, indent=1)
                f.write("\n")
            print(f"wrote the calibrated setup to {args.write_game}")
    else:
        print("no --market given, so nothing is calibrated: every prop will be "
              "marked low confidence and say so")

    made, sims = projections_from(board, usage, market_sims,
                                  market_weight=args.market_weight)
    print(f"{len(made)} projection(s) from {len(board)} board row(s), "
          f"{args.runs} simulated games each")
    unpriced = [p for p in made if p.confidence == "low"]
    if unpriced:
        print(f"  {len(unpriced)} have no market line — usage model only")
    with open(args.write_projections, "w") as f:
        json.dump({"projections": [p.as_json() for p in made]}, f, indent=1)
        f.write("\n")
    print(f"wrote {args.write_projections}")
    if args.write_simulations:
        with open(args.write_simulations, "w") as f:
            json.dump(sims.by_scenario, f)
            f.write("\n")
        print(f"wrote {args.write_simulations}")
    return 0 if made else 1


def cmd_tennis(tracker, args):
    """Fit one match to the market, then price its board.

    A tennis fit is entirely market-derived -- there is no usage model to
    disagree with it -- so the anchors are the whole input, and a match without
    a moneyline or a games-won line is refused rather than fitted to a total
    that two very different matches would both produce.
    """
    from . import tennis

    with open(args.match) as f:
        blob = json.load(f)
    anchors = []
    for row in blob.get("anchors") or []:
        anchors.append((row["kind"], row.get("who"), row.get("line"),
                        row["probability"]))
    fitted = tennis.fit(anchors, blob.get("tour", "ATP"))
    print(f"fit: serve {fitted.p_a:.3f} against {fitted.p_b:.3f} "
          f"(level {fitted.level:.3f}, gap {fitted.gap:+.3f}) over "
          f"{fitted.anchors} anchor(s), squared error {fitted.error:.2e}")
    if not fitted.usable:
        print(f"! {fitted.why_not()}")
        return 1

    points = tennis.simulate_points(
        fitted.p_a, fitted.p_b,
        aces=tuple(blob.get("ace_rates") or (0.075, 0.075)),
        double_faults=tuple(blob.get("double_fault_rates") or (0.03, 0.03)),
        runs=args.runs, seed=args.seed)
    with open(args.board) as f:
        board = json.load(f)
    made = tennis.projections_from(board, fitted, points,
                                   observed_breaks=blob.get("observed_breaks"))
    print(f"{len(made)} of {len(board)} board row(s) priced")
    skipped = len(board) - len(made)
    if skipped:
        print(f"  {skipped} not priced — a break prop without a measured break "
              "rate, a stat the model does not produce, or a row that does not "
              "say which player it is")
    with open(args.write_projections, "w") as f:
        json.dump({"projections": [p.as_json() for p in made]}, f, indent=1)
        f.write("\n")
    print(f"wrote {args.write_projections}")
    return 0 if made else 1


def cmd_slate(tracker, args):
    if args.board:
        projections = project_board(args)
        if not projections:
            print("nothing on the board could be projected")
            return 1
    else:
        projections = load_projections(args.projections)
    sims = load_simulations(args.simulations) if args.simulations else None
    slate = bld.build_slate(projections, tracker, sims,
                            sizes=tuple(args.sizes), mode=args.mode,
                            min_worst_ev=args.min_ev)
    print("\n".join(bld.render(slate)))
    # Exit 0 only when something is actually placeable. A slip that clears the
    # EV bar but cannot be staked -- the $1 minimum over full Kelly on a small
    # bankroll -- is information, not an instruction, and a scheduled run
    # should be able to tell the two apart without parsing the output.
    return 0 if any(c.stake.bet for c in slate["early"] + slate["late"]) else 1


def cmd_size(tracker, args):
    legs = []
    for spec in args.prob:
        p_win, _, p_push = spec.partition(",")
        legs.append((float(p_win), float(p_push or 0)))
    table = outcome_table(legs, args.mode, args.multiplier, tracker.table)
    bankroll = to_cents(args.bankroll) if args.bankroll is not None else tracker.balance()
    # The record decides how the stake is set, not a flag: if the model's
    # probabilities have not been honest, Kelly is the wrong instrument and
    # asking for it does not make it right.
    policy, why = analytics.sizing_policy(tracker.slips)
    if args.policy:
        policy, why = args.policy, f"forced with --policy {args.policy}"
    got = recommend(table, bankroll, stress_width=args.stress,
                    staked_tonight_cents=tracker.ledger.staked_on(
                        datetime.now().astimezone().date()),
                    policy=policy, policy_reason=why)
    print(f"bankroll {fmt(bankroll)}   {len(legs)} legs, {args.mode}")
    for outcome in table:
        print(f"  {outcome.label:<18} p={outcome.probability:6.2%}  "
              f"net {outcome.net_return:+.2f}")
    print(f"expected {got.expected_value:+.2%} per unit; full Kelly "
          f"{got.kelly_fraction:.2%} of bankroll, at {got.applied_fraction}x"
          + (f"  [{policy} sizing]" if policy != "kelly" else ""))
    print(f"STAKE {fmt(got.stake_cents)}" if got.bet else "NO BET")
    for reason in got.reasons:
        print(f"  - {reason}")
    print("  - legs priced as independent; correlated same-match legs make this "
          "optimistic")
    return 0 if got.bet else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="propedge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", help="override $PROPEDGE_DATA")
    subs = ap.add_subparsers(dest="command", required=True)

    balance = subs.add_parser("balance", help="the bankroll and recent entries")
    balance.add_argument("--tail", type=int, default=10)

    for name in ("deposit", "withdraw"):
        money = subs.add_parser(name, help=f"record a {name}")
        money.add_argument("amount")
        money.add_argument("--note", default="")

    place = subs.add_parser("place", help="log a slip you placed by hand")
    place.add_argument("--mode", choices=("power", "flex"), default="power")
    place.add_argument("--stake", required=True)
    place.add_argument("--multiplier", help="as printed on the slip")
    place.add_argument("--leg", action="append", required=True,
                       help='"sport=cs2 player=djay team=FlyQuest stat=headshots '
                            'line=15 side=under maps=2 odds_type=goblin"')
    place.add_argument("--at", help="ISO timestamp; defaults to now")
    place.add_argument("--note", default="")
    place.add_argument("--force", action="store_true",
                       help="log a slip that breaks a rule")

    listing = subs.add_parser("list", help="slips and their legs")
    listing.add_argument("--pending", action="store_true")

    grade = subs.add_parser("grade", help="set one leg's actual result")
    grade.add_argument("slip")
    grade.add_argument("--player", required=True)
    grade.add_argument("--actual", type=float)
    grade.add_argument("--dnp", action="store_true", help="did not play")

    auto = subs.add_parser("autograde", help="fill esports legs from props_results.json")
    auto.add_argument("--results", default="props_results.json")

    settle = subs.add_parser("settle", help="settle a slip whose legs are all graded")
    settle.add_argument("slip")
    settle.add_argument("--payout", help="what actually paid; omit to estimate")

    slate = subs.add_parser("slate", help="tonight's ranked slips")
    source = slate.add_mutually_exclusive_group(required=True)
    source.add_argument("--projections",
                        help="JSON: a list of model rows, or {\"projections\": [...]}")
    source.add_argument("--board",
                        help="a props.json to project with the esports model")
    slate.add_argument("--root", default=".",
                       help="where the per-game data files live (with --board)")
    slate.add_argument("--results", default="props_results.json",
                       help="graded record behind the market prior (with --board)")
    slate.add_argument("--write-projections",
                       help="also save the projected rows here")
    slate.add_argument("--simulations",
                       help='JSON: {scenario: {prop_id: [value, ...]}}, one '
                            'simulated game per index')
    slate.add_argument("--sizes", type=int, nargs="+", default=list(bld.DEFAULT_SIZES))
    slate.add_argument("--mode", choices=("power", "flex"), default="power")
    slate.add_argument("--min-ev", type=float, default=bld.MIN_WORST_EV,
                       dest="min_ev", help="worst-case EV bar (default %(default)s)")

    nfl = subs.add_parser("nfl", help="simulate an NFL game into projection rows")
    nfl.add_argument("--game", required=True, help="JSON GameSetup")
    nfl.add_argument("--board", required=True, help="JSON board rows for that game")
    nfl.add_argument("--market", help="JSON [[player, stat, line], ...] from a book")
    nfl.add_argument("--runs", type=int, default=20_000)
    nfl.add_argument("--rounds", type=int, default=12)
    nfl.add_argument("--seed", type=int, default=1)
    nfl.add_argument("--market-weight", type=float, default=0.6,
                     dest="market_weight")
    nfl.add_argument("--write-projections", default="nfl_projections.json")
    nfl.add_argument("--write-simulations", default="nfl_simulations.json")
    nfl.add_argument("--write-game", help="save the calibrated setup here")

    tennis = subs.add_parser("tennis", help="fit a tennis match and price its board")
    tennis.add_argument("--match", required=True,
                        help='JSON: {"tour", "anchors": [{"kind","who","line",'
                             '"probability"}], "ace_rates", "double_fault_rates",'
                             ' "observed_breaks"}')
    tennis.add_argument("--board", required=True,
                        help="JSON board rows, each carrying who=0 or who=1")
    tennis.add_argument("--runs", type=int, default=6000)
    tennis.add_argument("--seed", type=int, default=1)
    tennis.add_argument("--write-projections", default="tennis_projections.json")

    report = subs.add_parser("report", help="calibration, ROI, bankroll and CLV")
    report.add_argument("--history", default="props_history.jsonl")

    size = subs.add_parser("size", help="stake a hypothetical slip")
    size.add_argument("--policy", choices=("kelly", "flat"),
                      help="override what the record says the sizing should be")
    size.add_argument("--prob", action="append", required=True,
                      help="p_win[,p_push] per leg")
    size.add_argument("--mode", choices=("power", "flex"), default="power")
    size.add_argument("--multiplier")
    size.add_argument("--bankroll", help="defaults to the tracked balance")
    size.add_argument("--stress", type=float, default=0.0,
                      help="width of the p_win stress range")
    return ap


HANDLERS = {"balance": cmd_balance, "deposit": cmd_money, "withdraw": cmd_money,
            "place": cmd_place, "list": cmd_list, "grade": cmd_grade,
            "autograde": cmd_autograde, "settle": cmd_settle, "size": cmd_size,
            "report": cmd_report, "slate": cmd_slate, "nfl": cmd_nfl,
            "tennis": cmd_tennis}


def main(argv=None):
    args = build_parser().parse_args(argv)
    tracker = Tracker.load(args.store)
    return HANDLERS[args.command](tracker, args)


if __name__ == "__main__":
    sys.exit(main())
