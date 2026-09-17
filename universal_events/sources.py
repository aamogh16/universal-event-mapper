"""Mock non-ecommerce event sources.

Each payload is shaped like what the business's *real* tool would send --
Calendly's nested `payload`, Mindbody's `eventData` envelope, Toast's GUIDs,
Bloomerang's flat snake_case. None of them look like Klaviyo events, which is
the entire point: the mapper has to do real work.

`UNKNOWN_PAYLOADS` are the live-demo money shot -- verticals with no config
file at all, handed to the inference engine cold.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Persona:
    """A stable fake customer.

    Stable on purpose: repeated demo triggers accumulate onto the same Klaviyo
    profile, which is what gives Part 2's agent a history to reason about.
    """

    email: str
    first_name: str
    last_name: str
    phone: str


@dataclass
class EventSample:
    key: str
    label: str
    build: Callable[[Persona], dict[str, Any]]


@dataclass
class Source:
    key: str
    display_name: str
    tool: str
    business_type: str
    personas: list[Persona]
    samples: list[EventSample] = field(default_factory=list)
    # None means "no config exists" -> forces the inference path.
    config_name: str | None = None

    def sample(self, key: str) -> EventSample:
        for item in self.samples:
            if item.key == key:
                return item
        raise KeyError(f"{self.key} has no sample {key!r}")

    def build(self, key: str, persona: Persona | None = None) -> dict[str, Any]:
        chosen = persona or random.choice(self.personas)
        return self.sample(key).build(chosen)


# --------------------------------------------------------------------------
# 1. Dental practice -- Calendly-style webhook
# --------------------------------------------------------------------------

DENTAL_PERSONAS = [
    Persona("maya.rodriguez@gmail.com", "Maya", "Rodriguez", "+16175550142"),
    Persona("dpark.home@outlook.com", "Daniel", "Park", "+16175550198"),
]


def _calendly_envelope(event: str, persona: Persona, payload_extra: dict) -> dict:
    scheduled = _now() + timedelta(days=random.randint(2, 21))
    base = {
        "created_at": _iso(_now()),
        "created_by": "https://api.calendly.com/users/AAAAAAAAAAAAAAAA",
        "event": event,
        "payload": {
            "uri": f"https://api.calendly.com/scheduled_events/{uuid.uuid4().hex[:16].upper()}/invitees/{uuid.uuid4().hex[:16].upper()}",
            "email": persona.email,
            "name": f"{persona.first_name} {persona.last_name}",
            "first_name": persona.first_name,
            "last_name": persona.last_name,
            "status": "active",
            "timezone": "America/New_York",
            "text_reminder_number": persona.phone,
            "rescheduled": False,
            "questions_and_answers": [
                {
                    "question": "Is this your first visit?",
                    "answer": random.choice(["Yes", "No"]),
                    "position": 0,
                },
                {
                    "question": "Dental insurance provider",
                    "answer": random.choice(["Delta Dental", "Cigna", "None"]),
                    "position": 1,
                },
            ],
            "scheduled_event": {
                "uri": f"https://api.calendly.com/scheduled_events/{uuid.uuid4().hex[:16].upper()}",
                "name": "Routine Cleaning & Exam (45 min)",
                "status": "active",
                "start_time": _iso(scheduled),
                "end_time": _iso(scheduled + timedelta(minutes=45)),
                "location": {
                    "type": "physical",
                    "location": "Beacon Hill Dental, 114 Charles St, Boston MA",
                },
                "event_memberships": [
                    {"user_name": "Dr. Alicia Chen", "user_email": "dr.chen@beaconhilldental.com"}
                ],
            },
        },
    }
    base["payload"].update(payload_extra)
    return base


DENTAL_SOURCE = Source(
    key="dental",
    display_name="Beacon Hill Dental",
    tool="Calendly",
    business_type="Dental practice / booking",
    personas=DENTAL_PERSONAS,
    config_name="dental_calendly",
    samples=[
        EventSample(
            "booked",
            "Appointment booked",
            lambda p: _calendly_envelope("invitee.created", p, {}),
        ),
        EventSample(
            "completed",
            "Appointment completed",
            lambda p: _calendly_envelope(
                "invitee.completed",
                p,
                {"status": "completed", "payment": {"amount": "180.00", "currency": "USD"}},
            ),
        ),
        EventSample(
            "no_show",
            "No-show",
            lambda p: _calendly_envelope(
                "invitee_no_show.created",
                p,
                {"no_show": {"created_at": _iso(_now())}},
            ),
        ),
    ],
)


# --------------------------------------------------------------------------
# 2. Fitness studio -- Mindbody-style webhook
# --------------------------------------------------------------------------

FITNESS_PERSONAS = [
    Persona("jordan.avery@gmail.com", "Jordan", "Avery", "+16175550231"),
    Persona("s.whitfield@protonmail.com", "Sam", "Whitfield", "+16175550277"),
]


def _mindbody_envelope(event_type: str, persona: Persona, data_extra: dict) -> dict:
    return {
        "messageId": str(uuid.uuid4()),
        "eventId": f"{event_type}.{uuid.uuid4().hex[:12]}",
        "eventSchemaVersion": 1,
        "eventInstanceOriginationDateTime": _iso(_now()),
        "eventType": event_type,
        "eventData": {
            "siteId": 574291,
            "locationId": 1,
            "clientId": "100042318",
            "clientUniqueId": 100042318,
            "firstName": persona.first_name,
            "lastName": persona.last_name,
            "email": persona.email,
            "mobilePhone": persona.phone,
            "status": "Active",
            **data_extra,
        },
    }


FITNESS_SOURCE = Source(
    key="fitness",
    display_name="Ironline Strength Co.",
    tool="Mindbody",
    business_type="Fitness studio / membership",
    personas=FITNESS_PERSONAS,
    config_name="fitness_mindbody",
    samples=[
        EventSample(
            "class_booked",
            "Class booked",
            lambda p: _mindbody_envelope(
                "classRosterBookingStatus.updated",
                p,
                {
                    "classId": 88213,
                    "className": random.choice(
                        ["Barbell Club 6am", "Conditioning 5:30pm", "Mobility 7am"]
                    ),
                    "classStartDateTime": _iso(_now() + timedelta(days=random.randint(1, 6))),
                    "staffName": "Coach Ray",
                    "signedIn": False,
                    "bookingStatus": "Booked",
                },
            ),
        ),
        EventSample(
            "membership_renewed",
            "Membership renewed",
            lambda p: _mindbody_envelope(
                "clientMembershipAssignment.created",
                p,
                {
                    "membershipName": "Unlimited Monthly",
                    "membershipId": 4417,
                    "paymentAmount": 159.0,
                    "currencyCode": "USD",
                    "activeDate": _iso(_now()),
                    "expirationDate": _iso(_now() + timedelta(days=30)),
                    "autoRenewing": True,
                },
            ),
        ),
        EventSample(
            "class_no_show",
            "Class no-show",
            lambda p: _mindbody_envelope(
                "classRosterBookingStatus.updated",
                p,
                {
                    "classId": 88219,
                    "className": "Conditioning 5:30pm",
                    "classStartDateTime": _iso(_now() - timedelta(hours=3)),
                    "staffName": "Coach Ray",
                    "signedIn": False,
                    "bookingStatus": "LateCancelled",
                    "lateCancelled": True,
                },
            ),
        ),
    ],
)


# --------------------------------------------------------------------------
# 3. Restaurant -- Toast-style (GUID-heavy) reservation payload
# --------------------------------------------------------------------------

RESTAURANT_PERSONAS = [
    Persona("t.okafor@gmail.com", "Tomi", "Okafor", "+16175550310"),
    Persona("hlin.reserve@icloud.com", "Helen", "Lin", "+16175550355"),
]


def _toast_envelope(entity_state: str, persona: Persona, extra: dict) -> dict:
    booked_for = _now() + timedelta(days=random.randint(1, 10))
    return {
        "guid": str(uuid.uuid4()),
        "entityType": "Reservation",
        "restaurantGuid": "8f2b1c44-7ad3-4e19-9c05-2a6de71b0f33",
        "restaurantName": "Camber & Rye",
        "state": entity_state,
        "modifiedDate": _iso(_now()),
        "reservation": {
            "partySize": random.randint(2, 6),
            "expectedArrival": _iso(booked_for),
            "durationMinutes": 90,
            "seatingArea": random.choice(["Main Dining", "Patio", "Bar"]),
            "specialRequests": random.choice(["", "Window table if possible", "Anniversary"]),
            "source": "TOAST_TABLES_WEB",
        },
        "guest": {
            "guestGuid": str(uuid.uuid4()),
            "firstName": persona.first_name,
            "lastName": persona.last_name,
            "emailAddress": persona.email,
            "phoneNumber": persona.phone,
            "loyaltyEnrolled": random.choice([True, False]),
            "visitCount": random.randint(1, 14),
        },
        **extra,
    }


RESTAURANT_SOURCE = Source(
    key="restaurant",
    display_name="Camber & Rye",
    tool="Toast Tables",
    business_type="Restaurant / reservations",
    personas=RESTAURANT_PERSONAS,
    config_name="restaurant_toast",
    samples=[
        EventSample(
            "reservation_made",
            "Reservation made",
            lambda p: _toast_envelope("BOOKED", p, {}),
        ),
        EventSample(
            "reservation_completed",
            "Reservation completed",
            lambda p: _toast_envelope(
                "COMPLETED",
                p,
                {
                    "check": {
                        "checkGuid": str(uuid.uuid4()),
                        "totalAmount": round(random.uniform(64, 240), 2),
                        "currency": "USD",
                        "tipAmount": round(random.uniform(10, 45), 2),
                    }
                },
            ),
        ),
        EventSample(
            "reservation_no_show",
            "No-show",
            lambda p: _toast_envelope(
                "NO_SHOW", p, {"noShowRecordedAt": _iso(_now())}
            ),
        ),
    ],
)


# --------------------------------------------------------------------------
# 4. Nonprofit -- Bloomerang-style CRM payload
# --------------------------------------------------------------------------

NONPROFIT_PERSONAS = [
    Persona("rwilliams.give@gmail.com", "Renee", "Williams", "+16175550422"),
    Persona("marcus.bell@yahoo.com", "Marcus", "Bell", "+16175550489"),
]


def _bloomerang_envelope(record_type: str, persona: Persona, extra: dict) -> dict:
    return {
        "Id": random.randint(400000, 499999),
        "RecordType": record_type,
        "AccountId": random.randint(90000, 99999),
        "Constituent": {
            "Id": random.randint(90000, 99999),
            "FirstName": persona.first_name,
            "LastName": persona.last_name,
            "PrimaryEmail": {"Value": persona.email, "Type": "Home"},
            "PrimaryPhone": {"Value": persona.phone, "Type": "Mobile"},
            "Type": "Individual",
        },
        "CreatedDate": _iso(_now()),
        **extra,
    }


NONPROFIT_SOURCE = Source(
    key="nonprofit",
    display_name="Harborlight Youth Coalition",
    tool="Bloomerang",
    business_type="Nonprofit / donations",
    personas=NONPROFIT_PERSONAS,
    config_name="nonprofit_bloomerang",
    samples=[
        EventSample(
            "donation",
            "Donation made",
            lambda p: _bloomerang_envelope(
                "Donation",
                p,
                {
                    "Amount": float(random.choice([25, 50, 100, 250, 500])),
                    "CurrencyCode": "USD",
                    "Method": random.choice(["CreditCard", "ACH"]),
                    "IsRecurring": random.choice([True, False]),
                    "Fund": {"Id": 12, "Name": "After-School Program"},
                    "Campaign": {"Id": 7, "Name": "Fall Giving Drive 2026"},
                    "Designation": "Unrestricted",
                },
            ),
        ),
        EventSample(
            "event_rsvp",
            "Event RSVP",
            lambda p: _bloomerang_envelope(
                "EventRegistration",
                p,
                {
                    "EventName": "Harborlight Annual Gala",
                    "EventDate": _iso(_now() + timedelta(days=45)),
                    "AttendeeCount": random.randint(1, 4),
                    "TicketLevel": random.choice(["General", "Patron", "Table Host"]),
                    "AmountPaid": float(random.choice([0, 75, 150])),
                    "CurrencyCode": "USD",
                },
            ),
        ),
    ],
)


SOURCES: dict[str, Source] = {
    source.key: source
    for source in (DENTAL_SOURCE, FITNESS_SOURCE, RESTAURANT_SOURCE, NONPROFIT_SOURCE)
}


# --------------------------------------------------------------------------
# Unknown payloads -- no config, no prior knowledge. The generalization proof.
# --------------------------------------------------------------------------

UNKNOWN_PAYLOADS: dict[str, dict[str, Any]] = {
    "veterinary": {
        "_description": "Veterinary clinic, Vetstoria-style. Identifier is nested "
        "under the pet's owner, not the top level.",
        "payload": {
            "booking_ref": "VS-2026-88431",
            "practice": {"id": 4417, "name": "Riverbend Animal Hospital"},
            "appointment_type": "Wellness Exam + Vaccination",
            "starts": "2026-10-02T14:30:00-04:00",
            "vet": "Dr. Imani Osei",
            "patient": {
                "name": "Biscuit",
                "species": "Canine",
                "breed": "Beagle",
                "age_years": 4,
                "owner": {
                    "contact_email": "leah.fitzgerald@gmail.com",
                    "contact_mobile": "+16175550517",
                    "display_name": "Leah Fitzgerald",
                    "client_since": "2023-06-11",
                },
            },
            "estimated_cost": {"amount": 145.0, "iso_currency": "USD"},
            "status": "confirmed",
        },
    },
    "tutoring": {
        "_description": "Tutoring company. Uses `learner` + `guardian`; the "
        "billable contact is the guardian, not the student.",
        "payload": {
            "session_uuid": "b21e7f90-4c88-11f0-9cd2-0242ac120002",
            "org": "Northgate Test Prep",
            "session": {
                "subject": "SAT Math",
                "mode": "in_person",
                "scheduled_start": "2026-09-24T18:00:00Z",
                "length_minutes": 90,
                "tutor_name": "Priya Raghavan",
                "outcome": "attended",
            },
            "learner": {"first": "Ethan", "last": "Brooks", "grade": 11},
            "guardian": {
                "first": "Danielle",
                "last": "Brooks",
                "email_address": "dbrooks.family@gmail.com",
                "sms": "+16175550603",
            },
            "invoice": {"charged": 120.0, "currency": "usd", "package_sessions_left": 7},
        },
    },
    "physical_therapy": {
        "_description": "Physical therapy clinic. Deeply nested, HL7-ish naming, "
        "and the email key is spelled unusually.",
        "payload": {
            "encounterId": "ENC-773311",
            "facility": {"npi": "1487652301", "label": "Coastline Physical Therapy"},
            "encounter": {
                "visitType": "Follow-up",
                "planOfCare": "Post-op ACL rehab",
                "visitNumber": 6,
                "authorizedVisits": 12,
                "occurredAt": "2026-09-16T09:15:00-04:00",
                "clinician": {"name": "Marco Devlin, DPT", "credential": "DPT"},
                "dispositionCode": "COMPLETED",
            },
            "subject": {
                "given": "Nadia",
                "family": "Hassan",
                "telecom": [
                    {"system": "email", "value": "nadia.hassan88@gmail.com", "use": "home"},
                    {"system": "phone", "value": "+16175550744", "use": "mobile"},
                ],
            },
            "billing": {"patientResponsibility": 35.0, "currency": "USD"},
        },
    },
}


def list_sources() -> list[Source]:
    return list(SOURCES.values())


def get_source(key: str) -> Source:
    if key not in SOURCES:
        raise KeyError(f"Unknown source {key!r}. Known: {', '.join(SOURCES)}")
    return SOURCES[key]
