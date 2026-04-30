import os
import random
import hashlib
import pendulum
from flask import Flask, jsonify, request
from otf_api import Otf, OtfUser
from otf_api.models.bookings import BookingStatus

app = Flask(__name__)

# Cache is now per user, so Brian's widget will never receive Chris's cached class data.
CACHE = {}

CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))

PREFERRED_COACH_NAMES = [
    "toni",
    "vassar",
    "ki",
    "jon",
    "carmine",
    "sydney",
    "ashlee",
    "natasha",
    "lily",
]

PRIVACY_NOTE = (
    "Your Orangetheory email and password are used only to fetch your widget data. "
    "This backend does not save your password or write it to a database."
)

def clean_value(value):
    return (value or "").strip()

def get_request_credentials():
    """
    Credentials should come from Scriptable request headers.
    This keeps credentials out of the shared code and out of the public GitHub repo.
    """
    email = clean_value(request.headers.get("X-OTF-Email"))
    password = clean_value(request.headers.get("X-OTF-Password"))

    if not email or not password:
        return None, None

    return email, password

def cache_key_for_email(email):
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()

def create_otf_client(email, password):
    if not email:
        raise ValueError("Missing Orangetheory email.")

    if not password:
        raise ValueError("Missing Orangetheory password.")

    return Otf(user=OtfUser(email, password))

def coach_name(coach):
    if not coach:
        return "Not listed"

    first = getattr(coach, "first_name", "")
    last = getattr(coach, "last_name", "")
    full_name = f"{first} {last}".strip()

    return full_name if full_name else str(coach)

def get_lifetime_classes(otf):
    try:
        return otf.member.class_summary.total_classes_attended
    except Exception:
        return None

def get_studio_uuid_from_obj(studio):
    for attr in ["studio_uuid", "uuid", "id"]:
        value = getattr(studio, attr, None)
        if value:
            return value

    if isinstance(studio, dict):
        for key in ["studio_uuid", "studioUUId", "uuid", "id"]:
            if studio.get(key):
                return studio.get(key)

    return None

def get_favorite_and_home_studio_uuids(otf):
    studio_uuids = set()

    try:
        if getattr(otf, "home_studio_uuid", None):
            studio_uuids.add(otf.home_studio_uuid)
    except Exception:
        pass

    try:
        favorites = otf.get_favorite_studios()
        for studio in favorites:
            uuid = get_studio_uuid_from_obj(studio)
            if uuid:
                studio_uuids.add(uuid)
    except Exception:
        pass

    return list(studio_uuids)

def collect_coach_images_from_favorite_studios(otf):
    coach_images = {}

    try:
        studio_uuids = get_favorite_and_home_studio_uuids(otf)

        if not studio_uuids:
            return coach_images

        raw_classes = otf.bookings.client.get_classes(studio_uuids)

        for raw_class in raw_classes:
            coach = raw_class.get("coach") or {}
            first_name = (coach.get("first_name") or "").strip()
            image_url = coach.get("image_url")

            if not first_name or not image_url:
                continue

            key = first_name.lower()

            if key not in coach_images:
                coach_images[key] = image_url

    except Exception:
        pass

    return coach_images

def same_class_time(raw_class, otf_class):
    try:
        studio_tz = otf_class.studio.time_zone
        raw_start = pendulum.parse(raw_class.get("starts_at")).in_timezone(studio_tz)
        model_start = pendulum.instance(otf_class.starts_at, tz=studio_tz)

        return (
            raw_start.year == model_start.year and
            raw_start.month == model_start.month and
            raw_start.day == model_start.day and
            raw_start.hour == model_start.hour and
            raw_start.minute == model_start.minute
        )
    except Exception:
        return False

def find_coach_image_url_for_class(otf, otf_class):
    try:
        studio_uuid = otf_class.studio.studio_uuid
        raw_classes = otf.bookings.client.get_classes([studio_uuid])

        for raw_class in raw_classes:
            raw_name = raw_class.get("name", "").lower()
            model_name = otf_class.name.lower()

            if raw_name == model_name and same_class_time(raw_class, otf_class):
                coach = raw_class.get("coach") or {}
                return coach.get("image_url")

    except Exception:
        return None

    return None

def fetch_next_class_data(email, password):
    otf = create_otf_client(email, password)

    lifetime_classes = get_lifetime_classes(otf)

    today = pendulum.today().date()
    future = pendulum.today().add(months=2).date()

    booked = otf.bookings.get_bookings(
        start_date=today,
        end_date=future,
        status=BookingStatus.Booked,
        exclude_cancelled=True,
        exclude_checkedin=True,
    )

    waitlisted = otf.bookings.get_bookings(
        start_date=today,
        end_date=future,
        status=BookingStatus.Waitlisted,
        exclude_cancelled=True,
        exclude_checkedin=True,
    )

    combined = booked + waitlisted

    # Remove classes that already started.
    now = pendulum.now()
    future_combined = []

    for booking in combined:
        try:
            starts_at = pendulum.instance(booking.otf_class.starts_at)
            if starts_at > now:
                future_combined.append(booking)
        except Exception:
            future_combined.append(booking)

    combined = future_combined

    coach_images = collect_coach_images_from_favorite_studios(otf)

    if not combined:
        available_preferred = [
            coach_images[name]
            for name in PREFERRED_COACH_NAMES
            if name in coach_images and coach_images[name]
        ]

        random_coach_image = random.choice(available_preferred) if available_preferred else None

        if not random_coach_image and coach_images:
            random_coach_image = random.choice(list(coach_images.values()))

        return {
            "has_class": False,
            "class_name": None,
            "starts_display": None,
            "studio": None,
            "coach": None,
            "coach_image_url": random_coach_image,
            "coach_images": coach_images,
            "status": "No upcoming classes",
            "lifetime_classes": lifetime_classes,
            "last_checked": pendulum.now().format("h:mm A"),
            "privacy_note": PRIVACY_NOTE,
        }

    combined = sorted(combined, key=lambda b: b.otf_class.starts_at)
    booking = combined[0]
    otf_class = booking.otf_class

    waitlist_position = getattr(booking, "waitlist_position", None)

    if str(booking.status) == "Waitlisted" and waitlist_position is not None:
        status = f"Waitlist #{waitlist_position}"
    else:
        status = str(booking.status)

    starts_at = pendulum.instance(otf_class.starts_at)
    coach = getattr(otf_class, "coach", None)
    coach_image_url = find_coach_image_url_for_class(otf, otf_class)

    if not coach_image_url and coach:
        coach_first = getattr(coach, "first_name", "")
        coach_key = coach_first.strip().lower()
        coach_image_url = coach_images.get(coach_key)

    return {
        "has_class": True,
        "class_name": otf_class.name,
        "starts_display": starts_at.format("ddd, MMM D [at] h:mm A"),
        "studio": otf_class.studio.name,
        "coach": coach_name(coach),
        "coach_image_url": coach_image_url,
        "coach_images": coach_images,
        "status": status,
        "lifetime_classes": lifetime_classes,
        "last_checked": pendulum.now().format("h:mm A"),
        "privacy_note": PRIVACY_NOTE,
    }

def get_cached_next_class_data(email, password, force_refresh=False):
    now = pendulum.now()
    user_cache_key = cache_key_for_email(email)

    cached = CACHE.get(user_cache_key)

    if (
        not force_refresh and
        cached is not None and
        cached.get("data") is not None and
        cached.get("fetched_at") is not None and
        (now - cached["fetched_at"]).total_seconds() < CACHE_SECONDS
    ):
        return cached["data"]

    data = fetch_next_class_data(email, password)

    CACHE[user_cache_key] = {
        "data": data,
        "fetched_at": now,
    }

    return data

def credentials_required_response():
    return jsonify({
        "has_class": False,
        "status": "Credentials required",
        "message": "Open the Scriptable widget and enter your Orangetheory email and password when prompted.",
        "privacy_note": PRIVACY_NOTE,
        "last_checked": pendulum.now().format("h:mm A"),
    }), 401

@app.route("/")
def home():
    return jsonify({
        "name": "Next OTF Widget API",
        "status": "running",
        "privacy_note": PRIVACY_NOTE,
        "endpoints": [
            "/api/next-class",
            "/api/refresh"
        ]
    })

@app.route("/api/next-class")
def api_next_class():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        return jsonify(get_cached_next_class_data(email, password, force_refresh=False))
    except Exception as e:
        return jsonify({
            "has_class": False,
            "status": "Error",
            "error": str(e),
            "privacy_note": PRIVACY_NOTE,
            "last_checked": pendulum.now().format("h:mm A"),
        }), 500

@app.route("/api/refresh")
def api_refresh():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        return jsonify(get_cached_next_class_data(email, password, force_refresh=True))
    except Exception as e:
        return jsonify({
            "has_class": False,
            "status": "Error",
            "error": str(e),
            "privacy_note": PRIVACY_NOTE,
            "last_checked": pendulum.now().format("h:mm A"),
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port)
