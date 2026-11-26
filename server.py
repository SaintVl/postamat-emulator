"""Учебный эмулятор постамата."""

from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from random import Random
import sqlite3
from urllib.parse import urlsplit


PORT = 8000
STATE_FILE = Path(__file__).with_name(".postamat-state.sqlite3")
EVENTS_PER_JOURNAL = 100

CODE_REJECTED = ("CODE_REJECTED",)
CODE_ACCEPTED = ("CODE_ACCEPTED",)
COMMAND_SENT = (*CODE_ACCEPTED, "OPEN_COMMAND_SENT")
CANCELLED = (*CODE_ACCEPTED, "PICKUP_CANCELLED")
COMMAND_FAILED = (*CODE_ACCEPTED, "OPEN_COMMAND_FAILED")
DOOR_FAILED = (*COMMAND_SENT, "DOOR_OPEN_FAILED")
TIMED_OUT = (*COMMAND_SENT, "OPEN_CONFIRMATION_TIMEOUT")
OPENED = (*COMMAND_SENT, "DOOR_OPEN_CONFIRMED")
CLOSED = (*OPENED, "PARCEL_REMOVED", "DOOR_CLOSED_CONFIRMED")
LEFT_OPEN = (*OPENED, "DOOR_LEFT_OPEN")
CLOSED_LATE = (*LEFT_OPEN, "PARCEL_REMOVED", "DOOR_CLOSED_CONFIRMED")

# Каждый заказ содержит одну или несколько попыток выдачи.
REQUIRED_ORDERS = (
    (CODE_REJECTED, TIMED_OUT, CLOSED_LATE),
    (CANCELLED,),
    (COMMAND_FAILED,),
    (DOOR_FAILED,),
    (COMMAND_SENT,),
    (LEFT_OPEN,),
)

RANDOM_ORDERS = (
    (CODE_REJECTED,),
    (CANCELLED,),
    (COMMAND_FAILED,),
    (COMMAND_SENT,),
    (DOOR_FAILED,),
    (TIMED_OUT,),
    (OPENED,),
    (CLOSED,),
    (LEFT_OPEN,),
    (CLOSED_LATE,),
    (CODE_REJECTED, OPENED),
    (TIMED_OUT, CLOSED),
    (TIMED_OUT, TIMED_OUT),
    (TIMED_OUT, COMMAND_SENT),
    (TIMED_OUT, DOOR_FAILED, CLOSED),
)

EVENT_DELAY_SECONDS = {
    "OPEN_CONFIRMATION_TIMEOUT": (4, 10),
    "DOOR_OPEN_FAILED": (4, 10),
    "DOOR_LEFT_OPEN": (30, 60),
}


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
    with closing(sqlite3.connect(STATE_FILE, timeout=10)) as database, database:
        database.execute("UPDATE journal_sequence SET last_number = last_number + 1")
        return database.execute(
            "SELECT last_number FROM journal_sequence"
        ).fetchone()[0]


def event_count(order: tuple[tuple[str, ...], ...]) -> int:
    return sum(len(attempt) for attempt in order)


def generate_log(journal_number: int) -> str:
    random = Random()
    orders = list(REQUIRED_ORDERS)
    remaining = EVENTS_PER_JOURNAL - sum(map(event_count, orders))

    while remaining > 0:
        candidates = [order for order in RANDOM_ORDERS if event_count(order) <= remaining]
        order = random.choice(candidates)
        orders.append(order)
        remaining -= event_count(order)

    random.shuffle(orders)
    log_date = datetime.now(timezone.utc).date()
    start = datetime.combine(log_date, time(8)) + timedelta(minutes=random.randrange(600))

    lines = []
    for order_index, attempts in enumerate(orders, start=1):
        order_number = f"A{journal_number * 100 + order_index:04d}"
        moment = start + timedelta(seconds=random.randint(0, 150))

        for attempt_number, events in enumerate(attempts, start=101):
            for event_index, event in enumerate(events):
                if event_index > 0:
                    minimum, maximum = EVENT_DELAY_SECONDS.get(event, (1, 3))
                    moment += timedelta(seconds=random.randint(minimum, maximum))

                lines.append(f"{moment.isoformat()}|{order_number}|{attempt_number}|{event}")

            moment += timedelta(seconds=random.randint(3, 20))

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
