"""notify.py -- format a completed week's summary into an HTML email and send it via Gmail.

Reads the weekly summary that analysis.py already computes (no new analysis
logic) and emails it with smtplib from the standard library (no new
dependencies, no paid service). Credentials come from .env:
    EMAIL_ADDRESS        the Gmail account that sends (and, by default, receives)
    EMAIL_APP_PASSWORD   a Google *app password* (16 chars), NOT your real password
    EMAIL_TO             optional; defaults to EMAIL_ADDRESS (send to yourself)
Called by weekly_check.py once per completed week.
"""

import os
import smtplib
from email.message import EmailMessage

import pandas as pd
from dotenv import load_dotenv

from analysis import HOME_TZ
from report import COLOR_BAD, COLOR_GOOD, GOAL_MODE

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_SSL_PORT = 465


def _goal_direction(metric: str) -> int:
    """Which delta sign is 'good' for a metric (-1 lower better, +1 higher, 0 neutral).

    Mirrors report.py's _goal_direction so the email colors deltas the same
    way the report does: weight/bmi follow GOAL_MODE, bodyfat is always
    "lower is better."
    """
    if metric == "bodyfat":
        return -1
    if metric in ("weight_lb", "bmi"):
        return {"cut": -1, "bulk": 1, "neutral": 0}[GOAL_MODE]
    return 0


def _delta_html(metric: str, value: float | None, unit: str) -> str:
    """Render one week-over-week delta as a colored, arrowed HTML snippet for the email body.

    Same rules as the report's delta cells: NaN/None -> em dash, a change that
    rounds to 0.0 -> neutral "no change", otherwise an up/down arrow colored
    green/red by whether the move was in the goal's good direction.
    """
    if value is None or pd.isna(value):
        return '<span style="color:#898781">&mdash;</span>'
    if round(value, 1) == 0:
        return '<span style="color:#898781">no change</span>'

    # HTML entities, not raw unicode glyphs -- keeps the email body pure ASCII
    # so it renders correctly in any client regardless of charset guessing
    # (&#9650; up-triangle, &#9660; down-triangle).
    arrow = "&#9650;" if value > 0 else "&#9660;"
    good_dir = _goal_direction(metric)
    if good_dir == 0:
        color = "#898781"
    else:
        moved_good = (value < 0 and good_dir < 0) or (value > 0 and good_dir > 0)
        color = COLOR_GOOD if moved_good else COLOR_BAD
    return f'<span style="color:{color}">{arrow} {abs(value):.1f}{unit}</span>'


def _fmt(value: float | None, unit: str = "") -> str:
    """Format a metric value to 1 decimal for the email, or an em dash if it's missing that week."""
    if value is None or pd.isna(value):
        return '<span style="color:#898781">&mdash;</span>'
    return f"{value:.1f}{unit}"


def _email_config() -> tuple[str, str, str]:
    """Load (sender, app_password, recipient) from .env; recipient defaults to the sender."""
    load_dotenv()
    address = os.getenv("EMAIL_ADDRESS")
    app_password = os.getenv("EMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "Missing EMAIL_ADDRESS or EMAIL_APP_PASSWORD in .env -- "
            "email cannot be sent."
        )
    recipient = os.getenv("EMAIL_TO") or address
    return address, app_password, recipient


def target_week_ending(weekly: pd.DataFrame) -> str | None:
    """Return the ISO Saturday date of the most recent *completed* week, or None if there isn't one.

    A week is complete once its Saturday is strictly before today's date in
    HOME_TZ (the same boundary report.py uses to mark a week 'in progress',
    just inverted). weekly_check.py uses this both to check has_notified and,
    if unsent, to build the email for that week.
    """
    today = pd.Timestamp.now(tz=HOME_TZ).date()
    completed = weekly[weekly.index.map(lambda d: d.date() < today)]
    if completed.empty:
        return None
    return completed.index[-1].strftime("%Y-%m-%d")


def _row_dict(row: pd.Series, week_ending: pd.Timestamp) -> dict:
    """Pull the display fields for one weekly row into a plain dict (with a full 'Week of' label)."""
    week_start = week_ending - pd.Timedelta(days=6)
    return {
        "label": f"{week_start.strftime('%b %-d')} &ndash; {week_ending.strftime('%b %-d, %Y')}",
        "readings": int(row["readings"]),
        "weight_lb": row["weight_lb"],
        "bodyfat": row["bodyfat"],
        "bmi": row["bmi"],
        "weight_lb_delta": row["weight_lb_delta"],
        "bodyfat_delta": row["bodyfat_delta"],
        "bmi_delta": row["bmi_delta"],
    }


def build_email(weekly: pd.DataFrame, week_ending_iso: str) -> tuple[str, str]:
    """Build (subject, html_body) for the given completed week, comparing it to the prior week.

    Looks the target week up by its Saturday date in `weekly`, grabs the row
    before it for the "last week" column, and lays out a small HTML table
    (This week / Last week / Change) that renders inline in the email so the
    numbers are readable without opening anything else.
    """
    week_ending = pd.Timestamp(week_ending_iso)
    pos = weekly.index.get_loc(week_ending)
    this_week = _row_dict(weekly.iloc[pos], week_ending)
    last_week = _row_dict(weekly.iloc[pos - 1], weekly.index[pos - 1]) if pos > 0 else None

    def col(week, key, unit=""):
        return _fmt(week[key], unit) if week else '<span style="color:#898781">&mdash;</span>'

    rows = [
        ("Weight", "weight_lb", " lb"),
        ("Body fat", "bodyfat", "%"),
        ("BMI", "bmi", ""),
    ]
    table_rows = ""
    for name, key, unit in rows:
        table_rows += (
            "<tr>"
            f'<td style="padding:6px 14px 6px 0;font-weight:600">{name}</td>'
            f'<td style="padding:6px 14px;text-align:right">{col(this_week, key, unit)}</td>'
            f'<td style="padding:6px 14px;text-align:right;color:#898781">{col(last_week, key, unit)}</td>'
            f'<td style="padding:6px 0 6px 14px;text-align:right">{_delta_html(key, this_week[key + "_delta"], unit)}</td>'
            "</tr>"
        )

    weight_delta = this_week["weight_lb_delta"]
    delta_str = _subject_delta(weight_delta)
    subject = f"RENPHO weekly: {this_week['weight_lb']:.1f} lb{delta_str}"

    html_body = f"""\
<div style="font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0b0b0b;max-width:520px">
  <h2 style="margin:0 0 4px">Weekly weight summary</h2>
  <p style="margin:0 0 16px;color:#52514e">{this_week['label']} &middot; {this_week['readings']} weigh-in(s)</p>
  <table style="border-collapse:collapse;font-size:15px">
    <thead>
      <tr style="color:#52514e;font-size:13px">
        <th style="text-align:left;padding:0 14px 6px 0"></th>
        <th style="text-align:right;padding:0 14px 6px">This week</th>
        <th style="text-align:right;padding:0 14px 6px">Last week</th>
        <th style="text-align:right;padding:0 0 6px 14px">Change</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
  <p style="margin:20px 0 0;color:#898781;font-size:13px">
    Weekly averages over the days you logged. Run <code>python run.py</code> for the full interactive report.
  </p>
</div>"""
    return subject, html_body


def _subject_delta(weight_delta: float | None) -> str:
    """Format the weight change for the subject line, e.g. ' (▼1.2 from last week)'. Empty if none."""
    if weight_delta is None or pd.isna(weight_delta) or round(weight_delta, 1) == 0:
        return ""
    arrow = "▲" if weight_delta > 0 else "▼"
    return f" ({arrow}{abs(weight_delta):.1f} from last week)"


def send_email(subject: str, html_body: str) -> None:
    """Send one HTML email via Gmail's SMTP over SSL, using the app password from .env.

    Raises on any failure (bad credentials, network, etc.) so the caller
    (weekly_check.py) can log it and NOT mark the week as notified -- so a
    failed send is retried on the next run rather than silently lost.
    """
    address, app_password, recipient = _email_config()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient
    msg.set_content("This is an HTML email; view it in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_SSL_PORT) as server:
        server.login(address, app_password)
        server.send_message(msg)


if __name__ == "__main__":
    # Standalone test: build and send the email for the latest completed week,
    # ignoring the once-per-week guard (that lives in weekly_check.py). Lets us
    # verify formatting + delivery without waiting for a real Sunday.
    from analysis import dedupe_to_daily, load_measurements, weekly_summary
    from store import get_connection

    conn = get_connection()
    weekly = weekly_summary(dedupe_to_daily(load_measurements(conn)))
    week_ending_iso = target_week_ending(weekly)
    if week_ending_iso is None:
        print("No completed week to summarize yet.")
    else:
        subject, html_body = build_email(weekly, week_ending_iso)
        send_email(subject, html_body)
        print(f"Sent test summary for week ending {week_ending_iso}: {subject!r}")
