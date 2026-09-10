"""
WHAT THIS FILE DOES
Prints a fake leaderboard to your terminal so you can SEE what the maths
produces, before any database or website exists.

WHY IT EXISTS
Stage 1 ships only pure functions. Tests prove they're correct, but they
don't show you the product. This does -- especially the F2 rule that a
low-sample leader shows a game count and no win rate.

RUN IT WITH:  python3 scripts/preview.py
Needs nothing installed. Standard library only.

EVERY NUMBER BELOW IS INVENTED. Nothing here touches a real data source.
"""

import sys
from pathlib import Path

# Put src/ on the import path so this runs without `pip install -e .`.
# Only needed because this is a loose script rather than an installed
# entry point; stage 3 replaces it with a proper CLI command.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from grandline.stats.rates import summarize  # noqa: E402

# Invented ladder results for one week.
# Shape: (code, display name, appearances, wins, draws, previous win rate)
# The last row is deliberately tiny -- it's here to demonstrate the
# sample gate, which is the behaviour most worth eyeballing.
SAMPLE_WINDOW = [
    ("OP11-001", "Blue/Yellow Nami",  2651, 1373, 0, 0.504),
    ("ST21-001", "Purple Luffy",      2165, 1150, 3, 0.502),
    ("OP14-001", "Red Shanks",        1829,  900, 1, 0.499),
    ("OP09-004", "Black Lucci",       1325,  668, 2, 0.504),
    ("OP12-002", "Green Uta",          884,  429, 0, 0.491),
    ("OP17-009", "Green Bonney",         74,   41, 0, None),  # below the gate
]

# Total matches in the window. Needed because play rate is measured
# against the whole format, not just the leaders listed above.
TOTAL_MATCHES = 9330


def pct(value: float) -> str:
    """Format a 0..1 rate as a percentage string, e.g. 0.518 -> '51.8%'."""
    return f"{value * 100:.1f}%"


def main() -> None:
    """Build a summary for each leader, sort by play rate, print the table."""
    # summarize() is the same function the real rollup job will call in
    # stage 3 -- this script only supplies the numbers.
    rows = [
        summarize(
            leader_code=code,
            appearances=appearances,
            wins=wins,
            total_matches=TOTAL_MATCHES,
            draws=draws,
            previous_win_rate=previous,
        )
        for code, _, appearances, wins, draws, previous in SAMPLE_WINDOW
    ]

    # Lookup table for display names, since RateSummary only carries the code.
    names = {code: name for code, name, *_ in SAMPLE_WINDOW}

    # Default sort matches feature F1: play rate descending, because
    # "what will I face?" is the more common first question.
    rows.sort(key=lambda r: r.play_rate, reverse=True)

    header = f"{'Leader':<22}{'Play':>8}{'Win rate':>18}{'Games':>8}{'Δ 7d':>9}"
    print()
    print(f"  Illustrative preview · {TOTAL_MATCHES:,} matches · figures invented")
    print("  " + "─" * len(header))
    print("  " + header)
    print("  " + "─" * len(header))

    for row in rows:
        name = names[row.leader_code]
        play = pct(row.play_rate)

        if row.show_win_rate:
            # Enough games: show the rate and a ± from the interval.
            spread = (row.ci_high - row.ci_low) / 2 * 100
            win = f"{pct(row.win_rate)} ±{spread:.1f}"
            delta = f"{row.delta_7d:+.1f}"
        else:
            # Feature F2: below the gate, publish nothing. Note the row
            # still appears -- hiding it would understate how much of the
            # format is fringe decks.
            win = "— low sample"
            delta = "new"

        print(f"  {name:<22}{play:>8}{win:>18}{row.decided:>8}{delta:>9}")

    print("  " + "─" * len(header))
    print("  Win rates below 100 decided games are withheld, not estimated.")
    print()


# Standard Python guard: run main() when executed directly, but stay
# silent if this file is ever imported by something else.
if __name__ == "__main__":
    main()
