import os
import json
import random
import hashlib
from collections import Counter
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
    """
    Approximate the prominent lifetime class count shown in the OTF app.

    The older member.class_summary fields undercount total classes.
    The closer source is the raw performance summaries endpoint.

    Current rule:
    - use performance summaries
    - count records where class.ot_base_class_uuid is None
    - exclude Orangetheory 101 Workshop, which appears to be a non-class workshop
    """
    try:
        raw = otf.workouts.client.get_performance_summaries(limit=2000)
        items = raw.get("items", [])

        count = 0

        for item in items:
            cls = item.get("class") or {}
            class_name = (cls.get("name") or cls.get("className") or "").strip().lower()
            base_uuid = cls.get("ot_base_class_uuid")

            if base_uuid is not None:
                continue

            if class_name == "orangetheory 101 workshop":
                continue

            count += 1

        return count

    except Exception:
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

def get_top_coach_image_urls_from_performance_summaries(otf, fallback_coach_images=None, limit=3):
    """
    Find the user's most frequent coaches from their performance summaries.

    Returns a list of image URLs for the top coaches, when available.
    This is user-specific and not tied to any city/studio.
    """
    fallback_coach_images = fallback_coach_images or {}

    try:
        raw = otf.workouts.client.get_performance_summaries(limit=2000)
        items = raw.get("items", [])

        coach_counts = Counter()
        coach_image_by_key = {}
        coach_display_by_key = {}

        for item in items:
            cls = item.get("class") or {}
            coach = cls.get("coach") or {}

            raw_name = (
                coach.get("first_name")
                or coach.get("name")
                or coach.get("display_name")
                or ""
            )

            raw_name = str(raw_name).strip()

            if not raw_name:
                continue

            # Some OTF responses put full names inside first_name.
            # Use the full raw name for counting, but first token for fallback image matching.
            coach_key = raw_name.lower()
            first_token = raw_name.split()[0].lower()

            coach_counts[coach_key] += 1
            coach_display_by_key[coach_key] = raw_name

            image_url = coach.get("image_url")
            if image_url:
                coach_image_by_key[coach_key] = image_url

            # If the exact coach image is missing from the performance summary,
            # try matching against the user's current home/favorite studio coach images.
            if coach_key not in coach_image_by_key and first_token in fallback_coach_images:
                coach_image_by_key[coach_key] = fallback_coach_images[first_token]

        top_coaches = coach_counts.most_common(limit)

        image_urls = []

        for coach_key, count in top_coaches:
            image_url = coach_image_by_key.get(coach_key)
            if image_url:
                image_urls.append(image_url)

        return image_urls

    except Exception:
        return []

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
        top_coach_image_urls = get_top_coach_image_urls_from_performance_summaries(
            otf,
            fallback_coach_images=coach_images,
            limit=3,
        )

        random_coach_image = random.choice(top_coach_image_urls) if top_coach_image_urls else None

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
            "top_coach_image_urls": top_coach_image_urls,
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


ANALYTICS_FILE = os.environ.get("ANALYTICS_FILE", "analytics.json")
STATS_SECRET = os.environ.get("STATS_SECRET", "")

def now_iso():
    return pendulum.now().to_iso8601_string()

def anonymize_email(email):
    if not email:
        return None
    return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()[:16]

def load_analytics():
    try:
        with open(ANALYTICS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "created_at": now_iso(),
            "events_total": 0,
            "install_events": 0,
            "login_success": 0,
            "login_failed": 0,
            "refresh_success": 0,
            "refresh_failed": 0,
            "status_counts": {},
            "studio_counts": {},
            "users": {},
            "recent_events": [],
        }

def save_analytics(data):
    try:
        with open(ANALYTICS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def increment_counter(container, key, amount=1):
    key = str(key or "unknown")
    container[key] = container.get(key, 0) + amount

def record_analytics_event(event_type, email=None, payload=None):
    """
    Privacy-safe analytics.

    Stores:
    - hashed email only, never raw email
    - event counts
    - coarse class/studio/status info
    - recent event summaries

    Does not store:
    - OTF password
    - raw OTF email
    """
    payload = payload or {}
    data = load_analytics()

    data["events_total"] = data.get("events_total", 0) + 1
    data["last_event_at"] = now_iso()

    if event_type == "install":
        data["install_events"] = data.get("install_events", 0) + 1
    elif event_type == "login_success":
        data["login_success"] = data.get("login_success", 0) + 1
    elif event_type == "login_failed":
        data["login_failed"] = data.get("login_failed", 0) + 1
    elif event_type == "refresh_success":
        data["refresh_success"] = data.get("refresh_success", 0) + 1
    elif event_type == "refresh_failed":
        data["refresh_failed"] = data.get("refresh_failed", 0) + 1

    status = payload.get("status")
    studio = payload.get("studio")
    has_class = payload.get("has_class")

    if status:
        increment_counter(data.setdefault("status_counts", {}), status)

    if studio:
        increment_counter(data.setdefault("studio_counts", {}), studio)

    user_hash = anonymize_email(email)

    if user_hash:
        users = data.setdefault("users", {})
        user = users.setdefault(user_hash, {
            "first_seen": now_iso(),
            "events": 0,
            "login_success": 0,
            "login_failed": 0,
            "refresh_success": 0,
            "refresh_failed": 0,
        })

        user["last_seen"] = now_iso()
        user["events"] = user.get("events", 0) + 1

        if event_type in user:
            user[event_type] = user.get(event_type, 0) + 1

        if status:
            user["last_status"] = status
        if studio:
            user["last_studio"] = studio
        if has_class is not None:
            user["last_has_class"] = has_class

    recent_event = {
        "time": now_iso(),
        "event": event_type,
        "user_hash": user_hash,
        "status": status,
        "studio": studio,
        "has_class": has_class,
    }

    recent_events = data.setdefault("recent_events", [])
    recent_events.append(recent_event)
    data["recent_events"] = recent_events[-50:]

    save_analytics(data)

def analytics_summary():
    data = load_analytics()
    users = data.get("users", {})

    return {
        "created_at": data.get("created_at"),
        "last_event_at": data.get("last_event_at"),
        "events_total": data.get("events_total", 0),
        "install_events": data.get("install_events", 0),
        "login_success": data.get("login_success", 0),
        "login_failed": data.get("login_failed", 0),
        "refresh_success": data.get("refresh_success", 0),
        "refresh_failed": data.get("refresh_failed", 0),
        "unique_users": len(users),
        "status_counts": data.get("status_counts", {}),
        "studio_counts": data.get("studio_counts", {}),
        "recent_events": data.get("recent_events", [])[-20:],
    }

def stats_authorized():
    if not STATS_SECRET:
        return True
    supplied = request.args.get("key") or request.headers.get("X-Stats-Key")
    return supplied == STATS_SECRET


@app.route("/")
def home():
    return jsonify({
        "name": "Next OTF Widget API",
        "status": "running",
        "privacy_note": PRIVACY_NOTE,
        "endpoints": [
            "/api/login-test",
            "/api/next-class",
            "/api/refresh",
            "/api/install-event",
            "/api/public-stats",
            "/api/stats"
        ]
    })

@app.route("/api/login-test")
def api_login_test():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        otf = create_otf_client(email, password)

        record_analytics_event("login_success", email=email, payload={
            "status": "Login successful",
            "studio": getattr(otf.home_studio, "name", None),
            "has_class": None,
        })

        return jsonify({
            "ok": True,
            "status": "Login successful",
            "first_name": getattr(otf.member, "first_name", None),
            "home_studio": getattr(otf.home_studio, "name", None),
            "privacy_note": PRIVACY_NOTE,
            "last_checked": pendulum.now().format("h:mm A"),
        })

    except Exception as e:
        try:
            email, _ = get_request_credentials()
            record_analytics_event("login_failed", email=email, payload={
                "status": "Login failed",
                "has_class": None,
            })
        except Exception:
            pass
        return safe_error_response(e, status_code=401)

@app.route("/api/next-class")
def api_next_class():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        data = get_cached_next_class_data(email, password, force_refresh=False)

        record_analytics_event("refresh_success", email=email, payload={
            "status": data.get("status"),
            "studio": data.get("studio"),
            "has_class": data.get("has_class"),
        })

        return jsonify(data)

    except Exception as e:
        try:
            email, _ = get_request_credentials()
            record_analytics_event("refresh_failed", email=email, payload={
                "status": "Error",
                "has_class": False,
            })
        except Exception:
            pass
        return safe_error_response(e, status_code=500)

@app.route("/api/refresh")
def api_refresh():
    try:
        email, password = get_request_credentials()

        if not email or not password:
            return credentials_required_response()

        data = get_cached_next_class_data(email, password, force_refresh=True)

        record_analytics_event("refresh_success", email=email, payload={
            "status": data.get("status"),
            "studio": data.get("studio"),
            "has_class": data.get("has_class"),
        })

        return jsonify(data)

    except Exception as e:
        try:
            email, _ = get_request_credentials()
            record_analytics_event("refresh_failed", email=email, payload={
                "status": "Error",
                "has_class": False,
            })
        except Exception:
            pass
        return safe_error_response(e, status_code=500)


@app.route("/api/install-event", methods=["POST", "GET"])
def api_install_event():
    record_analytics_event("install", payload={
        "status": "Installer copied/ran",
        "has_class": None,
    })

    return jsonify({
        "ok": True,
        "status": "Install event recorded",
        "last_checked": pendulum.now().format("h:mm A"),
    })


@app.route("/api/public-stats")
def api_public_stats():
    """
    Public, privacy-safe stats for the install page.

    This exposes only broad counts. It does not expose emails, user hashes,
    studios, recent events, or anything credential-related.
    """
    summary = analytics_summary()

    resp = jsonify({
        "script_downloads": summary.get("install_events", 0),
        "unique_widget_users": summary.get("unique_users", 0),
        "last_updated": pendulum.now().format("MMM D, YYYY h:mm A"),
    })

    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/stats")
def api_stats():
    if not stats_authorized():
        return jsonify({
            "ok": False,
            "status": "Unauthorized",
            "message": "Missing or invalid stats key."
        }), 401

    return jsonify(analytics_summary())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port)
