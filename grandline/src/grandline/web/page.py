"""
WHAT THIS FILE DOES
Builds the tracker's one page: gathers everything it shows from the store
(load_page) and turns that into HTML (render_page).

WHY IT EXISTS
Stage 5, features F4-F8: the record, by leader, matchups, leader names
and recent games. Kept apart from the web server so the page can be
tested as plain "data in, HTML out" -- no server, no browser.

WHAT IT SHOWS, TOP TO BOTTOM
  Health    Is the watcher alive? When was the last game captured? A
            stale heartbeat is shown loudly: PRD risk #1 is the watcher
            silently not running.
  F4        Overall record, raw win rate (D5: no minimum sample), its
            95% interval, and how many games have no result (T4).
  F12       The same record split by going first / second. The data is
            captured anyway; it costs one line.
  F5        Record by my leader. Clicking a leader filters F6 and F8.
  F6        Record against each opposing leader.
  F8        The last twenty games.
  F7        Leader names come from the combat logs, stored with each game.

SECURITY
Opponent handles are chosen by other players, so every value from the
database is HTML-escaped. A handle like <script>...</script> must show
up as text, never run.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import quote

import sqlite3

from grandline.capture.store import (
    Group,
    Heartbeat,
    Record,
    StoredGame,
    heartbeat,
    last_capture,
    record,
    records_by,
    recent_games,
)

# A heartbeat older than this means the watcher isn't running.
STALE_SECONDS = 60
REFRESH_SECONDS = 30
RECENT_LIMIT = 20


@dataclass(frozen=True, slots=True)
class PageData:
    """Everything the page shows, already read from the store."""

    overall: Record
    going_first: Record
    going_second: Record
    by_leader: list[Group]
    matchups: list[Group]
    recent: list[StoredGame]
    selected: Group | None  # the my_leader filter, if one is active
    last_capture: datetime | None
    heartbeat: Heartbeat | None


def load_page(conn: sqlite3.Connection, leader: str | None = None) -> PageData:
    """Read everything for one page view.

    WHY THIS FUNCTION EXISTS: so rendering never touches the database,
    and a filter value from the URL is only honoured if it names a leader
    that actually exists -- anything else just shows the full page.
    """
    by_leader = records_by(conn, "my_leader")
    selected = next((g for g in by_leader if g.key == leader), None)
    filters = {"my_leader": selected.key} if selected else {}
    return PageData(
        overall=record(conn, **filters),
        going_first=record(conn, went_first=1, **filters),
        going_second=record(conn, went_first=0, **filters),
        by_leader=by_leader,
        matchups=records_by(conn, "opponent_leader", **filters),
        recent=recent_games(conn, RECENT_LIMIT, **filters),
        selected=selected,
        last_capture=last_capture(conn),
        heartbeat=heartbeat(conn),
    )


# --- Formatting -------------------------------------------------------


def fmt_rate(r: Record) -> str:
    """Raw win rate (D5), or a dash when there are no decided games.

    WHY THE DASH: win_rate() returns 0.0 for "no data" (the open design
    question in CLAUDE.md). "0%" would claim "never wins"; "—" says
    "nothing to show yet".
    """
    return "—" if r.games == 0 else f"{r.win_rate:.0%}"


def fmt_interval(r: Record) -> str:
    """The 95% interval as a range, e.g. '41–79%'. Empty with no games."""
    if r.games == 0:
        return ""
    low, high = r.interval
    return f"{low:.0%}–{high:.0%}"


def fmt_age(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "never"
    s = int((now - moment).total_seconds())
    if s < 120:
        return f"{s}s ago"
    if s < 7200:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def fmt_when(moment: datetime) -> str:
    """Local time, e.g. 'Fri 2 Oct, 16:46'."""
    local = moment.astimezone()
    return f"{local:%a} {local.day} {local:%b}, {local:%H:%M}"


# Outcome -> (pill text, css class). D3: each early ending keeps its tag.
_OUTCOME = {
    "win": ("Win", "w"),
    "opponent_concede": ("Win · they conceded", "w"),
    "opponent_disconnect": ("Win · they left", "w"),
    "loss": ("Loss", "l"),
    "concede": ("Loss · conceded", "l"),
    "unknown": ("No result", "u"),
}

_SOURCE = {
    "combat_log": "",
    "manual_combat_log": "from a manual save",
    "player_log": "from Player.log",
    "none": "",  # the "No result" pill already says it
}


def _leader(name: str, card_id: str) -> str:
    return f'{escape(name)} <span class="id">{escape(card_id)}</span>'


def _record_cells(r: Record) -> str:
    unknown = (f' <span class="muted small" title="games with no result in any log">'
               f"· {r.unknown} unknown</span>") if r.unknown else ""
    return (
        f'<td class="num rec">{r}{unknown}</td>'
        f'<td class="num">{fmt_rate(r)}</td>'
        f'<td class="num muted">{fmt_interval(r)}</td>'
    )


# --- The page ---------------------------------------------------------


def render_page(data: PageData, now: datetime) -> str:
    """Turn PageData into a complete HTML document. Pure."""
    title = "Grand Line Meta"
    if data.selected:
        title = f"{data.selected.label} · Grand Line Meta"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<title>{escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
  <h1>Grand Line Meta</h1>
  {_health(data, now)}
</header>
{_summary(data)}
{_by_leader(data)}
{_matchups(data)}
{_recent(data)}
<footer>Refreshes every {REFRESH_SECONDS}s · Win rates are raw at every sample size; the record beside each one is the sample. Range = 95% interval.</footer>
</main>
</body>
</html>
"""


def _health(data: PageData, now: datetime) -> str:
    hb = data.heartbeat
    last = f"Last game captured {fmt_age(data.last_capture, now)}."
    if hb is None:
        return (f'<p class="health bad"><b>The watcher has never run.</b> Games are not being '
                f"captured. Start it with <code>python -m grandline.capture.watcher</code>.</p>")
    age = (now - hb.last_poll_at).total_seconds()
    if age > STALE_SECONDS:
        return (f'<p class="health bad"><b>The watcher is not running</b> — last checked in '
                f"{fmt_age(hb.last_poll_at, now)}. Games played now will not be captured. "
                f"{last}</p>")
    return f'<p class="health ok"><span class="dot"></span>Watcher running. {last}</p>'


def _summary(data: PageData) -> str:
    r = data.overall
    scope = (f"with {_leader(data.selected.label, data.selected.key)} "
             f'<a href="/">show all</a>') if data.selected else "all games"
    unknown = (f'<p class="muted">{r.unknown} game{"s" if r.unknown != 1 else ""} with no '
               f"result in any log — counted, but in neither column.</p>") if r.unknown else ""
    early = []
    if r.concedes:
        early.append(f"{r.concedes} conceded by you")
    if r.opponent_concedes:
        early.append(f"{r.opponent_concedes} conceded by them")
    if r.opponent_disconnects:
        early.append(f"{r.opponent_disconnects} they left")
    early_line = f'<p class="muted">Includes {", ".join(early)}.</p>' if early else ""
    return f"""<section class="summary">
  <p class="scope">{scope}</p>
  <div class="big">
    <div><span class="label">Record</span><span class="value">{r}</span></div>
    <div><span class="label">Win rate</span><span class="value">{fmt_rate(r)}</span>
      <span class="range">{fmt_interval(r)}</span></div>
  </div>
  {early_line}
  {unknown}
  <table class="split">
    <tr><th>Going first</th>{_record_cells(data.going_first)}</tr>
    <tr><th>Going second</th>{_record_cells(data.going_second)}</tr>
  </table>
</section>"""


def _table(title: str, head: str, rows: list[str], empty: str) -> str:
    body = "\n".join(rows) if rows else f'<tr><td colspan="9" class="muted">{empty}</td></tr>'
    return f"""<section>
  <h2>{title}</h2>
  <div class="scroll"><table>
    <thead><tr>{head}</tr></thead>
    <tbody>{body}</tbody>
  </table></div>
</section>"""


_REC_HEAD = '<th class="num">Record</th><th class="num">Win rate</th><th class="num">Range</th>'


def _by_leader(data: PageData) -> str:
    rows = []
    for g in data.by_leader:
        active = data.selected is not None and g.key == data.selected.key
        rows.append(
            f'<tr class="{"active" if active else ""}"><td>'
            f'<a href="/?leader={quote(g.key)}">{_leader(g.label, g.key)}</a></td>'
            f"{_record_cells(g.record)}</tr>"
        )
    return _table("Your leaders", f"<th>Leader</th>{_REC_HEAD}", rows, "No games yet.")


def _matchups(data: PageData) -> str:
    title = "Matchups"
    if data.selected:
        title += f" — as {escape(data.selected.label)}"
    rows = [f"<tr><td>{_leader(g.label, g.key)}</td>{_record_cells(g.record)}</tr>"
            for g in data.matchups]
    return _table(title, f"<th>Against</th>{_REC_HEAD}", rows, "No games yet.")


def _recent(data: PageData) -> str:
    rows = []
    for g in data.recent:
        text, cls = _OUTCOME.get(g.outcome, (g.outcome, "u"))
        source = _SOURCE.get(g.outcome_source, g.outcome_source)
        note = f'<span class="muted small">{escape(source)}</span>' if source else ""
        first = {True: "1st", False: "2nd", None: "—"}[g.went_first]
        rows.append(
            f'<tr><td class="nowrap">{fmt_when(g.ended_at)}</td>'
            f"<td>{_leader(g.my_leader_name, g.my_leader)}</td>"
            f"<td>{escape(g.opponent_handle)}</td>"
            f"<td>{_leader(g.opponent_leader_name, g.opponent_leader)}</td>"
            f'<td class="num">{first}</td>'
            f'<td><span class="pill {cls}">{text}</span> {note}</td></tr>'
        )
    head = ('<th>When</th><th>You</th><th>Opponent</th><th>Their leader</th>'
            '<th class="num">Turn</th><th>Result</th>')
    return _table(f"Last {RECENT_LIMIT} games", head, rows, "No games yet.")


_CSS = """
:root{
  --bg:#f6f7f9; --surface:#fff; --ink:#15202b; --muted:#5d6b78; --line:#dde3e9;
  --accent:#12586e; --win:#1f7a52; --win-bg:#e3f3ea; --loss:#a93226; --loss-bg:#f8e5e2;
  --unk:#7a6a10; --unk-bg:#f5efd8; --bad-bg:#fbe9e7; --ok-bg:#e6f2ec;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0f1720; --surface:#16212c; --ink:#e4ebf1; --muted:#90a2b2; --line:#283542;
    --accent:#5cb8d2; --win:#55c08f; --win-bg:#173528; --loss:#e3786a; --loss-bg:#3a1f1c;
    --unk:#d6b54a; --unk-bg:#33301a; --bad-bg:#3a1f1c; --ok-bg:#173528;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 8px;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:0 0 10px}
section{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:16px;margin-top:16px}
a{color:var(--accent)}
code{font-size:.9em}
.health{margin:0;padding:10px 12px;border-radius:6px}
.health.ok{background:var(--ok-bg)}
.health.bad{background:var(--bad-bg);color:var(--loss)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--win);
  margin-right:8px;vertical-align:middle}
.scope{margin:0 0 8px;color:var(--muted)}
.big{display:flex;flex-wrap:wrap;gap:32px;margin-bottom:8px}
.big .label{display:block;font-size:.8rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.06em}
.big .value{font-size:2.2rem;font-weight:650;font-variant-numeric:tabular-nums}
.big .range{color:var(--muted);margin-left:8px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
thead th{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
tbody tr:last-child td{border-bottom:none}
table.split{width:auto;margin-top:8px}
table.split th{font-weight:500;color:var(--muted);padding-left:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.rec{font-weight:600}
tbody td:first-child{min-width:10rem}
.id{color:var(--muted);font-size:.8em;margin-left:4px}
.muted{color:var(--muted)}
.small{font-size:.82em}
.nowrap{white-space:nowrap}
tr.active td{background:var(--ok-bg)}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.82em;font-weight:600;
  white-space:nowrap}
.pill.w{color:var(--win);background:var(--win-bg)}
.pill.l{color:var(--loss);background:var(--loss-bg)}
.pill.u{color:var(--unk);background:var(--unk-bg)}
footer{margin-top:20px;color:var(--muted);font-size:.85rem}
"""
