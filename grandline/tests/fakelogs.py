"""
WHAT THIS FILE DOES
Builds synthetic simulator logs -- combat logs and player logs -- for the
tests, in exactly the line formats the real files use.

WHY IT EXISTS
Real logs contain other players' handles and local paths, so they are
never committed. These builders reproduce the shapes verified against the
real files on 4 Oct 2026 (zero-width spaces, <mark><link> card markup,
room markers, life lines) with invented handles.
"""

from datetime import datetime
from pathlib import Path

ZW = "​"  # trap T2: every real handle has one before the '#'
ME = "Tester#1111"


def h(handle: str) -> str:
    """A handle as the sim writes it: 'Name\\u200b#1234'."""
    name, tag = handle.split("#")
    return f"{name}{ZW}#{tag}"


def card(card_id: str, name: str) -> str:
    """A card reference in the AutoSaved format."""
    return f'{name} [<mark><link="{card_id}">{card_id}</link></mark>]'


def combat_log(
    opponent: str = "Rival#2222",
    my_leader: tuple[str, str] = ("OP17-039", "Rocks D. Xebec"),
    opp_leader: tuple[str, str] = ("OP17-058", "Kaido"),
    room: str = "WROOM01",
    hosted: bool = True,
    i_go_first: bool = True,
    body: list[str] | None = None,
) -> list[str]:
    """One game's combat log. `body` is everything after the setup."""
    me_line = f"[{h(ME)}] Leader is {card(*my_leader)}"
    opp_line = f"[{h(opponent)}] Leader is {card(*opp_leader)}"
    if hosted:
        head = [f"Waiting for a Connection with Room ID:{room}",
                f"{h(ME)} Has Connected", "Version is 1.43a.3", me_line,
                f"{h(opponent)} Has Connected", "Version is 1.43a.3", opp_line]
    else:
        head = [f"Attempting to connect to {room}",
                f"{h(opponent)} Has Connected", "Version is 1.43a.3", opp_line,
                f"{h(ME)} Has Connected", "Version is 1.43a.3", me_line]
    first, second = (ME, opponent) if i_go_first else (opponent, ME)
    opening = [f"[{h(first)}] Draw 1 Don", f"[{h(second)}] Draw 1 Card",
               f"[{h(second)}] Draw 2 Don", "RZ1|1|2|OP10-011|0|5|0|0|0|0|0|0|0"]
    return head + opening + (body or [])


def concede(handle: str) -> list[str]:
    return [f"[{h(handle)}] <b><size=24>ggs</size></b>", f"[{h(handle)}] Concedes!"]


def life(handle: str, n: int) -> str:
    return f"[{h(handle)}] Life: {n}"


def attack(attacker: str, with_card: tuple[str, str], target: tuple[str, str]) -> str:
    return f"[{h(attacker)}] {card(*with_card)} attacking {card(*target)}"


def hit(target: tuple[str, str], damage: int = 1) -> str:
    return f"{card(*target)} hit for {damage} damage"


def player_log(install_root: Path | str, events: list[str]) -> list[str]:
    """A player log: Unity's startup lines (naming the install) + events."""
    managed = f"{Path(install_root).as_posix()}/OPTCGSim_Data/Managed"
    return [f"Mono path[0] = '{managed}'",
            "Initialize engine version: 6000.0.58f2", *events]


def hosted(room: str) -> str:
    return f"match ready on Room ID {room} timed: True"


def joined(relay: str) -> str:
    return f"Joined relay {relay}"


def result(outcome: str) -> str:
    return f"OPB_RESULT|{outcome}"


def stem(moment: datetime) -> str:
    """A combat log file name for a local time."""
    return moment.strftime("%Y-%m-%dT%H.%M.%S")


def write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
