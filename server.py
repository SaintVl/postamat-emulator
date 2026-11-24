"""Serve a different simulated parcel locker journal on every GET /logs."""

from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from random import Random
import sqlite3
from urllib.parse import urlsplit

PORT = 8000
STATE_FILE = Path(__file__).with_name(".postamat-state.sqlite3")

CONFIRMED = "DOOR_OPEN_CONFIRMED"
TIMEOUT = "OPEN_CONFIRMATION_TIMEOUT"

# Each tuple describes the outcomes of successive attempts on one order.
# None means the journal ends without a final event for that attempt.
SCENARIOS = (
    (CONFIRMED,),
    (TIMEOUT,),
    (None,),
    (TIMEOUT, CONFIRMED),
    (TIMEOUT, TIMEOUT),
    (TIMEOUT, None),
    (TIMEOUT, TIMEOUT, CONFIRMED),
)


def initialize_sequence() -> None:
    with closing(sqlite3.connect(STATE_FILE)) as database, database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS journal_sequence "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), last_number INTEGER NOT NULL)"
        )
        database.execute(
            "INSERT OR IGNORE INTO journal_sequence (id, last_number) VALUES (1, 0)"
        )


def reserve_journal_number() -> int:
    # A write transaction reserves one number even when requests arrive together.
    with closing(sqlite3.connect(STATE_FILE, timeout=10)) as database, database:
        database.execute("UPDATE journal_sequence SET last_number = last_number + 1")
        return database.execute("SELECT last_number FROM journal_sequence").fetchone()[0]


def event_count(scenario: tuple[str | None, ...]) -> int:
    return sum(2 if outcome is None else 3 for outcome in scenario)


def generate_log(journal_number: int) -> str:
    random = Random()
    target = random.randint(40, 50)
    orders = [(TIMEOUT, CONFIRMED), (None,), (CONFIRMED,)]
    remaining = target - sum(map(event_count, orders))

    while remaining:
        choices = [
            scenario for scenario in SCENARIOS
            if event_count(scenario) <= remaining and remaining - event_count(scenario) != 1
        ]
        selected = random.choice(choices)
        orders.append(selected)
        remaining -= event_count(selected)

    random.shuffle(orders)
    log_date = datetime.now(timezone.utc).date()
    start = datetime.combine(log_date, time(8)) + timedelta(minutes=random.randrange(600))

    lines = []
    for order_index, outcomes in enumerate(orders, start=1):
        # Fewer than 100 orders fit in one journal, so ranges never overlap.
        order = f"A{journal_number * 100 + order_index:04d}"
        started = start + timedelta(seconds=random.randint(0, 150))
        for attempt, outcome in enumerate(outcomes, start=101):
            command_sent = started + timedelta(seconds=random.randint(1, 3))
            events = [
                (started, "CODE_ACCEPTED"),
                (command_sent, "OPEN_COMMAND_SENT"),
            ]
            if outcome is not None:
                delay = random.randint(4, 10) if outcome == TIMEOUT else random.randint(1, 3)
                finished = command_sent + timedelta(seconds=delay)
                events.append((finished, outcome))
                started = finished + timedelta(seconds=random.randint(3, 20))

            for timestamp, event in events:
                lines.append(f"{timestamp.isoformat()}|{order}|{attempt}|{event}")

    # Delivery order may differ from event time, as in the lesson examples.
    random.shuffle(lines)
    return "\n".join(lines) + "\n"


class LogRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/logs":
            self.send_text(404, "Для получения журнала откройте /logs.\n")
            return

        try:
            journal_number = reserve_journal_number()
        except sqlite3.Error:
            self.log_error("Could not reserve a journal number")
            self.send_text(503, "Журнал временно недоступен.\n")
            return

        self.send_text(200, generate_log(journal_number))

    def send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    initialize_sequence()
    with ThreadingHTTPServer(("0.0.0.0", PORT), LogRequestHandler) as server:
        print(f"Учебный журнал доступен на http://localhost:{PORT}/logs", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
