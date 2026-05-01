import os
import random
import hashlib
import pendulum
from flask import Flask, jsonify, request
from otf_api import Otf, OtfUser
from otf_api.models.bookings import BookingStatus

app = Flask(__name__)

CACHE = {}
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))

PRIVACY_NOTE = (
    "Your Orangetheory email and password are used only to fetch your widget data. "
    "This backend does not save your password or write it to a database."
)

PREFERRED_NO_CLASS_COACHES = [
    "toni", "vassar", "ki", "jon", "carmine", "sydney", "ashlee", "natasha", "lily"
]

def clean_value(value):
    return (value or "").strip()

def get_request_credentials():
    email = clean_value(request.headers.get("X-OTF-Email"))
    password = clean_value(request.headers.get("X-OTF-Password"))
    if not email or not password:
        return None, None
    return email, password

def cache_key_for_email(email):
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()

def create_otf_client(email, password):
    email = clean_value(email)
    password = clean_value(password)

    if not email:
        raise ValueError("Missing Orangetheory email.")

    if not password:
        raise ValueError("Missing Orangetheory password.")

    return Otf(user=OtfUser(email, password))

def safe_error_response(error, status_code=500):
    return jsonify({
        "has_class": False,
        "status": "Error",
        "error": str(error),
        "privacy_note": PRIVACY_NOTE,
        "last_checked": pendulum.now().format("h:mm A"),
    }), status_code

def credentials_required_response():
    return jsonify({
        "has_class": False,
        "status": "Credentials required",
        "message": "Open the Scriptable widget and enter your Orangetheory email and password when prompted.",
        "privacy_note": PRIVACY_NOTE,
        "last_checked": pendulum.now().format("h:mm A"),
    }), 401

def coach_name(coach):
    if not coach:
        return "Not listed"

    first = getattr(coach, "first_name", "")
    last = getattr(coach, "last_name", "")
    full_name = f"{first} {last}".strip()

    return full_name if full_name else str(coach)

def coach_first_name_from_model(coach):
    if not coach:
        return None

    first = getattr(coach, "first_name", None)
    if first:
        return first.strip().lower()

    full = coach_name(coach)
    if full and full != "Not listed":
        return full.split()[0].strip().lower()

    return None

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

def collect_coach_images_from_studios(otf, studio_uuids):
    """
    Dynamic coach image collection from any studio UUID list.
    This is not Greenville-specific. It works for whatever studios the user has access to.
    """
    coach_images = {}

    try:
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

def collect_coach_images_from_favorite_studios(otf):
    studio_uuids = get_favorite_and_home_studio_uuids(otf)
    return collect_coach_images_from_studios(otf, studio_uuids)

def parse_raw_class_start(raw_class, studio_tz):
    raw_start = raw_class.get("starts_at") or raw_class.get("startsAt")
    if not raw_start:
        return None
    return pendulum.parse(raw_start).in_timezone(studio_tz)

def same_class_time(raw_class, otf_class):
    try:
        studio_tz = otf_class.studio.time_zone
        raw_start = parse_raw_class_start(raw_class, studio_tz)
        model_start = pendulum.instance(otf_class.starts_at, tz=studio_tz)

        if not raw_start:
            return False

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
    """
    Dynamic class-specific image lookup.

    This uses the actual studio UUID for the user's next booked/waitlisted class.
    So if Adam is booked in Milwaukee, we query the Milwaukee studio schedule and
    find that class's raw coach.image_url.
    """
    try:
        studio_uuid = otf_class.studio.studio_uuid
        raw_classes = otf.bookings.client.get_classes([studio_uuid])

        model_name = (otf_class.name or "").lower()
        model_coach_first = coach_first_name_from_model(getattr(otf_class, "coach", None))

        best_name_time_match = None
        best_time_coach_match = None

        for raw_class in raw_classes:
            raw_name = (raw_class.get("name") or "").lower()
            coach = raw_class.get("coach") or {}
            image_url = coach.get("image_url")
            raw_coach_first = (coach.get("first_name") or "").strip().lower()

            if not image_url:
                continue

            time_matches = same_class_time(raw_class, otf_class)
            name_matches = raw_name == model_name
            coach_matches = model_coach_first and raw_coach_first == model_coach_first

            if time_matches and name_matches and coach_matches:
                return image_url

            if time_matches and name_matches:
                best_name_time_match = image_url

            if time_matches and coach_matches:
                best_time_coach_match = image_url

        return best_name_time_match or best_time_coach_match

    except Exception:
        return None

def filter_future_bookings(bookings):
    now = pendulum.now()
    future_bookings = []

    for booking in bookings:
        try:
            starts_at = pendulum.instance(booking.otf_class.starts_at)
            if starts_at > now:
                future_bookings.append(booking)
        except Exception:
            future_bookings.append(booking)

    return future_bookings

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

    combined = filter_future_bookings(booked + waitlisted)

    # For no-class states and fallback images, collect from the user's own home/favorite studios.
    coach_images = collect_coach_images_from_favorite_studios(otf)

    if not combined:
        preferred_urls = [
            coach_images[name]
            for name in PREFERRED_NO_CLASS_COACHES
            if name in coach_images and coach_images[name]
        ]

        random_coach_image = random.choice(preferred_urls) if preferred_urls else None

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

    # First: try to find the actual image from the exact booked/waitlisted class's studio schedule.
    coach_image_url = find_coach_image_url_for_class(otf, otf_class)

    # Second: fallback to user's favorite/home studio coach image map by first name.
    if not coach_image_url and coach:
        coach_key = coach_first_name_from_model(coach)
        if coach_key:
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

@app.route("/")
def home():
    return jsonify({
        "name": "Next OTF Widget API",
        "status": "running",
        "privacy_note": PRIVACY_NOTE,
        "endpoints": [
            "/api/login-test",
            "/api/next-class",
            "/api/refresh"
        ]
    })

@app.route("/api/login-test")
def api_login_test():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        otf = create_otf_client(email, password)

        return jsonify({
            "ok": True,
            "status": "Login successful",
            "first_name": getattr(otf.member, "first_name", None),
            "home_studio": getattr(otf.home_studio, "name", None),
            "privacy_note": PRIVACY_NOTE,
            "last_checked": pendulum.now().format("h:mm A"),
        })

    except Exception as e:
        return safe_error_response(e, status_code=401)

@app.route("/api/next-class")
def api_next_class():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        return jsonify(get_cached_next_class_data(email, password, force_refresh=False))

    except Exception as e:
        return safe_error_response(e, status_code=500)

@app.route("/api/refresh")
def api_refresh():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        return jsonify(get_cached_next_class_data(email, password, force_refresh=True))

    except Exception as e:
        return safe_error_response(e, status_code=500)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port)
