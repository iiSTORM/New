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
import sys
from datetime import datetime

from . import analytics
from . import autograde as ag
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
            "report": cmd_report}


def main(argv=None):
    args = build_parser().parse_args(argv)
    tracker = Tracker.load(args.store)
    return HANDLERS[args.command](tracker, args)


if __name__ == "__main__":
    sys.exit(main())
