"""Deterministic demo history.

Seeded history is written LOCALLY ONLY -- it never goes to Klaviyo. Two
reasons: a few hundred backdated events would pollute the real account, and
the free plan caps at 250 profiles. Events fired live during the demo DO go
to Klaviyo for real. That split is deliberate.

The scenarios below are built so the agent's findings are genuinely true of
the data rather than hardcoded. Each one exists to make a specific demo beat
work:

  A. Jordan Avery stopped booking 23 days ago
     -> the scheduled sweep finds a lapsed member
  B. The 5:30pm class no-show rate tripled over 14 days
     -> the scheduled sweep finds an aggregate trend (not a person problem)
  C. Sam Whitfield already has 2 recent no-shows
     -> firing a 3rd live during the demo trips the trigger
  D. Renee Williams last donated 95 days ago after 3 prior gifts
     -> a lapsed-donor proposal in a second vertical, for the reject demo

Everything uses a fixed RNG seed so the demo starts identically every time.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import store
from .mapping.base import MappingResult, ProfileIdentity

RNG_SEED = 20260917
CLASS_5_30 = "Conditioning 5:30pm"
OTHER_CLASSES = ("Barbell Club 6am", "Mobility 7am", "Olympic Lifting 6pm")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return _now() - timedelta(days=days)


def _event(
    *,
    email: str,
    first: str,
    last: str,
    metric: str,
    when: datetime,
    source: str,
    properties: dict | None = None,
    value: float | None = None,
) -> None:
    store.record_event(
        MappingResult(
            metric_name=metric,
            identity=ProfileIdentity(email=email, first_name=first, last_name=last),
            properties=properties or {},
            value=value,
            value_currency="USD" if value else None,
            occurred_at=when,
            strategy="config",
            reasoning="seeded demo history",
        ),
        source_key=source,
    )


def _seed_fitness(rng: random.Random) -> int:
    written = 0
    members = [
        ("jordan.avery@gmail.com", "Jordan", "Avery"),
        ("s.whitfield@protonmail.com", "Sam", "Whitfield"),
        ("nina.castellanos@gmail.com", "Nina", "Castellanos"),
        ("owen.brady@outlook.com", "Owen", "Brady"),
        ("priya.n@gmail.com", "Priya", "Nair"),
        ("t.duval@icloud.com", "Theo", "Duval"),
        ("k.osei@gmail.com", "Kwame", "Osei"),
        ("lena.fischer@gmail.com", "Lena", "Fischer"),
        ("m.ibrahim@yahoo.com", "Mina", "Ibrahim"),
        ("gabe.romero@gmail.com", "Gabe", "Romero"),
        ("h.tanaka@gmail.com", "Haruki", "Tanaka"),
        ("erin.walsh@outlook.com", "Erin", "Walsh"),
    ]

    # --- Scenario A: Jordan books steadily, then stops 23 days ago ----------
    email, first, last = members[0]
    day = 88.0
    while day >= 24:
        _event(
            email=email, first=first, last=last,
            metric="Class Booked", when=_ago(day), source="fitness",
            properties={"Class Name": rng.choice(OTHER_CLASSES), "Coach": "Coach Ray"},
        )
        written += 1
        day -= rng.choice([3.0, 3.5, 4.0])
    _event(
        email=email, first=first, last=last,
        metric="Membership Renewed", when=_ago(26), source="fitness",
        properties={"Membership Name": "Unlimited Monthly", "Auto Renewing": True},
        value=159.0,
    )
    written += 1

    # --- Scenario B: 5:30pm no-show rate triples in the last 14 days --------
    # Prior window (days 15-28): 40 bookings / 4 no-shows  = 10%
    # Recent window (days 0-14):  38 bookings / 12 no-shows = 32%  (~3.2x)
    for count, metric, lo, hi in (
        (40, "Class Booked", 15, 28),
        (4, "Class No-Show", 15, 28),
        (38, "Class Booked", 0, 14),
        (12, "Class No-Show", 0, 14),
    ):
        for _ in range(count):
            email, first, last = rng.choice(members[2:])
            _event(
                email=email, first=first, last=last,
                metric=metric, when=_ago(rng.uniform(lo, hi)), source="fitness",
                properties={"Class Name": CLASS_5_30, "Coach": "Coach Ray"},
            )
            written += 1

    # --- Scenario C: Sam has 2 recent no-shows; a 3rd fires live ------------
    email, first, last = members[1]
    for days_back in (11, 4):
        _event(
            email=email, first=first, last=last,
            metric="Class No-Show", when=_ago(days_back), source="fitness",
            properties={"Class Name": CLASS_5_30, "Coach": "Coach Ray"},
        )
        written += 1
    for days_back in (30, 24, 18, 13, 6):
        _event(
            email=email, first=first, last=last,
            metric="Class Booked", when=_ago(days_back), source="fitness",
            properties={"Class Name": CLASS_5_30, "Coach": "Coach Ray"},
        )
        written += 1

    # Background: healthy members booking other classes.
    for email, first, last in members[2:]:
        for _ in range(rng.randint(4, 9)):
            _event(
                email=email, first=first, last=last,
                metric="Class Booked", when=_ago(rng.uniform(0, 60)), source="fitness",
                properties={"Class Name": rng.choice(OTHER_CLASSES)},
            )
            written += 1
    return written


def _seed_nonprofit(rng: random.Random) -> int:
    written = 0
    # --- Scenario D: Renee is a reliable donor who has gone quiet ----------
    for days_back, amount in ((420, 100.0), (250, 150.0), (95, 250.0)):
        _event(
            email="rwilliams.give@gmail.com", first="Renee", last="Williams",
            metric="Donation Made", when=_ago(days_back), source="nonprofit",
            properties={"Fund": "After-School Program", "Recurring Donor": False},
            value=amount,
        )
        written += 1

    _event(
        email="marcus.bell@yahoo.com", first="Marcus", last="Bell",
        metric="Donation Made", when=_ago(12), source="nonprofit",
        properties={"Fund": "After-School Program", "Recurring Donor": True},
        value=50.0,
    )
    _event(
        email="marcus.bell@yahoo.com", first="Marcus", last="Bell",
        metric="Event RSVP", when=_ago(5), source="nonprofit",
        properties={"Event Name": "Harborlight Annual Gala", "Ticket Level": "Patron"},
        value=150.0,
    )
    written += 2

    for i, (email, first, last) in enumerate(
        [
            ("c.nguyen@gmail.com", "Cam", "Nguyen"),
            ("d.abramov@outlook.com", "Dina", "Abramov"),
            ("f.silva@gmail.com", "Felipe", "Silva"),
        ]
    ):
        for _ in range(rng.randint(2, 4)):
            _event(
                email=email, first=first, last=last,
                metric="Donation Made", when=_ago(rng.uniform(10, 300)),
                source="nonprofit",
                properties={"Fund": "General Operating"},
                value=float(rng.choice([25, 50, 100])),
            )
            written += 1
    return written


def _seed_dental(rng: random.Random) -> int:
    written = 0
    # Overdue recall: cleaning was 205 days ago, recall interval is 180 days.
    for days_back in (565, 385, 205):
        _event(
            email="maya.rodriguez@gmail.com", first="Maya", last="Rodriguez",
            metric="Appointment Completed", when=_ago(days_back), source="dental",
            properties={"Appointment Type": "Routine Cleaning & Exam",
                        "Provider": "Dr. Alicia Chen"},
            value=180.0,
        )
        written += 1
    _event(
        email="dpark.home@outlook.com", first="Daniel", last="Park",
        metric="Appointment No-Show", when=_ago(9), source="dental",
        properties={"Appointment Type": "Routine Cleaning & Exam"},
    )
    _event(
        email="dpark.home@outlook.com", first="Daniel", last="Park",
        metric="Appointment Booked", when=_ago(20), source="dental",
        properties={"Appointment Type": "Routine Cleaning & Exam"},
    )
    written += 2
    return written


def _seed_restaurant(rng: random.Random) -> int:
    written = 0
    guests = [
        ("t.okafor@gmail.com", "Tomi", "Okafor"),
        ("hlin.reserve@icloud.com", "Helen", "Lin"),
    ]
    for email, first, last in guests:
        for _ in range(rng.randint(3, 6)):
            _event(
                email=email, first=first, last=last,
                metric="Reservation Completed", when=_ago(rng.uniform(5, 180)),
                source="restaurant",
                properties={"Party Size": rng.randint(2, 6), "Seating Area": "Main Dining"},
                value=round(rng.uniform(70, 210), 2),
            )
            written += 1
    _event(
        email=guests[0][0], first=guests[0][1], last=guests[0][2],
        metric="Reservation No-Show", when=_ago(3), source="restaurant",
        properties={"Party Size": 4, "Seating Area": "Patio"},
    )
    written += 1
    return written


def seed_demo_history(*, reset: bool = True) -> dict[str, int]:
    """Build the full demo dataset. Returns per-vertical event counts."""
    if reset:
        store.reset()
    else:
        store.init_db()
    rng = random.Random(RNG_SEED)
    counts = {
        "fitness": _seed_fitness(rng),
        "nonprofit": _seed_nonprofit(rng),
        "dental": _seed_dental(rng),
        "restaurant": _seed_restaurant(rng),
    }
    counts["total"] = sum(counts.values())
    return counts
