"""Flask web interface for the Insights tool."""

from __future__ import annotations
import os
# below (e.g. FLASK_SECRET_KEY, OPENAI_API_KEY). override=False so a
# Load variables from .env into os.environ before any os.environ.get(...) calls
# below (e.g. FLASK_SECRET_KEY, OPENAI_API_KEY). override=False so a
# value already exported in the shell still wins.
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

import functools
import io
import csv
import math
import json
import sqlite3
import tempfile
import threading
import uuid
from queue import Queue
from flask import (
    Flask,
    Response,
    request,
    render_template,
    redirect,
    url_for,
    session,
    jsonify,
    send_from_directory,
    abort,
    g,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
import cloudinary
import cloudinary.uploader
import re
import feedparser
import requests
from datetime import datetime, timedelta, timezone
import time
from html import unescape
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, urlunparse
from urllib import request as urllib_request
from flasgger import Swagger
import usage_meter
import agent_hub
import domestic_publisher
import video_media
import database
from database import (
    init_db,
    get_episode,
    get_episode_by_id,
    save_episode,
    queue_episode,
    update_episode_status,
    delete_episode_by_id,
    delete_episodes_bulk,
    reset_episode_for_reprocess,
    add_feed,
    list_feeds,
    get_feed_by_id,
    delete_feed,
    delete_feeds_bulk,
    list_all_episodes,
    get_youtube_episodes_missing_channel,
    set_episode_channel,
    add_article,
    get_article,
    list_articles,
    update_article,
    delete_article,
    delete_social_posts_for_article,
    update_feed_metadata,
    # Standalone posts functions (Command Center)
    add_standalone_post,
    # Connected accounts the composer can aim a card at
    list_social_accounts,
    get_social_account,
    resolve_account_id,
    get_existing_standalone_content,
    list_standalone_posts,
    list_standalone_posts_by_source_url,
    count_standalone_posts_by_source_urls,
    get_standalone_post,
    update_standalone_post,
    update_standalone_post_image,
    update_standalone_post_video,
    delete_standalone_post,
    delete_standalone_posts_bulk,
    mark_standalone_post_used,
    # URL sources functions
    add_url_source,
    list_url_sources,
    get_url_source,
    get_url_source_by_url,
    delete_url_source,
    update_url_source_last_used,
    update_url_source_content,
    # Standalone post scheduling
    get_pending_schedule_ids_for_standalone_posts,
    get_pending_schedules_for_standalone_posts,
    get_posted_info_for_standalone_posts,
    # Uploaded images library
    add_uploaded_image,
    list_uploaded_images,
    delete_uploaded_image,
    # Recent prompts
    list_recent_prompts,
    list_recent_image_prompts,
    clear_recent_prompts,
    delete_prompt_by_content,
    delete_prompts_bulk,
    # Prompt library (curated, named reusable prompts)
    add_library_prompt,
    list_library_prompts,
    get_library_prompt,
    update_library_prompt,
    delete_library_prompt,
    # Generated thumbnails
    add_generated_thumbnail,
    list_generated_thumbnails,
    get_generated_thumbnail,
    delete_generated_thumbnail,
    # Users (authentication)
    create_user,
    get_user_by_username,
    get_user_by_id,
    count_users,
    list_users,
    update_last_login,
    # Activity log
    log_activity,
    list_activity,
    count_activity,
    iter_activity_for_export,
    distinct_activity_actions,
    # AI usage metering
    usage_totals,
    usage_by_mode,
    usage_by_category,
    usage_by_model,
    usage_daily,
    list_usage,
    count_usage,
    iter_usage_for_export,
    distinct_usage_categories,
    # Content agent (briefs + runs)
    create_content_brief,
    update_content_brief,
    get_content_brief,
    list_content_briefs,
    delete_content_brief,
    set_content_brief_enabled,
    set_content_brief_schedule,
    get_due_content_briefs,
    create_brief_run,
    finalize_brief_run,
    get_brief_run,
    list_brief_runs,
    get_active_brief_run,
)
from insights import (
    transcribe_audio,
    summarize_text,
    extract_action_items,
    write_results_json,
    configure_logging,
    generate_article,
    refine_article,
    # Command Center functions
    generate_posts_from_prompt,
    generate_posts_from_url,
    generate_posts_from_text,
    generate_posts_from_images,
    condense_document_text,
    check_ollama_status,
    provider_model_options,
    # YouTube functions
    is_youtube_url,
    classify_youtube_url,
    youtube_url_to_rss,
    download_youtube_audio,
    get_youtube_video_id,
    DOWNLOADS_DIR,
    # Thumbnail generation
    fetch_youtube_metadata,
    generate_youtube_thumbnail,
    suggested_thumbnail_prompt,
)
# Content agent orchestrator. Safe to import at top: content_agent (and the
# research_engine/web_search it pulls in) only late-import insights_web inside
# functions, so there is no import cycle at module load.
import appconfig
import content_agent
import document_extractor
from starter_prompts import grouped_starter_prompts
from stock_images import (
    search_stock_images,
    get_image_for_post,
    get_images_for_post,
    extract_keywords_from_text,
    is_configured as stock_images_configured,
    get_configured_services as get_stock_image_services,
)
app = Flask(__name__)

# Session secret key. A stable value is required so login sessions survive
# restarts; if FLASK_SECRET_KEY is missing we fall back to a per-process
# ephemeral key and log a loud warning (login cookies will be invalidated on
# every restart, which defeats the purpose).
_secret_key_env = os.environ.get("FLASK_SECRET_KEY")
if not _secret_key_env:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "FLASK_SECRET_KEY is not set. Generating an ephemeral key — all "
        "sessions will be invalidated on restart. Set FLASK_SECRET_KEY in .env "
        "to a stable value (e.g. python -c \"import secrets; print(secrets.token_hex(32))\")."
    )
app.secret_key = _secret_key_env or os.urandom(24).hex()

# Harden the session cookie. SESSION_COOKIE_SECURE should be set to "true"
# only when serving over HTTPS, otherwise the browser will refuse the cookie
# and login will silently fail on http://localhost.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Configure Swagger/OpenAPI documentation
swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/apidocs/"
}

swagger_template = {
    "info": {
        "title": "Insights API",
        "description": "API for managing podcasts, articles, social media posts, and scheduling",
        "version": "1.0.0"
    },
    "basePath": "/",
    "schemes": ["http", "https"],
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# Configure image uploads
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# Video uploads: a post's video on any platform.
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov'}
MAX_IMAGE_BYTES = 16 * 1024 * 1024   # 16MB — enforced manually on the image path
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Global cap is raised for video; the image route re-checks MAX_IMAGE_BYTES so the
# effective image limit is unchanged.
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB (Cloudinary free-tier per-file)

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Cloudinary (optional - for public image URLs). Used
# to be configured once at import from the environment; now the first upload
# applies whatever /settings has, so an admin can add credentials without a
# restart.
def cloudinary_ready() -> bool:
    """Apply the saved Cloudinary credentials if present; True when usable."""
    cloud_name = appconfig.get("CLOUDINARY_CLOUD_NAME")
    if not cloud_name:
        return False
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=appconfig.get("CLOUDINARY_API_KEY"),
        api_secret=appconfig.get("CLOUDINARY_API_SECRET"),
        secure=True
    )
    return True

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

# ── Authentication & activity tracking ────────────────────────────────────
#
# Multi-user login: every request must carry session['user_id'] except for a
# small allow-list of endpoints (login/signup/logout pages, static assets, and
# the health check). Mutating (non-GET) requests are recorded in
# activity_log via after_request; named auth events use log_event().

PUBLIC_ENDPOINTS = {"login", "signup", "logout", "health",
                    "agent_heartbeat", "agent_claim", "agent_job_events",
                    "agent_pair"}

# Auto-tracking writes a row for every non-GET request, but we suppress
# rows for these endpoints because they're either covered by an explicit
# log_event() call or aren't interesting in the audit log.
SKIP_TRACK_ENDPOINTS = {"login", "logout", "signup", "health",
                        "agent_heartbeat", "agent_claim", "agent_job_events"}

def _is_static_endpoint(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    return endpoint == "static" or endpoint.endswith(".static")

def _client_ip() -> str | None:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr

def _short_ua() -> str:
    return (request.headers.get("User-Agent") or "")[:500]

def _signups_allowed() -> bool:
    """First user is always allowed (bootstrap). After that, gated by setting."""
    try:
        if count_users() == 0:
            return True
    except sqlite3.OperationalError:
        return True
    return (appconfig.get("ALLOW_SIGNUP") or "").lower() == "true"

def current_user():
    """Return the logged-in user row for this request, cached on flask.g."""
    if "user_id" not in session:
        return None
    cached = getattr(g, "_current_user", None)
    if cached is not None:
        return cached
    user = get_user_by_id(session["user_id"])
    if user is None:
        # Stale session pointing at a deleted user
        session.pop("user_id", None)
        return None
    g._current_user = user
    return user

@app.context_processor
def _inject_current_user():
    return {"current_user": current_user()}

# ── Interface language (English / 简体中文) ────────────────────────────────
#
# Templates hold the UI strings in English; the interface itself is Chinese
# only. Templates receive `lang` (used for <html lang> and data-lang), and the
# actual string replacement happens client-side in static/i18n.js against
# static/i18n/zh.json — a newly added template string is simply untranslated
# until its entry is added to the dictionary. There is no language choice to
# make: every request is zh, whatever the cookie or Accept-Language say.

SUPPORTED_LANGS = ("zh",)

def _worker_lang() -> str:
    """Language for generation outside a request (queue workers, agent runs)."""
    return (os.getenv("CONTENT_LANG") or "zh").strip().lower()

@app.before_request
def _pick_request_lang():
    g.lang = "zh"

@app.context_processor
def _inject_lang():
    return {"lang": "zh"}

@app.template_filter("md_preview")
def _md_preview(text, length=200):
    """A plain-text teaser of a markdown body, for list cards.

    The cards used to slice the raw markdown, so a preview read
    "# Title Intro text ## Section 1. First point ...". This drops the syntax
    and cuts on a word boundary instead of mid-word.
    """
    if not text:
        return ""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)                 # fenced code
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                     # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                  # links -> their label
    t = re.sub(r"^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+", "", t, flags=re.M)  # heading/quote/list markers
    t = re.sub(r"<[^>]+>", " ", t)                                  # inline html
    t = t.replace("`", "")
    t = re.sub(r"(?<!\w)[*_~]+|[*_~]+(?!\w)", "", t)                # emphasis; keeps snake_case
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= length:
        return t
    return t[:length].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"

def _target_from_view_args(view_args: dict | None) -> str | None:
    """Best-effort: turn {'episode_id': 42} into 'episode:42' for the log."""
    if not view_args:
        return None
    for key, value in view_args.items():
        if not isinstance(key, str):
            continue
        if key.endswith("_id") and isinstance(value, (int, str)):
            entity = key[: -len("_id")] or "id"
            return f"{entity}:{value}"
    # Fall back to the first scalar value
    for key, value in view_args.items():
        if isinstance(value, (int, str)):
            return f"{key}:{value}"
    return None

def log_event(
    action: str,
    *,
    status_code: int = 200,
    details: dict | None = None,
    username: str | None = None,
) -> None:
    """Record a named event (login_success, login_failed, logout, signup, ...)."""
    user = current_user() if "user_id" in session else None
    try:
        log_activity(
            action=action,
            user_id=user["id"] if user else None,
            username=username or (user["username"] if user else None),
            method=request.method,
            path=request.path,
            endpoint=request.endpoint,
            status_code=status_code,
            ip=_client_ip(),
            user_agent=_short_ua(),
            details=json.dumps(details) if details else None,
        )
    except Exception:
        app.logger.exception("activity log_event failed for action=%s", action)

@app.before_request
def _auth_and_timer():
    # Start a per-request timer used by the activity logger.
    g._track_t0 = time.monotonic()

    endpoint = request.endpoint
    if _is_static_endpoint(endpoint):
        return None
    if endpoint in PUBLIC_ENDPOINTS or endpoint is None:
        return None

    # A GET whose destination is an image must not be answered with a redirect
    # to the login page: an <img> follows it, receives HTML with status 200,
    # and renders as a broken image with nothing in the network log to explain
    # why. A bare 401 is what the browser needs to fire the element's onerror.
    wants_image = request.headers.get("Sec-Fetch-Dest") == "image"

    if "user_id" not in session:
        if request.method == "GET" and not wants_image:
            # Preserve the original target so we can bounce them back post-login.
            return redirect(url_for("login", next=request.full_path))
        # For JSON / form POSTs, return 401 instead of a redirect so the
        # caller (browser fetch, API client) gets a clear error.
        return jsonify({"error": "authentication required"}), 401

    # Resolve the user once and cache it; this also clears a stale session
    # pointing at a deleted account.
    if current_user() is None:
        if request.method == "GET" and not wants_image:
            return redirect(url_for("login", next=request.full_path))
        return jsonify({"error": "authentication required"}), 401

    return None

@app.after_request
def _track_request(response):
    endpoint = request.endpoint
    # Only record mutations to keep the log signal-rich. Auth endpoints use
    # log_event() explicitly so we skip them here to avoid duplicate rows.
    if (
        request.method == "GET"
        or endpoint is None
        or endpoint in SKIP_TRACK_ENDPOINTS
        or _is_static_endpoint(endpoint)
    ):
        return response

    try:
        user = current_user()
        duration_ms = None
        t0 = getattr(g, "_track_t0", None)
        if t0 is not None:
            duration_ms = int((time.monotonic() - t0) * 1000)
        log_activity(
            action=endpoint,
            user_id=user["id"] if user else None,
            username=user["username"] if user else None,
            method=request.method,
            path=request.path,
            endpoint=endpoint,
            target=_target_from_view_args(request.view_args),
            status_code=response.status_code,
            ip=_client_ip(),
            user_agent=_short_ua(),
            duration_ms=duration_ms,
        )
    except Exception:
        app.logger.exception("activity after_request log failed")
    return response

def _safe_next_url(next_value: str | None) -> str | None:
    """Only accept relative paths for the post-login redirect (no open redirects)."""
    if not next_value:
        return None
    parsed = urlparse(next_value)
    if parsed.scheme or parsed.netloc:
        return None
    if not next_value.startswith("/"):
        return None
    return next_value

@app.route("/login", methods=["GET", "POST"])
def login():
    error: str | None = None
    next_url = request.values.get("next")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = get_user_by_username(username) if username else None
        if user is None or not check_password_hash(user["password_hash"], password):
            log_event(
                "login_failed",
                status_code=401,
                username=username or None,
                details={"reason": "invalid_credentials"},
            )
            error = "Invalid username or password."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            try:
                update_last_login(user["id"])
            except Exception:
                app.logger.exception("update_last_login failed")
            log_event("login_success", username=user["username"])
            target = _safe_next_url(next_url) or url_for("index")
            return redirect(target)

    return render_template(
        "login.html",
        error=error,
        next_url=next_url or "",
        signups_allowed=_signups_allowed(),
    )

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not _signups_allowed():
        abort(404)

    error: str | None = None
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        if len(username) < 3 or len(username) > 32 or not re.match(r"^[A-Za-z0-9_.-]+$", username):
            error = "Username must be 3–32 characters (letters, numbers, . _ -)."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif get_user_by_username(username) is not None:
            error = "That username is already taken."
        else:
            try:
                user_id = create_user(
                    username,
                    generate_password_hash(password, method="pbkdf2:sha256"),
                )
            except sqlite3.IntegrityError:
                error = "That username is already taken."
            else:
                session.clear()
                session["user_id"] = user_id
                session.permanent = True
                log_event("signup", username=username)
                return redirect(url_for("index"))

    return render_template("signup.html", error=error, username=username)

@app.route("/logout", methods=["POST"])
def logout():
    if "user_id" in session:
        log_event("logout")
    session.clear()
    return redirect(url_for("login"))

# ── Instance settings (admin) ─────────────────────────────────────────────
# The .env file used to be the only place configuration lived, which works
# for one person and not for a team: nobody pastes an API key into a server
# file for each colleague. The admin manages the keys on /settings instead;
# appconfig resolves each one from the saved value first, then the
# environment, so a .env keeps working until the same key is saved here.

def require_admin(view):
    """Limit a view to signed-in admins; everyone else gets a 403."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None or not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped

@app.route("/settings", methods=["GET", "POST"])
@require_admin
def instance_settings():
    if request.method == "POST":
        saved, cleared, rejected = [], [], []
        for key in appconfig.SETTING_KEYS:
            meta = appconfig.SETTINGS[key]
            if request.form.get(f"clear_{key}"):
                database.delete_setting(key)
                cleared.append(key)
                continue
            raw = request.form.get(key)
            if raw is None:
                continue
            value = raw.strip()
            if meta.get("secret") and not value:
                # A blank secret field means "keep what is saved" — the
                # browser never sees the current value to resend it.
                continue
            choices = meta.get("choices")
            if choices and value and value not in choices:
                rejected.append(key)
                continue
            if value:
                database.set_setting(key, value, updated_by=current_user()["id"])
                saved.append(key)
        if saved or cleared:
            log_event("settings_updated",
                      details={"saved": saved, "cleared": cleared})
        return redirect(url_for("instance_settings",
                                saved=len(saved), cleared=len(cleared),
                                rejected=len(rejected)))

    # Group the registry for the page; secrets render as an empty field with
    # a "set" marker, never their value. The Western platforms' OAuth group is
    # hidden: those channels are no longer offered, so there is nothing left
    # to wire up (the registry keeps the keys so old saved values resolve).
    hidden_groups = {"platforms"}
    groups = []
    for group_id, heading in appconfig.SETTING_GROUPS:
        if group_id in hidden_groups:
            continue
        rows = []
        for key, meta in appconfig.SETTINGS.items():
            if meta["group"] != group_id:
                continue
            where = appconfig.source(key)
            row = {
                "key": key,
                "label": meta["label"],
                "help": meta.get("help"),
                "secret": meta.get("secret", False),
                "choices": meta.get("choices"),
                "value": None if meta.get("secret") else appconfig.get(key),
                "is_set": bool(appconfig.get(key)),
                "source": where,
            }
            rows.append(row)
        if rows:
            groups.append({"id": group_id, "heading": heading, "rows": rows})
    return render_template("settings.html", groups=groups,
                           saved=request.args.get("saved", type=int),
                           cleared=request.args.get("cleared", type=int),
                           rejected=request.args.get("rejected", type=int))

# ── Activity log viewer ───────────────────────────────────────────────────

def _parse_activity_filters():
    """Pull and normalize filter params from the querystring."""
    user = current_user()
    raw_user = request.args.get("user_id") or ""
    requested = int(raw_user) if raw_user.isdigit() else None
    if user and not user["is_admin"]:
        user_id = user["id"]        # 非 admin 固定本人视角；请求参数一律忽略
    else:
        user_id = requested         # admin：按参数筛选，不带参数看全部

    action = (request.args.get("action") or "").strip() or None
    start = (request.args.get("start") or "").strip() or None
    end = (request.args.get("end") or "").strip() or None

    # Accept YYYY-MM-DD inputs (datetime-local without time also works);
    # broaden bare dates to full-day ranges.
    if start and len(start) == 10:
        start = f"{start} 00:00:00"
    if end and len(end) == 10:
        end = f"{end} 23:59:59"

    return {
        "user_id": user_id,
        "action": action,
        "start_ts": start,
        "end_ts": end,
    }

@app.route("/activity")
def activity_page():
    filters = _parse_activity_filters()

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page

    rows = list_activity(**filters, limit=per_page, offset=offset)
    total = count_activity(**filters)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "activity.html",
        rows=rows,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        users=list_users(),
        actions=distinct_activity_actions(),
        filter_user_id=filters["user_id"] or "",
        filter_action=request.args.get("action", ""),
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
    )

@app.route("/activity.csv")
def activity_csv():
    filters = _parse_activity_filters()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "ts",
                "user_id",
                "username",
                "action",
                "method",
                "path",
                "endpoint",
                "target",
                "status_code",
                "ip",
                "user_agent",
                "duration_ms",
                "details",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for row in iter_activity_for_export(**filters):
            writer.writerow(
                [
                    row["id"],
                    row["ts"],
                    row["user_id"],
                    row["username"],
                    row["action"],
                    row["method"],
                    row["path"],
                    row["endpoint"],
                    row["target"],
                    row["status_code"],
                    row["ip"],
                    row["user_agent"],
                    row["duration_ms"],
                    row["details"],
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    headers = {
        "Content-Disposition": f'attachment; filename="activity-{stamp}.csv"'
    }
    return Response(generate(), mimetype="text/csv", headers=headers)

# ── AI usage / cost meters ─────────────────────────────────────────────────

def _parse_usage_filters():
    """Pull and normalize usage-page filter params from the querystring."""
    mode = (request.args.get("mode") or "").strip() or None
    if mode not in ("proactive", "reactive"):
        mode = None

    category = (request.args.get("category") or "").strip() or None
    start = (request.args.get("start") or "").strip() or None
    end = (request.args.get("end") or "").strip() or None

    # Accept bare YYYY-MM-DD inputs; broaden them to full-day ranges.
    if start and len(start) == 10:
        start = f"{start} 00:00:00"
    if end and len(end) == 10:
        end = f"{end} 23:59:59"

    return {"mode": mode, "category": category, "start_ts": start, "end_ts": end}

def _usage_scope_user_id():
    """Resolve the usage page's per-user scope.

    Everyone sees their own events by default; ``?scope=all`` widens the view to
    the whole instance, but only for admins — anyone else silently falls back to
    their own data. Returns the ``user_id`` to filter on, or None for "all".
    """
    user = current_user()
    if (request.args.get("scope") or "").strip() == "all" and user and user["is_admin"]:
        return None
    return user["id"] if user else None

@app.route("/usage")
def usage_page():
    filters = _parse_usage_filters()
    scope_user_id = _usage_scope_user_id()
    # Headline tiles and breakdowns reflect the date range only, so the
    # proactive-vs-reactive split stays meaningful; mode/category filter the log.
    range_filters = {"start_ts": filters["start_ts"], "end_ts": filters["end_ts"],
                     "user_id": scope_user_id}

    totals = usage_totals(**range_filters)
    by_mode = usage_by_mode(**range_filters)
    by_category = usage_by_category(**range_filters)
    by_model = usage_by_model(**range_filters)
    daily = usage_daily(**range_filters)

    empty = {"cost_cny": 0.0, "total_tokens": 0, "events": 0}
    proactive = by_mode.get("proactive", empty)
    reactive = by_mode.get("reactive", empty)

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 100
    offset = (page - 1) * per_page

    rows = list_usage(**filters, user_id=scope_user_id, limit=per_page, offset=offset)
    total_events = count_usage(**filters, user_id=scope_user_id)
    total_pages = max(1, (total_events + per_page - 1) // per_page)

    return render_template(
        "usage.html",
        totals=totals,
        proactive=proactive,
        reactive=reactive,
        by_category=by_category,
        by_model=by_model,
        daily=daily,
        rows=rows,
        total_events=total_events,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        categories=distinct_usage_categories(user_id=scope_user_id),
        filter_mode=request.args.get("mode", ""),
        filter_category=request.args.get("category", ""),
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
        is_admin=bool(current_user() and current_user()["is_admin"]),
        scope="all" if scope_user_id is None else "mine",
    )

@app.route("/usage.csv")
def usage_csv():
    filters = _parse_usage_filters()
    scope_user_id = _usage_scope_user_id()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id", "ts", "mode", "category", "provider", "model",
                "prompt_tokens", "completion_tokens", "total_tokens",
                "audio_seconds", "images", "cost_cny",
                "user_id", "username", "details",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for row in iter_usage_for_export(**filters, user_id=scope_user_id):
            writer.writerow(
                [
                    row["id"], row["ts"], row["mode"], row["category"],
                    row["provider"], row["model"], row["prompt_tokens"],
                    row["completion_tokens"], row["total_tokens"],
                    row["audio_seconds"], row["images"], row["cost_cny"],
                    row["user_id"], row["username"], row["details"],
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    headers = {
        "Content-Disposition": f'attachment; filename="usage-{stamp}.csv"'
    }
    return Response(generate(), mimetype="text/csv", headers=headers)

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ── SSRF-hardened outbound fetch utilities ─────────────────────────────────
#
# Any code path that fetches a URL whose value is influenced by user input
# (post bodies, recall image lists, og:image candidates, etc.) MUST go through
# `_fetch_safely` so we get protocol allowlisting, public-IP DNS validation,
# per-redirect re-checks, response-size caps, and content-type checks.

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

_ALLOWED_URL_SCHEMES = {"http", "https"}
_ALLOWED_URL_PORTS = {None, 80, 443}
_FETCH_USER_AGENT = "InsightsBot/1.0 (+https://insights.local)"
_MAX_FETCH_REDIRECTS = 5
_URL_REGEX = re.compile(r'https?://[^\s<>"\'\)\]\}]+', re.IGNORECASE)
_TRAILING_PUNCT = '.,;:!?>'

# Background pool for non-blocking og:image fetches triggered from save paths
_link_image_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="link-image")

class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF validation."""

def _ip_is_public(ip_obj) -> bool:
    """Return True only for globally routable, non-private IP addresses."""
    if (
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_private
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    ):
        return False
    # Block IPv4-mapped/IPv4-compatible IPv6 that wrap a private address
    if isinstance(ip_obj, ipaddress.IPv6Address):
        mapped = ip_obj.ipv4_mapped or getattr(ip_obj, "sixtofour", None)
        if mapped is not None and not _ip_is_public(mapped):
            return False
    return True

def _assert_safe_url(url: str):
    """Validate scheme, port, and resolved IPs for a URL. Returns the parsed URL."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is empty")
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise UnsafeURLError(f"URL scheme not allowed: {scheme!r}")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no hostname")
    if "@" in (parsed.netloc or ""):
        raise UnsafeURLError("URL must not contain userinfo")
    port = parsed.port
    if port not in _ALLOWED_URL_PORTS:
        raise UnsafeURLError(f"URL port not allowed: {port}")

    host = parsed.hostname
    # Reject bare IP literals that fall in private space without a DNS lookup
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not _ip_is_public(literal_ip):
        raise UnsafeURLError(f"URL host resolves to non-public address: {host}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {host!r}: {exc}") from exc
    if not infos:
        raise UnsafeURLError(f"No addresses resolved for {host!r}")
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        # IPv6 scoped addresses arrive like "fe80::1%eth0" -- strip zone id
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeURLError(f"Could not parse resolved address {ip_str!r}") from exc
        if not _ip_is_public(ip_obj):
            raise UnsafeURLError(
                f"URL host {host!r} resolves to non-public address {ip_str}"
            )
    return parsed

# Some platforms make this server download a video before uploading it, so the
# video downloader gets the same guard as every other user-influenced fetch.
video_media.set_url_guard(_assert_safe_url)

def _fetch_safely(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 10.0,
    allowed_content_types: tuple = (),
):
    """Fetch a URL with SSRF, redirect, size, and content-type protections.

    Returns (body_bytes, content_type, final_url). Raises UnsafeURLError on
    SSRF/validation failures, or RuntimeError on fetch problems.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    current = url
    session = requests.Session()
    session.headers.update({"User-Agent": _FETCH_USER_AGENT, "Accept": "*/*"})
    try:
        for hop in range(_MAX_FETCH_REDIRECTS + 1):
            _assert_safe_url(current)
            response = session.get(
                current,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            )
            try:
                if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError(
                            f"Redirect status {response.status_code} without Location"
                        )
                    next_url = urljoin(current, location)
                    if hop >= _MAX_FETCH_REDIRECTS:
                        raise RuntimeError("Too many redirects")
                    current = next_url
                    continue

                if not response.ok:
                    raise RuntimeError(
                        f"Upstream returned HTTP {response.status_code} for {current}"
                    )

                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if allowed_content_types and not any(
                    content_type.startswith(prefix) for prefix in allowed_content_types
                ):
                    raise RuntimeError(
                        f"Disallowed content type {content_type!r} for {current}"
                    )

                content_length_header = response.headers.get("Content-Length")
                if content_length_header is not None:
                    try:
                        if int(content_length_header) > max_bytes:
                            raise RuntimeError(
                                f"Response too large ({content_length_header} bytes) for {current}"
                            )
                    except ValueError:
                        pass  # malformed header -- enforce via streaming below

                buffer = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise RuntimeError(
                            f"Response exceeded max_bytes ({max_bytes}) for {current}"
                        )
                return bytes(buffer), content_type, current
            finally:
                response.close()
        raise RuntimeError("Too many redirects")
    finally:
        session.close()

def extract_urls_from_text(text: str | None, *, limit: int = 5) -> list:
    """Pull HTTP(S) URLs out of arbitrary text, deduped, trailing punct stripped."""
    if not text:
        return []
    seen = []
    for match in _URL_REGEX.findall(text):
        cleaned = match.rstrip(_TRAILING_PUNCT)
        # Balance trailing closing parens that the regex grabbed
        while cleaned.endswith(')') and cleaned.count('(') < cleaned.count(')'):
            cleaned = cleaned[:-1]
        if not cleaned:
            continue
        try:
            parsed = urlparse(cleaned)
        except ValueError:
            continue
        if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.hostname:
            continue
        if cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen

def fetch_og_image_for_url(url: str):
    """Fetch a webpage safely and return an absolute, SSRF-validated og:image URL."""
    try:
        body, _ctype, final_url = _fetch_safely(
            url,
            max_bytes=2_000_000,
            timeout=8,
            allowed_content_types=("text/html", "application/xhtml"),
        )
    except (UnsafeURLError, RuntimeError, requests.RequestException) as exc:
        app.logger.info("og:image page fetch failed for %s: %s", url, exc)
        return None

    try:
        # Parse only enough to find meta/link tags
        soup = BeautifulSoup(body, "html.parser")
    except Exception as exc:
        app.logger.info("og:image HTML parse failed for %s: %s", url, exc)
        return None

    candidate = None
    for selector in (
        ("meta", {"property": re.compile(r"^og:image(?::url)?$", re.IGNORECASE)}),
        ("meta", {"name": re.compile(r"^og:image$", re.IGNORECASE)}),
        ("meta", {"name": re.compile(r"^twitter:image(?::src)?$", re.IGNORECASE)}),
        ("meta", {"property": re.compile(r"^twitter:image(?::src)?$", re.IGNORECASE)}),
        ("link", {"rel": re.compile(r"image_src", re.IGNORECASE)}),
    ):
        tag_name, attrs = selector
        tag = soup.find(tag_name, attrs=attrs)
        if not tag:
            continue
        value = tag.get("content") or tag.get("href")
        if value:
            candidate = value.strip()
            break

    if not candidate:
        return None

    absolute = urljoin(final_url, candidate)
    try:
        _assert_safe_url(absolute)
    except UnsafeURLError as exc:
        app.logger.info("og:image candidate rejected for %s: %s", url, exc)
        return None
    return absolute

def validate_and_clean_image(file_storage):
    """
    Validate image by parsing with Pillow and re-encode to strip embedded data.
    Returns (cleaned_bytes, extension) or raises ValueError.
    """
    try:
        file_storage.seek(0)
        img = Image.open(file_storage)
        img.verify()  # Verify it's a valid image
        
        # Re-open after verify (verify() can only be called once)
        file_storage.seek(0)
        img = Image.open(file_storage)
        
        # Check format against allowlist
        detected_format = img.format.lower() if img.format else None
        if detected_format not in ['png', 'jpeg', 'gif', 'webp']:
            raise ValueError(f"Image format not allowed: {detected_format}")
        
        # Re-encode to strip any embedded data (anti-polyglot)
        output = io.BytesIO()
        
        # Handle format-specific saving
        save_format = 'JPEG' if detected_format == 'jpeg' else detected_format.upper()
        if save_format == 'JPEG':
            img = img.convert('RGB')  # JPEG doesn't support alpha
        
        img.save(output, format=save_format, optimize=True)
        output.seek(0)
        
        ext = 'jpg' if detected_format == 'jpeg' else detected_format
        return output.read(), ext
        
    except Exception as e:
        raise ValueError(f"Invalid image file: {str(e)}")

def save_stock_image_to_library(image_url: str, user_id: int, direct_save: bool = False) -> str:
    """
    Save a stock image to the library.

    If direct_save=True or URL is from Unsplash/Pexels/Pixabay, saves the URL directly
    to the library without downloading (these services allow hotlinking).

    Otherwise downloads and re-uploads to Cloudinary or local storage.
    Returns the saved image URL.
    """
    # Check if this image URL is already in the library.
    # `list_uploaded_images()` returns `sqlite3.Row` objects which use mapping
    # access (`row['col']`) but do not implement `dict.get()`.
    existing = list_uploaded_images(user_id)
    for img in existing:
        keys = img.keys() if hasattr(img, 'keys') else []
        existing_url = img['url'] if 'url' in keys else None
        existing_filename = img['filename'] if 'filename' in keys else ''
        if existing_url == image_url or (existing_filename and image_url in str(existing_filename)):
            return existing_url or image_url
    
    # Check if this is a stock image URL that allows hotlinking
    stock_domains = ['images.unsplash.com', 'unsplash.com', 'pexels.com', 'pixabay.com']
    is_stock_url = any(domain in image_url for domain in stock_domains)
    
    # For stock URLs, save directly to library without downloading
    if direct_save or is_stock_url:
        # Extract a meaningful filename from the URL
        photo_match = re.search(r'photo-([a-zA-Z0-9_-]+)', image_url)
        if photo_match:
            filename = f"unsplash_{photo_match.group(1)}"
        else:
            filename = f"stock_{uuid.uuid4().hex[:8]}"
        
        # Determine storage type based on URL
        if 'unsplash' in image_url:
            storage = 'unsplash'
        elif 'pexels' in image_url:
            storage = 'pexels'
        elif 'pixabay' in image_url:
            storage = 'pixabay'
        else:
            storage = 'external'
        
        add_uploaded_image(
            filename=filename,
            url=image_url,
            storage=storage,
            size=0,
            user_id=user_id,
        )
        return image_url
    
    # For non-stock URLs, download and re-upload through the SSRF-hardened helper
    image_data, content_type, _final_url = _fetch_safely(
        image_url,
        max_bytes=16 * 1024 * 1024,
        timeout=15,
        allowed_content_types=("image/",),
    )

    ext_map = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/webp': 'webp',
    }
    ext = ext_map.get((content_type or '').split(';')[0].strip(), 'jpg')

    # Validate and clean the image
    img_file = io.BytesIO(image_data)
    try:
        img = Image.open(img_file)
        img.verify()
        img_file.seek(0)
        img = Image.open(img_file)
        
        # Re-encode to strip metadata
        output = io.BytesIO()
        save_format = 'JPEG' if ext == 'jpg' else ext.upper()
        if save_format == 'JPEG':
            img = img.convert('RGB')
        img.save(output, format=save_format, optimize=True)
        output.seek(0)
        cleaned_bytes = output.read()
    except Exception as e:
        raise ValueError(f"Invalid image from stock API: {e}")
    
    # Upload to Cloudinary if configured
    if cloudinary_ready():
        result = cloudinary.uploader.upload(
            cleaned_bytes,
            folder="insights/stock",
            resource_type="image"
        )
        saved_url = result['secure_url']
        filename = f"stock_{result['public_id'].split('/')[-1]}"
        file_size = result.get('bytes', len(cleaned_bytes))
        
        add_uploaded_image(
            filename=filename,
            url=saved_url,
            storage='cloudinary',
            size=file_size,
            user_id=user_id,
        )
        return saved_url
    
    # Local storage fallback
    unique_filename = f"stock_{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
    
    with open(filepath, 'wb') as f:
        f.write(cleaned_bytes)
    
    saved_url = f"/static/uploads/{unique_filename}"
    
    add_uploaded_image(
        filename=unique_filename,
        url=saved_url,
        storage='local',
        size=len(cleaned_bytes),
        user_id=user_id,
    )

    return saved_url

configure_logging()
init_db()

# Background processing queue used to process episodes without blocking the web request
task_queue: Queue = Queue()

def _backfill_youtube_source(url: str, transcript: str, summary: str, user_id: int) -> None:
    """Update the matching URL source with the transcript after transcription."""
    source = get_url_source_by_url(url, user_id)
    if source:
        update_url_source_content(
            source['id'],
            user_id,
            title=source['title'],
            description=summary,
            content=transcript,
            og_image=source['og_image'],
        )

def worker() -> None:
    """Background thread processing queued episodes."""
    while True:
        # Wait for an episode to appear in the queue
        item = task_queue.get()
        if item is None:
            break
        url = item["url"]
        title = item.get("title", "Episode")
        feed_id = item.get("feed_id")
        published = item.get("published")
        try:
            # Attribute this episode's AI usage to the user who enqueued it.
            # The queue is in-memory, so items pending across a restart lose
            # their attribution (events then record no user).
            with usage_meter.user_context(item.get("user_id"), item.get("username")):
                update_episode_status(url, "processing", item['user_id'])
                if is_youtube_url(url):
                    channel = None
                    try:
                        meta = fetch_youtube_metadata(url)
                        channel = meta.get("channel") or None
                    except Exception:
                        pass
                    audio_path = download_youtube_audio(url)
                    try:
                        transcript = transcribe_audio(audio_path)
                        summary = summarize_text(transcript, lang=_worker_lang())
                        actions = extract_action_items(transcript, lang=_worker_lang())
                        save_episode(url, title, transcript, summary, actions, feed_id, published, channel=channel,
                                     user_id=item['user_id'])
                        _backfill_youtube_source(url, transcript, summary, user_id=item['user_id'])
                    finally:
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                else:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        audio_path = os.path.join(tmpdir, "episode.mp3")
                        with requests.get(url, stream=True) as r:
                            r.raise_for_status()
                            with open(audio_path, "wb") as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    f.write(chunk)
                        transcript = transcribe_audio(audio_path)
                        summary = summarize_text(transcript, lang=_worker_lang())
                        actions = extract_action_items(transcript, lang=_worker_lang())
                        out_path = os.path.join(tmpdir, "results.json")
                        write_results_json(transcript, summary, actions, out_path)
                        save_episode(url, title, transcript, summary, actions, feed_id, published,
                                     user_id=item['user_id'])
        except Exception:
            app.logger.exception("Failed to process episode %s", url)
            error_uid = item.get("user_id")
            if error_uid is not None:
                update_episode_status(url, "error", error_uid)
            else:
                # Defensive: an item enqueued without attribution can only be
                # logged — there is no owner to mark the error status for, and
                # no NULL-owned row may be created.
                app.logger.error(
                    "Episode %s failed while queued without a user_id; error status left unattributed",
                    url)
        finally:
            task_queue.task_done()

def _proactive_worker() -> None:
    """Run the episode ``worker`` with every generation tagged as proactive.

    The background queue is automated (non-interactive) work, so all AI usage it
    incurs — transcription, summaries, action items — is metered as ``proactive``.
    ContextVars are per-thread, so this override is scoped to the worker thread.
    """
    with usage_meter.usage_context("proactive"):
        worker()

# Worker thread will be started in main block to avoid duplicates in debug mode
# (app.debug is False at import time, so we can't check it here)

def strip_html(text: str) -> str:
    """Return plain text with HTML tags removed."""
    if not text:
        return ""
    # replace common HTML tags with line breaks then strip everything else
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()

def make_short_description(text: str, limit: int = 200) -> str:
    """Return a short preview from the provided text."""
    if not text:
        return ""
    text = text.strip()
    # Use the first couple of sentences as a human friendly snippet
    sentences = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(sentences[:2])
    if len(short) > limit:
        short = short[:limit].rstrip() + "..."
    return short

def fetch_article_content(url: str, timeout: int = 15) -> str:
    """Fetch and extract the main content from an article URL.
    
    Uses trafilatura for robust article extraction, with BeautifulSoup as fallback.
    Returns extracted text or empty string on failure.
    """
    import trafilatura
    
    # Skip non-HTML URLs (audio, video, images, etc.)
    media_extensions = (
        '.mp3', '.mp4', '.m4a', '.wav', '.ogg', '.webm', '.avi', '.mov',
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.pdf', '.zip'
    )
    url_lower = url.lower().split('?')[0]  # Remove query params for extension check
    if url_lower.endswith(media_extensions):
        app.logger.info("Skipping media URL (not an article): %s", url)
        return ""
    
    try:
        # Try trafilatura first - it's specifically designed for article extraction
        downloaded = trafilatura.fetch_url(url)
        
        if downloaded:
            content = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            if content and len(content) > 200:
                app.logger.info("Extracted %d chars using trafilatura from: %s", len(content), url)
                return content
        
        # Fallback to BeautifulSoup for non-article pages or if trafilatura fails
        app.logger.info("Trafilatura extraction insufficient, falling back to BeautifulSoup for: %s", url)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        
        # Check content type - only process HTML
        content_type = resp.headers.get('content-type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            return ""
        
        soup = BeautifulSoup(resp.content, 'lxml')
        
        # Remove unwanted elements
        for element in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 
                                       'aside', 'iframe', 'noscript', 'form',
                                       'button', 'input', 'select', 'textarea']):
            element.decompose()
        
        # Remove common ad/tracking elements by class or id patterns
        for element in soup.find_all(class_=re.compile(r'(ad|ads|advert|banner|sidebar|comment|share|social|related|recommend|newsletter|popup|modal|cookie)', re.I)):
            element.decompose()
        for element in soup.find_all(id=re.compile(r'(ad|ads|advert|banner|sidebar|comment|share|social|related|recommend|newsletter|popup|modal|cookie)', re.I)):
            element.decompose()
        
        # Try to find the main content area
        article_content = None
        
        # Priority 1: Look for article tag
        article = soup.find('article')
        if article:
            article_content = article
        
        # Priority 2: Look for main content divs
        if not article_content:
            for selector in ['[role="main"]', '.article-content', '.post-content', 
                            '.entry-content', '.content', '#content', '.story-body',
                            '.article-body', '.post-body', 'main']:
                found = soup.select_one(selector)
                if found:
                    article_content = found
                    break
        
        # Priority 3: Use body as fallback
        if not article_content:
            article_content = soup.find('body') or soup
        
        # Extract text from paragraphs for cleaner output
        paragraphs = article_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'code'])
        
        if paragraphs:
            text_parts = []
            for p in paragraphs:
                text = p.get_text(separator=' ', strip=True)
                if text and len(text) > 20:  # Filter out short fragments
                    text_parts.append(text)
            content = '\n\n'.join(text_parts)
        else:
            # Fallback: get all text
            content = article_content.get_text(separator='\n', strip=True)
        
        # Clean up whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = content.strip()
        
        return content
        
    except requests.exceptions.Timeout:
        app.logger.warning("Timeout fetching article: %s", url)
        return ""
    except requests.exceptions.RequestException as e:
        app.logger.warning("Failed to fetch article %s: %s", url, str(e))
        return ""
    except Exception as e:
        app.logger.exception("Error extracting content from %s: %s", url, str(e))
        return ""

def fetch_article_content_safe(url: str, timeout: int = 12, max_bytes: int = 3_000_000) -> tuple[str, str]:
    """SSRF-safe article fetch + extraction for agent/automated flows.

    Unlike ``fetch_article_content`` (which calls ``trafilatura.fetch_url``/``requests``
    directly and bypasses the SSRF guard), this fetches bytes through
    ``_fetch_safely`` (per-redirect public-IP validation, content-type + size caps)
    and extracts text from the in-memory HTML. Never performs a second, unguarded
    network fetch.

    Returns ``(text, final_url)``; ``("", url)`` on any failure.
    """
    try:
        body, ctype, final_url = _fetch_safely(
            url,
            max_bytes=max_bytes,
            timeout=timeout,
            allowed_content_types=("text/html", "application/xhtml"),
        )
    except (UnsafeURLError, RuntimeError, requests.RequestException) as exc:
        app.logger.info("Safe fetch rejected/failed for %s: %s", url, exc)
        return "", url
    except Exception:
        app.logger.exception("Unexpected error during safe fetch of %s", url)
        return "", url

    html = body.decode("utf-8", errors="replace")

    # Prefer trafilatura's precision extraction on the already-downloaded HTML.
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if text and len(text) > 200:
            return text.strip(), final_url
    except Exception:
        app.logger.debug("trafilatura.extract failed for %s", final_url, exc_info=True)

    # Fallback: compact BeautifulSoup reader (mirrors fetch_article_content).
    try:
        soup = BeautifulSoup(html, "lxml")
        for element in soup.find_all(["script", "style", "nav", "header", "footer",
                                       "aside", "iframe", "noscript", "form",
                                       "button", "input", "select", "textarea"]):
            element.decompose()
        container = soup.find("article")
        if not container:
            for selector in ['[role="main"]', ".article-content", ".post-content",
                             ".entry-content", ".content", "#content", ".story-body",
                             ".article-body", ".post-body", "main"]:
                found = soup.select_one(selector)
                if found:
                    container = found
                    break
        if not container:
            container = soup.find("body") or soup
        parts = []
        for p in container.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                      "li", "blockquote", "pre", "code"]):
            t = p.get_text(separator=" ", strip=True)
            if t and len(t) > 20:
                parts.append(t)
        text = "\n\n".join(parts) if parts else container.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
        return text, final_url
    except Exception:
        app.logger.exception("BeautifulSoup extraction failed for %s", final_url)
        return "", final_url

# Templates are stored in the ``templates`` directory

def parse_feed_with_fallback(url: str):
    """Parse a feed URL, falling back to the ``www.`` variant if the
    response is HTML (commonly caused by WAF challenge pages).

    Some hosts serve WAF/security verification HTML on the apex domain
    while serving real RSS XML from the ``www`` subdomain.  This helper
    transparently retries those common fallbacks so users do not have to
    know which hostname variant to enter.
    """
    parsed = feedparser.parse(url)
    if parsed.entries:
        return parsed

    # A real empty RSS feed will have an XML content-type.  HTML responses
    # mean we almost certainly hit a WAF page, portal, or redirect page.
    headers = getattr(parsed, "headers", {}) or {}
    content_type = (headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return parsed

    # Try extracting an alternate RSS/Atom link from the returned HTML.
    try:
        req = urllib_request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
        for pattern in (
            r'<link[^>]*rel=["\']alternate["\'][^>]*type=["\']application/rss\+xml["\'][^>]*href=["\']([^"\']+)["\']',
            r'<link[^>]*type=["\']application/rss\+xml["\'][^>]*href=["\']([^"\']+)["\']',
            r'<link[^>]*href=["\']([^"\']+)["\'][^>]*type=["\']application/rss\+xml["\']',
        ):
            m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if m:
                alt_url = urljoin(url, m.group(1))
                alt_parsed = feedparser.parse(alt_url)
                if alt_parsed.entries:
                    return alt_parsed
                break
    except Exception:
        pass

    # Try the www. / non-www. hostname swap.
    try:
        parts = urlparse(url)
        netloc = parts.netloc
        if netloc.lower().startswith("www."):
            swapped = netloc[4:]
        else:
            swapped = "www." + netloc
        swapped_url = urlunparse(parts._replace(netloc=swapped))
        swapped_parsed = feedparser.parse(swapped_url)
        if swapped_parsed.entries:
            return swapped_parsed
    except Exception:
        pass

    return parsed


def refresh_feed_metadata(feed_id: int, feed_url: str, user_id: int) -> dict:
    """Fetch feed and update cached metadata. Returns the metadata dict."""
    try:
        feed_data = parse_feed_with_fallback(feed_url)
        if not feed_data.entries:
            return {'type': 'unknown', 'last_post': None, 'item_count': 0}
        
        # Determine feed type from URL and entries
        if 'youtube.com/feeds/videos.xml' in feed_url:
            feed_type = 'youtube'
        else:
            is_audio = False
            for entry in feed_data.entries[:5]:
                if entry.get('enclosures'):
                    is_audio = True
                    break
            feed_type = 'audio' if is_audio else 'text'
        
        # Get last post date from most recent entry
        last_post = None
        last_post_str = None
        for entry in feed_data.entries[:1]:
            if getattr(entry, 'published_parsed', None):
                last_post = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                last_post_str = last_post.isoformat()
            elif getattr(entry, 'updated_parsed', None):
                last_post = datetime.fromtimestamp(time.mktime(entry.updated_parsed))
                last_post_str = last_post.isoformat()
        
        item_count = len(feed_data.entries)
        
        # Save to database
        update_feed_metadata(feed_id, feed_type, last_post_str, item_count, user_id)
        
        return {
            'type': feed_type,
            'last_post': last_post,
            'item_count': item_count
        }
    except Exception:
        return {'type': 'unknown', 'last_post': None, 'item_count': 0}

@app.route('/', methods=['GET', 'POST'])
def index():
    """List stored podcast feeds and allow new ones to be added."""
    uid = current_user()['id']
    if request.method == 'POST':
        feed_url = request.form['feed_url']

        if is_youtube_url(feed_url):
            yt_kind = classify_youtube_url(feed_url)

            if yt_kind in ("channel", "playlist"):
                rss_url = youtube_url_to_rss(feed_url)
                if rss_url:
                    feed = feedparser.parse(rss_url)
                    title = feed.feed.get('title', feed_url)
                    feed_id = add_feed(rss_url, title, uid)
                    update_feed_metadata(feed_id, 'youtube', None, len(feed.entries), uid)
                    return redirect(url_for('view_feed', feed_id=feed_id))

            if yt_kind == "video":
                video_id = get_youtube_video_id(feed_url)
                title, description, og_image = feed_url, "", None
                try:
                    import yt_dlp
                    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                        info = ydl.extract_info(feed_url, download=False)
                        title = info.get("title", title)
                        description = info.get("description", "")
                        og_image = info.get("thumbnail")
                except Exception:
                    pass
                if not og_image and video_id:
                    og_image = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                add_url_source(
                    url=feed_url, title=title,
                    description=description, content="", og_image=og_image,
                    user_id=uid,
                )
                return redirect(url_for('sources_page'))

        feed = parse_feed_with_fallback(feed_url)
        title = feed.feed.get('title', feed_url)
        feed_id = add_feed(feed_url, title, uid)
        refresh_feed_metadata(feed_id, feed_url, uid)
        return redirect(url_for('view_feed', feed_id=feed_id))

    # Get filter and sort parameters
    filter_type = request.args.get('type', '')
    sort_by = request.args.get('sort', 'title')
    sort_order = request.args.get('order', 'asc')
    search_query = request.args.get('q', '').lower()

    raw_feeds = list_feeds(uid)
    feeds_with_meta = []
    
    for f in raw_feeds:
        # Use cached metadata from database
        last_post = None
        if f['last_post']:
            try:
                last_post = datetime.fromisoformat(f['last_post'])
            except (ValueError, TypeError):
                pass
        
        feed_dict = {
            'id': f['id'],
            'title': f['title'],
            'url': f['url'],
            'type': f['feed_type'] or 'unknown',
            'last_post': last_post,
            'item_count': f['item_count'] or 0,
            'last_checked': f['last_checked'],
        }
        
        # Apply type filter
        if filter_type and feed_dict['type'] != filter_type:
            continue
        
        # Apply search filter
        if search_query and search_query not in feed_dict['title'].lower():
            continue
        
        feeds_with_meta.append(feed_dict)
    
    # Sort feeds
    if sort_by == 'last_post':
        feeds_with_meta.sort(
            key=lambda x: x['last_post'] or datetime.min,
            reverse=(sort_order == 'desc')
        )
    elif sort_by == 'type':
        feeds_with_meta.sort(
            key=lambda x: x['type'] or '',
            reverse=(sort_order == 'desc')
        )
    elif sort_by == 'items':
        feeds_with_meta.sort(
            key=lambda x: x['item_count'] or 0,
            reverse=(sort_order == 'desc')
        )
    else:  # Default: title
        feeds_with_meta.sort(
            key=lambda x: x['title'].lower(),
            reverse=(sort_order == 'desc')
        )
    
    return render_template(
        'feeds.html',
        feeds=feeds_with_meta,
        filter_type=filter_type,
        sort_by=sort_by,
        sort_order=sort_order,
        search_query=search_query,
        now=datetime.now(),
    )

@app.route('/feed/<int:feed_id>/delete', methods=['POST'])
def remove_feed(feed_id: int):
    """Delete a feed and all its associated data."""
    delete_feed(feed_id, current_user()['id'])
    return redirect(url_for('index'))

@app.route('/feeds/bulk-delete', methods=['POST'])
def bulk_delete_feeds():
    """Delete multiple feeds at once."""
    uid = current_user()['id']
    feed_ids = request.form.getlist('feed_ids', type=int)
    if feed_ids:
        delete_feeds_bulk(feed_ids, uid)
    return redirect(url_for('index'))

@app.route('/feed/<int:feed_id>/refresh')
def refresh_feed(feed_id: int):
    """Refresh metadata for a specific feed."""
    uid = current_user()['id']
    feed = get_feed_by_id(feed_id, uid)
    if not feed:
        abort(404)
    refresh_feed_metadata(feed_id, feed['url'], uid)
    return redirect(url_for('index'))

@app.route('/feeds/refresh-all')
def refresh_all_feeds():
    """Refresh metadata for all feeds (runs in background thread)."""
    uid = current_user()['id']

    def refresh_worker():
        for f in list_feeds(uid):
            try:
                refresh_feed_metadata(f['id'], f['url'], uid)
            except Exception:
                pass

    thread = threading.Thread(target=refresh_worker, daemon=True)
    thread.start()
    return redirect(url_for('index'))

@app.route('/feed/<int:feed_id>')
def view_feed(feed_id: int):
    """Display episodes/articles for a particular feed with pagination."""
    uid = current_user()['id']
    feed = get_feed_by_id(feed_id, uid)
    if not feed:
        abort(404)

    is_youtube_feed = feed['feed_type'] == 'youtube'

    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    per_page = min(per_page, 50)

    all_episodes = []
    is_text_feed = True

    refresh_feed_metadata(feed_id, feed['url'], uid)
    feed_data = parse_feed_with_fallback(feed['url'])

    audio_extensions = ('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac')

    for entry in feed_data.entries:
        has_audio = bool(entry.get('enclosures'))
        if has_audio:
            is_text_feed = False
            url = entry.enclosures[0].href
            item_type = 'audio'
        elif is_youtube_feed:
            url = entry.get('link', entry.get('id', ''))
            item_type = 'youtube'
            is_text_feed = False
            if not url:
                continue
        else:
            url = entry.get('link', entry.get('id', ''))
            item_type = 'text'
            if not url:
                continue
            url_check = url.lower().split('?')[0]
            if url_check.endswith(audio_extensions):
                is_text_feed = False
                item_type = 'audio'
        ep_db = get_episode(url, uid)
        status = {
            'transcribed': ep_db is not None and bool(ep_db['transcript']),
            'summarized': ep_db is not None and bool(ep_db['summary']),
            'actions': ep_db is not None and bool(ep_db['action_items']),
            'state': ep_db['status'] if ep_db else 'new',
        }
        content = ''
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].get('value', '')
        if not content:
            content = entry.get('summary') or entry.get('description', '')
        desc = entry.get('summary') or entry.get('description', '')
        clean_desc = strip_html(desc)
        img = None
        if is_youtube_feed:
            vid = get_youtube_video_id(url)
            if vid:
                img = f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"
        if not img:
            if hasattr(entry, 'image') and getattr(entry.image, 'href', None):
                img = entry.image.href
            elif entry.get('itunes_image'):
                img = entry.itunes_image.get('href') if isinstance(entry.itunes_image, dict) else entry.itunes_image
            elif entry.get('media_thumbnail'):
                img = entry.media_thumbnail[0].get('url')
            elif entry.get('media_content'):
                img = entry.media_content[0].get('url')
        published_ts = None
        if getattr(entry, 'published_parsed', None):
            published_ts = datetime.fromtimestamp(time.mktime(entry.published_parsed))
        elif getattr(entry, 'updated_parsed', None):
            published_ts = datetime.fromtimestamp(time.mktime(entry.updated_parsed))
        published_iso = published_ts.isoformat() if published_ts else None
        author = entry.get('author', '')
        ep_data = {
            'title': entry.title,
            'description': desc,
            'content': content,
            'clean_description': clean_desc,
            'short_description': make_short_description(clean_desc),
            'image': img,
            'enclosure': url,
            'link': entry.get('link', ''),
            'author': author,
            'type': item_type,
            'status': status,
            'published': published_iso,
        }
        if item_type == 'youtube':
            ep_data['video_id'] = get_youtube_video_id(url)
        all_episodes.append(ep_data)

    total_items = len(all_episodes)
    total_pages = (total_items + per_page - 1) // per_page
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    episodes = all_episodes[start_idx:end_idx]

    pagination = {
        'page': page,
        'per_page': per_page,
        'total_items': total_items,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if page < total_pages else None,
    }

    return render_template(
        'feed.html',
        feed=feed,
        episodes=episodes,
        is_text_feed=is_text_feed,
        is_youtube_feed=is_youtube_feed,
        pagination=pagination,
    )

@app.route('/enqueue')
def enqueue_episode():
    """Queue an episode for background processing."""
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    if not audio_url or feed_id is None:
        return redirect(url_for('index'))
    uid = current_user()['id']
    if not get_feed_by_id(feed_id, uid):
        abort(404)
    queue_episode(audio_url, title, feed_id, published, uid)
    _user = current_user()
    task_queue.put({'url': audio_url, 'title': title, 'feed_id': feed_id, 'published': published,
                    'user_id': _user['id'] if _user else None,
                    'username': _user['username'] if _user else None})
    return redirect(url_for('status_page'))

@app.route('/process_text')
def process_text_article():
    """Process a text article (no transcription needed)."""
    article_url = request.args.get('url')
    title = request.args.get('title', 'Article')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    description = ""
    content = ""

    if not article_url:
        return redirect(url_for('index'))

    uid = current_user()['id']

    app.logger.info("Processing text article: %s", article_url)

    # Fetch content from the feed first
    if feed_id:
        feed = get_feed_by_id(feed_id, uid)
        if not feed:
            abort(404)
        feed_data = parse_feed_with_fallback(feed['url'])
        for entry in feed_data.entries:
            entry_url = entry.get('link', entry.get('id', ''))
            if entry_url == article_url:
                desc = entry.get('summary') or entry.get('description', '')
                description = strip_html(desc)
                # Get full content from feed
                if hasattr(entry, 'content') and entry.content:
                    content = entry.content[0].get('value', '')
                if not content:
                    content = desc
                content = strip_html(content)
                break
    
    # If RSS content is too short (likely just metadata/link), fetch the actual article
    MIN_CONTENT_LENGTH = 500  # Minimum chars to consider content "full"
    if len(content) < MIN_CONTENT_LENGTH:
        app.logger.info("RSS content too short (%d chars), fetching from URL: %s", len(content), article_url)
        fetched_content = fetch_article_content(article_url)
        if fetched_content and len(fetched_content) > len(content):
            app.logger.info("Fetched %d chars from article URL", len(fetched_content))
            content = fetched_content
        else:
            app.logger.warning("Could not fetch better content from URL")

    # Reuse previously processed data if available
    existing = get_episode(article_url, uid)
    if existing:
        transcript = existing["transcript"]
        summary = existing["summary"]
        actions = existing["action_items"].splitlines()
        articles = [dict(a) for a in list_articles(existing["id"], uid)]
        return render_template(
            'result.html',
            title=existing["title"],
            transcript=transcript,
            summary=summary,
            actions=actions,
            description=description,
            feed_id=feed_id,
            url=article_url,
            articles=articles,
            current_url=request.full_path,
            is_text=True,
            original_link=article_url,
        )

    if not content:
        app.logger.error("No content found for article: %s", article_url)
        return redirect(url_for('view_feed', feed_id=feed_id))

    # For text articles, content IS the transcript (no transcription needed)
    transcript = content
    app.logger.info("Article content loaded (%d chars)", len(transcript))

    app.logger.info("Generating summary")
    summary = summarize_text(transcript, lang=g.lang)
    app.logger.info("Summary complete")

    app.logger.info("Extracting action items")
    actions = extract_action_items(transcript, lang=g.lang)
    app.logger.info("Action item extraction complete")

    # Persist results
    save_episode(article_url, title, transcript, summary, actions, feed_id, published,
                 user_id=uid)

    return render_template(
        'result.html',
        title=title,
        transcript=transcript,
        summary=summary,
        actions=actions,
        description=description,
        feed_id=feed_id,
        url=article_url,
        articles=[],
        current_url=request.full_path,
        is_text=True,
        original_link=article_url,
    )

@app.route('/process')
def process_episode():
    """Process an episode synchronously and show the results."""
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    description = ""
    if not audio_url:
        return redirect(url_for('index'))
    uid = current_user()['id']
    app.logger.info("Processing episode: %s", audio_url)
    if not description and feed_id:
        feed = get_feed_by_id(feed_id, uid)
        if not feed:
            abort(404)
        feed_data = parse_feed_with_fallback(feed['url'])
        for entry in feed_data.entries:
            entry_url = entry.get('link', entry.get('id', ''))
            enclosure_url = entry.enclosures[0].href if entry.get('enclosures') else None
            if enclosure_url == audio_url or entry_url == audio_url:
                desc = entry.get('summary') or entry.get('description', '')
                description = strip_html(desc)
                break
    # Reuse previously processed data if available
    existing = get_episode(audio_url, uid)
    if existing:
        # Already processed - read results from the DB
        transcript = existing["transcript"]
        summary = existing["summary"]
        actions = existing["action_items"].splitlines()
        articles = [dict(a) for a in list_articles(existing["id"], uid)]
        return render_template(
            'result.html',
            title=existing["title"],
            transcript=transcript,
            summary=summary,
            actions=actions,
            description=description,
            feed_id=feed_id,
            url=audio_url,
            articles=articles,
            current_url=request.full_path,
        )

    channel = None
    if is_youtube_url(audio_url):
        try:
            meta = fetch_youtube_metadata(audio_url)
            channel = meta.get("channel") or None
        except Exception:
            pass
        audio_path = download_youtube_audio(audio_url)
    else:
        tmpdir = tempfile.mkdtemp()
        audio_path = os.path.join(tmpdir, 'episode.mp3')
        with requests.get(audio_url, stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
    try:
        app.logger.info("Transcribing audio")
        transcript = transcribe_audio(audio_path)
        app.logger.info("Transcription complete")

        app.logger.info("Generating summary")
        summary = summarize_text(transcript, lang=g.lang)
        app.logger.info("Summary complete")

        app.logger.info("Extracting action items")
        actions = extract_action_items(transcript, lang=g.lang)
        app.logger.info("Action item extraction complete")
        save_episode(audio_url, title, transcript, summary, actions, feed_id, published, channel=channel,
                     user_id=uid)
        if is_youtube_url(audio_url):
            _backfill_youtube_source(audio_url, transcript, summary, uid)
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    return render_template(
        'result.html',
        title=title,
        transcript=transcript,
        summary=summary,
        actions=actions,
        description=description,
        feed_id=feed_id,
        url=audio_url,
        articles=[],
        current_url=request.full_path,
    )

@app.route('/api/routes')
def list_api_routes():
    """List all available API routes.
    ---
    tags:
      - System
    responses:
      200:
        description: List of all API routes with their methods and endpoints
        schema:
          type: object
          properties:
            routes:
              type: array
              items:
                type: object
                properties:
                  endpoint:
                    type: string
                  methods:
                    type: array
                    items:
                      type: string
                  path:
                    type: string
            count:
              type: integer
    """
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint != 'static':
            routes.append({
                'endpoint': rule.endpoint,
                'methods': list(rule.methods - {'HEAD', 'OPTIONS'}),
                'path': str(rule)
            })
    routes.sort(key=lambda x: x['path'])
    return jsonify({'routes': routes, 'count': len(routes)})

@app.route('/accounts')
def accounts_page():
    """Platform logins: what is connected, what needs a fresh login.

    One card per platform. Each platform's row shows the current login
    (or none), the last time it was seen, and the login state from the
    agent's heartbeat — ok / logged_out / unknown / offline. Domestic
    platforms log in through the browser, so "connecting" is a browser
    login the agent then reports.
    """
    meta = {
        'xiaohongshu': {'label': '小红书', 'color': '#FF2442'},
        'douyin':      {'label': '抖音',   'color': '#FE2C55'},
        'wechat':      {'label': '公众号', 'color': '#07C160'},
        'zhihu':       {'label': '知乎',   'color': '#0084FF'},
        'toutiao':     {'label': '头条号', 'color': '#ED1C24'},
    }
    login = agent_hub.login_states_view()
    states = login.get('platforms', {})
    accounts = list_social_accounts()
    platforms = []
    for pid, m in meta.items():
        rows = [a for a in accounts if a['platform'] == pid]
        st = states.get(pid, {'state': 'offline', 'at': None})
        state = st.get('state', 'offline')
        account = rows[0] if rows else None
        # 每平台一号：登录态就是浏览器里那个账号。旧多账号时代留下的
        # social_accounts 行带身份信息；没有行时，heartbeat 判定已登录
        # 的平台就用平台名作为当前登录展示，直到 agent 上报真实身份。
        if account:
            account_label = (account['label'] or account['display_name']
                             or account['handle'] or account['external_id'])
            last_seen = account['updated_at']
        else:
            account_label = m['label'] if state in ('ok', 'logged_out') else None
            last_seen = None
        platforms.append({
            'id': pid,
            'label': m['label'],
            'color': m['color'],
            'state': state,
            'checked_at': st.get('at'),
            'login_url': st.get('login_url'),
            'account': {
                'label': account_label,
                'last_seen': last_seen,
            } if (account or state in ('ok', 'logged_out')) else None,
        })
    summary = {'logged_in': 0, 'logged_out': 0, 'unknown': 0}
    for pl in platforms:
        if pl['state'] in ('ok',): summary['logged_in'] += 1
        elif pl['state'] == 'logged_out': summary['logged_out'] += 1
        elif pl['state'] in ('unknown', 'offline'): summary['unknown'] += 1
    return render_template('accounts.html', platforms=platforms,
                           summary=summary,
                           agent_online=agent_hub.agent_online())

@app.route('/schedule')
def schedule_page():
    """Monthly calendar of scheduled posts plus slot and limit settings.

    Reads today's month from the request (what you came from the sidebar or
    a date picker) and lists that month's scheduled posts. The calendar is a
    read view of queue data the composer writes: each entry shows on the date
    it is scheduled for, tagged by platform colour, and carries its post
    status. Below the grid sit the configured publish time slots and the
    per-platform daily limits — both already stored in the database by the
    composer.
    """
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    today = datetime.now(timezone.utc).date()
    year = year or today.year
    month = month or today.month
    month = max(1, min(12, month))
    first = "%04d-%02d-01" % (year, month)
    if month == 12:
        nxt_y, nxt_m = year + 1, 1
    else:
        nxt_y, nxt_m = year, month + 1
    last = "%04d-%02d-01" % (nxt_y, nxt_m)

    uid = current_user()['id']
    rows = database.list_scheduled_posts(uid, date_from=first, date_to=last, sort_order='asc')
    PLATFORM_META = {
        'xiaohongshu': {'label': '小红书', 'color': '#FF2442'},
        'douyin':      {'label': '抖音',   'color': '#FE2C55'},
        'wechat':      {'label': '公众号', 'color': '#07C160'},
        'zhihu':       {'label': '知乎',   'color': '#0084FF'},
        'toutiao':     {'label': '头条号', 'color': '#ED1C24'},
    }
    def _title(row):
        for key in ('article_topic', 'social_content', 'standalone_content'):
            val = row[key] if key in row.keys() else None
            if val:
                return (val if len(val) <= 40 else val[:40] + '…')
        return '未命名'
    events = []
    for row in rows:
        status = row['status'] if 'status' in row.keys() else 'pending'
        if status == 'cancelled':
            continue
        when = row['scheduled_for'] if 'scheduled_for' in row.keys() else ''
        platform = row['platform'] if 'platform' in row.keys() else ''
        meta = PLATFORM_META.get(platform, {'label': platform, 'color': '#8b93a7'})
        events.append({
            'day': when[:10] if len(when) >= 10 else '',
            'time': when[11:16] if len(when) >= 16 else '',
            'platform': platform,
            'label': meta['label'],
            'color': meta['color'],
            'title': _title(row),
            'status': status,
        })
    events = [e for e in events if e['day']]
    slots = database.list_time_slots()
    slots_by_day = {d: [] for d in range(7)}
    for s in slots:
        d = s.get('day_of_week')
        if d is None or d < -1 or d > 6:
            continue
        if d == -1:
            for k in slots_by_day:
                slots_by_day[k].append(s)
        else:
            slots_by_day[d].append(s)
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(database.DB_PATH)
    limit_rows = conn.execute("SELECT platform, max_posts_per_day FROM platform_daily_limits").fetchall()
    conn.close()
    limits_list = [{'platform': r[0], 'max': r[1]} for r in limit_rows]
    return render_template('schedule.html', year=year, month=month, events=events,
                           slots=slots, slots_by_day=slots_by_day, limits=limits_list,
                           platform_meta=PLATFORM_META)

@app.route('/status')
def status_page():
    """Display processing status for all episodes."""
    sort = request.args.get('sort', 'released')
    sort_order = request.args.get('order', 'desc')
    filter_status = request.args.get('status', '')
    filter_feed = request.args.get('feed', '', type=str)
    filter_type = request.args.get('type', '')
    search_query = request.args.get('q', '').lower()
    
    if sort == 'released':
        order_by = 'published'
    elif sort == 'processed':
        order_by = 'processed_at'
    else:
        order_by = 'id'
    
    uid = current_user()['id']
    all_episodes = list_all_episodes(order_by=order_by, user_id=uid)
    feeds_list = list_feeds(uid)
    feeds = {f["id"]: f["title"] for f in feeds_list}

    ep_channels = {
        ep["id"]: ep["channel"]
        for ep in all_episodes
        if not ep["feed_id"] and ep["channel"]
    }

    # Filter episodes
    filtered_episodes = []
    for ep in all_episodes:
        # Status filter
        if filter_status and ep['status'] != filter_status:
            continue
        
        # Feed filter
        if filter_feed and str(ep['feed_id']) != filter_feed:
            continue
        
        # Type filter (audio vs text)
        is_audio = ep['url'].lower().split('?')[0].endswith(('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac'))
        ep_type = 'audio' if is_audio else 'text'
        if filter_type and ep_type != filter_type:
            continue
        
        # Search filter
        if search_query and search_query not in (ep['title'] or '').lower():
            continue
        
        filtered_episodes.append(ep)
    
    # Reverse order if ascending
    if sort_order == 'asc':
        filtered_episodes = list(reversed(filtered_episodes))
    
    return render_template(
        'status.html',
        episodes=filtered_episodes,
        feeds=feeds,
        feeds_list=feeds_list,
        ep_channels=ep_channels,
        sort=sort,
        sort_order=sort_order,
        filter_status=filter_status,
        filter_feed=filter_feed,
        filter_type=filter_type,
        search_query=search_query,
    )

@app.route('/episode/<int:episode_id>/reprocess')
def reprocess_episode(episode_id: int):
    """Reprocess an episode - clears existing data and requeues."""
    uid = current_user()['id']
    episode = get_episode_by_id(episode_id, uid)
    if not episode:
        abort(404)

    audio_extensions = ('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac')
    is_audio = (
        episode['url'].lower().split('?')[0].endswith(audio_extensions)
        or is_youtube_url(episode['url'])
    )

    reset_episode_for_reprocess(episode_id, uid)

    if is_audio:
        # Queue for background processing
        _user = current_user()
        task_queue.put({
            'url': episode['url'],
            'title': episode['title'],
            'feed_id': episode['feed_id'],
            'published': episode['published'],
            'user_id': _user['id'] if _user else None,
            'username': _user['username'] if _user else None,
        })
        return redirect(url_for('status_page'))
    else:
        # Process text article directly
        return redirect(url_for(
            'process_text_article',
            url=episode['url'],
            title=episode['title'],
            feed_id=episode['feed_id'],
            published=episode['published'],
        ))

@app.route('/episode/<int:episode_id>/delete')
def delete_episode(episode_id: int):
    """Delete an episode and all associated data."""
    uid = current_user()['id']
    if not get_episode_by_id(episode_id, uid):
        abort(404)
    delete_episode_by_id(episode_id, uid)
    return redirect(url_for('status_page'))

@app.route('/episodes/bulk-delete', methods=['POST'])
def bulk_delete_episodes():
    """Delete multiple episodes at once."""
    uid = current_user()['id']
    episode_ids = request.form.getlist('episode_ids', type=int)
    if episode_ids:
        delete_episodes_bulk(episode_ids, uid)
    return redirect(url_for('status_page'))

@app.route('/episodes/backfill-channels', methods=['POST'])
def backfill_youtube_channels():
    """Populate channel names for existing YouTube episodes missing them."""
    episodes = get_youtube_episodes_missing_channel()
    updated = 0
    for ep in episodes:
        try:
            meta = fetch_youtube_metadata(ep["url"])
            channel = meta.get("channel") or None
            if channel:
                set_episode_channel(ep["id"], channel)
                updated += 1
        except Exception:
            continue
    return jsonify({"success": True, "updated": updated, "total": len(episodes)})

@app.route('/generate_article', methods=['POST'])
def create_article():
    """Generate an article based on podcast or text article content."""
    episode_url = request.form.get('episode_url')
    topic = request.form.get('topic', '').strip()
    style = request.form.get('style', 'blog')
    extra_context = request.form.get('extra_context', '').strip()

    if not episode_url or not topic:
        return redirect(request.referrer or url_for('index'))

    uid = current_user()['id']
    episode = get_episode(episode_url, uid)
    if not episode:
        abort(404)

    # Detect if this is a text article (not an audio file)
    audio_extensions = ('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac')
    is_text_source = not episode_url.lower().split('?')[0].endswith(audio_extensions)

    # Get the podcast/publication title for attribution
    feed = get_feed_by_id(episode['feed_id'], uid) if episode['feed_id'] else None
    podcast_title = feed['title'] if feed else ("the publication" if is_text_source else "the podcast")
    episode_title = episode['title'] or ("this article" if is_text_source else "this episode")

    try:
        # Generate article using OpenAI
        article_content = generate_article(
            transcript=episode['transcript'],
            summary=episode['summary'],
            topic=topic,
            podcast_title=podcast_title,
            episode_title=episode_title,
            style=style,
            extra_context=extra_context if extra_context else None,
            is_text_source=is_text_source,
            lang=g.lang,
        )
        # Save article to database
        article_id = add_article(
            episode_id=episode['id'],
            topic=topic,
            style=style,
            content=article_content,
            user_id=uid,
        )
        return redirect(url_for('view_article', article_id=article_id))
    except Exception as exc:
        app.logger.exception("Failed to generate article")
        return render_template(
            'article_error.html',
            error=str(exc),
            episode_url=episode_url,
            feed_id=episode['feed_id'],
        )

@app.route('/article/<int:article_id>')
def view_article(article_id: int):
    """Display a generated article."""
    article = get_article(article_id, current_user()['id'])
    if not article:
        abort(404)

    return render_template('article.html', article=dict(article))

@app.route('/article/<int:article_id>/edit', methods=['GET', 'POST'])
def edit_article(article_id: int):
    """Edit an existing article."""
    uid = current_user()['id']
    article = get_article(article_id, uid)
    if not article:
        abort(404)

    if request.method == 'POST':
        topic = request.form.get('topic', '').strip()
        style = request.form.get('style', '').strip()
        content = request.form.get('content', '').strip()

        if topic and content:
            update_article(article_id, uid, topic=topic, style=style, content=content)
            return redirect(url_for('view_article', article_id=article_id))

    return render_template('article_edit.html', article=dict(article))

@app.route('/article/<int:article_id>/delete', methods=['POST'])
def remove_article(article_id: int):
    """Delete an article."""
    uid = current_user()['id']
    if not get_article(article_id, uid):
        abort(404)
    delete_social_posts_for_article(article_id, uid)
    delete_article(article_id, uid)
    return redirect(url_for('view_articles'))

@app.route('/posts/bulk-replace', methods=['POST'])
def bulk_replace_posts():
    """Replace text in all posts of a given type, optionally filtered by post IDs."""
    from database import bulk_replace_post_content
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    find_text = data.get('find', '')
    replace_text = data.get('replace', '')
    post_type = data.get('post_type', 'standalone')  # 'standalone' only
    case_sensitive = data.get('case_sensitive', False)
    whole_word = data.get('whole_word', False)
    post_ids = data.get('post_ids')  # Optional list of post IDs to filter
    excluded_matches = data.get('excluded_matches')  # Optional dict of excluded matches
    
    if not find_text:
        return jsonify({"error": "Find text is required"}), 400
    
    if post_type != 'standalone':
        return jsonify({"error": "post_type must be 'standalone'"}), 400

    uid = current_user()['id']

    # Convert post_ids to integers if provided
    if post_ids:
        try:
            post_ids = [int(pid) for pid in post_ids]
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid post IDs"}), 400

    try:
        affected_count = bulk_replace_post_content(
            find_text=find_text,
            replace_text=replace_text,
            post_type=post_type,
            user_id=uid,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            post_ids=post_ids,
            excluded_matches=excluded_matches,
        )
        return jsonify({
            "success": True,
            "affected_count": affected_count,
            "message": f"Replaced in {affected_count} post(s)"
        })
    except Exception as exc:
        app.logger.exception("Failed to bulk replace posts")
        return jsonify({"error": str(exc)}), 500

@app.route('/article/<int:article_id>/refine', methods=['POST'])
def refine_article_with_ai(article_id: int):
    """Refine an article using AI based on user feedback."""
    uid = current_user()['id']
    article = get_article(article_id, uid)
    if not article:
        return {"error": "Article not found"}, 404

    feedback = request.form.get('feedback', '').strip()
    if not feedback:
        return {"error": "Please provide feedback for how to refine the article"}, 400

    try:
        refined_content = refine_article(
            current_content=article['content'],
            user_feedback=feedback,
            article_topic=article['topic'],
            lang=g.lang,
        )

        # Optionally auto-save the refined content
        auto_save = request.form.get('auto_save', 'false') == 'true'
        if auto_save:
            update_article(article_id, uid, content=refined_content)
        
        return {"success": True, "refined_content": refined_content, "saved": auto_save}
    except Exception as exc:
        app.logger.exception("Failed to refine article")
        return {"error": str(exc)}, 500

@app.route('/articles')
def view_articles():
    """Display all generated articles."""
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')
    filter_style = request.args.get('style', '')
    filter_podcast = request.args.get('podcast', '')
    search_query = request.args.get('q', '').lower()
    
    raw_articles = [dict(a) for a in list_articles(None, current_user()['id'])]
    
    # Collect unique styles and podcasts for filter dropdowns
    all_styles = set()
    all_podcasts = set()
    
    articles = []
    for a in raw_articles:
        all_styles.add(a['style'])
        if a.get('podcast_title'):
            all_podcasts.add(a['podcast_title'])
        
        # Filter by style
        if filter_style and a['style'] != filter_style:
            continue
        
        # Filter by podcast
        if filter_podcast and a.get('podcast_title') != filter_podcast:
            continue
        
        # Search filter
        if search_query:
            searchable = f"{a['topic']} {a.get('episode_title', '')} {a.get('podcast_title', '')}".lower()
            if search_query not in searchable:
                continue
        
        articles.append(a)
    
    # Sort articles
    if sort_by == 'topic':
        articles.sort(key=lambda x: x['topic'].lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'style':
        articles.sort(key=lambda x: x['style'].lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'podcast':
        articles.sort(key=lambda x: (x.get('podcast_title') or '').lower(), reverse=(sort_order == 'desc'))
    else:  # Default: date
        articles.sort(key=lambda x: x['created_at'] or '', reverse=(sort_order == 'desc'))
    
    return render_template(
        'articles.html',
        articles=articles,
        sort_by=sort_by,
        sort_order=sort_order,
        filter_style=filter_style,
        filter_podcast=filter_podcast,
        search_query=search_query,
        all_styles=sorted(all_styles),
        all_podcasts=sorted(all_podcasts),
    )

POSTS_PAGE_SIZE = 20
# The most cards one /compose/posts/more request will render. "Load all" asks
# for this many at a time rather than for everything at once, so a library of
# thousands of posts arrives as it renders instead of as one huge response.
POSTS_MAX_BATCH = 100

# Every platform a saved post can target, in the order a Compose card draws its
# ticks. One list so the chips, the composer and the filter bar agree. The app
# is Chinese-first: only the domestic platforms are offered.
COMPOSE_PLATFORMS = ('xiaohongshu', 'douyin', 'wechat', 'zhihu', 'toutiao')

# Platforms with no posting API yet: content is generated and saved as
# reviewable drafts, and the card's publish button is a reserved placeholder —
# publishing will go through an open-source publisher once one is wired in.
# These platforms are refused by the schedule/publish routes.
DRAFT_ONLY_PLATFORMS = ('xiaohongshu', 'douyin', 'wechat')

def _group_standalone_posts(rows):
    """Collapse per-platform rows into one entry per distinct post.

    Compose shows a single card per piece of copy, with a tick for each platform
    it goes to. The database still stores one row per (platform, content) — that
    row is what queueing, scheduling and publishing key on — so the card is a
    view over a set of rows, not a new kind of record.

    Rows group when they share the content, the image and the video, capped at one
    row per target: a deliberate repost (the importer's ``repost`` column writes a
    second identical row for the same target) opens its own card instead of
    disappearing into the first one.

    A target is a platform plus the account it publishes as, not a platform on
    its own, which is what lets one card carry two accounts for the same
    platform side by side instead of the second row starting a card of its own.

    ``rows`` must already be in display order; each group takes the position of
    its first row, kept as ``head`` so sorts have a representative row.
    """
    groups = []
    by_key = {}
    for row in rows:
        key = (row['content'] or '', row['image_url'] or '', _row_video(row))
        slot = _row_target_key(row)
        candidates = by_key.setdefault(key, [])
        for group in candidates:
            if slot not in group['platforms']:
                group['platforms'][slot] = row
                break
        else:
            group = {'platforms': {slot: row}, 'head': row}
            groups.append(group)
            candidates.append(group)
    return groups

def _row_video(row):
    """A saved post row's video URL, or '' for one without (or from before videos)."""
    return (row['video_url'] if 'video_url' in row.keys() else None) or ''

def _account_label(account, platform=None):
    """What to call an account on screen: its label, profile name or handle.

    Mirrors what the retired multi-account publisher did, so the composer's
    chips can still tell two logins on one platform apart.
    """
    if account is None:
        return domestic_publisher.display_name(platform or '')
    for field in ('label', 'display_name', 'handle'):
        if field in account.keys() and account[field]:
            return str(account[field])
    return domestic_publisher.display_name(account['platform'] if 'platform' in account.keys() else (platform or ''))

def _row_target_key(row):
    """The (platform, account) a saved post row publishes to.

    A row with no account is one written before accounts existed, or one aimed
    at whichever account is default. It keys on None so it stays a single slot
    on the card rather than colliding with a row that names an account.
    """
    account_id = row['account_id'] if 'account_id' in row.keys() else None
    return (row['platform'], account_id)

def _group_row_for_platform(group, platform):
    """The card's row for a platform, or None. First account wins when several."""
    for (row_platform, _account), row in group['platforms'].items():
        if row_platform == platform:
            return row
    return None

def _ordered_group_rows(group):
    """A card's rows in the order its target chips are drawn.

    Platform order first, then the accounts within a platform in the order they
    were connected, so a card's chips do not reshuffle between page loads.
    """
    slots = group['platforms']
    ordered = []
    for platform in COMPOSE_PLATFORMS:
        for slot in sorted(
            (s for s in slots if s[0] == platform),
            key=lambda s: (s[1] is not None, s[1] or 0),
        ):
            ordered.append(slots[slot])
    # Anything unrecognised (older or hand-written data) still gets shown.
    ordered.extend(row for slot, row in slots.items() if slot[0] not in COMPOSE_PLATFORMS)
    return ordered

def _rows_for_groups(groups, platform=None):
    """Flatten cards back to the rows the row-level endpoints act on."""
    rows = []
    for group in groups:
        for row in _ordered_group_rows(group):
            if not platform or row['platform'] == platform:
                rows.append(row)
    return rows

def _json_list_column(row, column):
    """Read a JSON-list column, tolerating NULL and older malformed values."""
    raw = row[column] if column in row.keys() else None
    try:
        value = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []

def _account_lookup():
    """Every connected account by id, read once per request.

    A page of cards asks for the same handful of accounts over and over, so the
    lookup is cached on the request rather than costing a query per chip.
    """
    cached = getattr(g, '_social_accounts', None)
    if cached is None:
        cached = {row['id']: row for row in list_social_accounts()}
        g._social_accounts = cached
    return cached

def _default_account_for(platform):
    """The account id a bare platform name resolves to, from the cached lookup."""
    for account in _account_lookup().values():
        if account['platform'] == platform and account['is_default']:
            return account['id']
    for account in _account_lookup().values():
        if account['platform'] == platform:
            return account['id']
    return None

def _publish_target_options():
    """Every (platform, account) a card can be ticked for.

    One entry per connected account, plus the platform itself when it has none
    connected yet, so the composer still offers the chip and the user finds out
    it needs connecting when they try to post rather than when they go looking.
    ``show_label`` is on only where a platform has more than one account: with a
    single login there is nothing to tell apart and the chip stays as it was.
    """
    accounts = list(_account_lookup().values())
    options = []
    for platform in COMPOSE_PLATFORMS:
        mine = [a for a in accounts if a['platform'] == platform]
        if not mine:
            options.append({
                'platform': platform, 'account_id': None,
                'label': None, 'show_label': False, 'is_default': True,
            })
            continue
        for account in mine:
            options.append({
                'platform': platform,
                'account_id': account['id'],
                'label': _account_label(account, platform),
                'show_label': len(mine) > 1,
                'is_default': bool(account['is_default']),
            })
    return options

@app.context_processor
def _inject_publish_targets():
    """Hand every card render the targets it can offer, and the push state.

    Cards are rendered from several places (the page, a card refresh, a partial
    after a tick), so the target list comes from the environment rather than
    each caller remembering to pass it. The push button on a card reads the
    same way: whether the local agent is online and which of its platforms are
    logged in is a property of this moment, not of the route that rendered.
    """
    return {'PUBLISH_TARGETS': _publish_target_options(),
            'DRAFT_ONLY_PLATFORMS': DRAFT_ONLY_PLATFORMS,
            'push_platforms': {
                p: {"label": domestic_publisher.display_name(p),
                    "enabled": p in domestic_publisher.enabled_platforms()}
                for p in domestic_publisher.DOMESTIC_ARTICLE_PLATFORMS},
            'agent_online': agent_hub.agent_online(),
            'login_states': agent_hub.login_states_view()}

def _row_account_label(row):
    """What to call the account a card row publishes as, or None.

    None means "this platform's default account", which is every row in a setup
    with one account per platform: the chip then reads exactly as it did before
    accounts existed instead of sprouting a label.
    """
    account_id = row['account_id'] if 'account_id' in row.keys() else None
    if not account_id:
        return None
    accounts = _account_lookup()
    if len([a for a in accounts.values() if a['platform'] == row['platform']]) < 2:
        # Only one account on this platform, so naming it adds nothing.
        return None
    account = accounts.get(account_id)
    return _account_label(account, row['platform']) if account else None

def _enrich_post_group(group, scheduled_info, posted_info, brief_names):
    """Turn a group of standalone_posts rows into the template's card dict."""
    rows = _ordered_group_rows(group)
    primary = rows[0]

    platforms = [
        {
            'platform': row['platform'],
            'id': row['id'],
            # A row written before it carried an account, or one aimed at "just
            # the platform", reports the platform's default account so its chip
            # matches the target the composer offers for that platform.
            'account_id': (row['account_id'] if 'account_id' in row.keys() else None)
                          or _default_account_for(row['platform']),
            'account_label': _row_account_label(row),
            'queued': scheduled_info.get(row['id'], {}).get(row['platform']),
            'posted': posted_info.get(row['id'], {}).get(row['platform']),
        }
        for row in rows
    ]

    brief_id = primary['brief_id'] if 'brief_id' in primary.keys() else None
    card = {
        'id': primary['id'],
        'post_ids': [row['id'] for row in rows],
        # platform -> one row id, which is what the older per-platform controls
        # address. With two accounts on a platform the first one answers here;
        # anything that has to reach every target reads `targets` instead.
        'platform_ids': {row['platform']: row['id'] for row in rows},
        'targets': [
            {'post_id': entry['id'], 'platform': entry['platform'],
             'account_id': entry['account_id'], 'account_label': entry['account_label']}
            for entry in platforms
        ],
        'platforms': platforms,
        'content': primary['content'],
        'image_url': primary['image_url'],
        'video_url': _row_video(primary) or None,
        # A card only reads as used once every platform it targets is done.
        'used': all(row['used'] for row in rows),
        'source_type': primary['source_type'],
        'created_at': primary['created_at'],
        'brief_id': brief_id,
        'brief_name': brief_names.get(brief_id) if brief_id else None,
        'display_index': group.get('display_index'),
    }
    return card

@app.route('/compose')
def compose_page():
    """Command Center page for generating social media posts."""
    uid = current_user()['id']
    # All saved posts (newest first) — cheap to load; we only render the first page.
    posts = list_standalone_posts(uid)

    # Map brief ids -> names so agent-curated posts can show/filter by their brief
    brief_names = {brief['id']: brief['name'] for brief in list_content_briefs(uid)}

    # One card per distinct post; only the first page of cards is enriched and
    # rendered, so the payload stays small however many posts are saved.
    groups = _group_standalone_posts(posts)
    for position, group in enumerate(groups, start=1):
        group['display_index'] = position
    first_page = groups[:POSTS_PAGE_SIZE]

    page_ids = [row['id'] for group in first_page for row in group['platforms'].values()]
    scheduled_info = get_pending_schedules_for_standalone_posts(page_ids, uid) if page_ids else {}
    posted_info = get_posted_info_for_standalone_posts(page_ids, uid) if page_ids else {}
    post_groups = [
        _enrich_post_group(group, scheduled_info, posted_info, brief_names)
        for group in first_page
    ]

    # Distinct briefs across ALL saved posts (not just the first page), for the filter
    briefs_in_posts = {}
    for post in posts:
        bid = post['brief_id'] if 'brief_id' in post.keys() else None
        if bid and bid in brief_names:
            briefs_in_posts[bid] = brief_names[bid]
    brief_filter_options = [
        {'id': bid, 'name': name}
        for bid, name in sorted(briefs_in_posts.items(), key=lambda kv: kv[1].lower())
    ]
    
    # Get saved URL sources for the "From Saved Source" tab
    saved_sources = list_url_sources(uid)

    # Check if a specific source is requested
    selected_source_id = request.args.get('source_id', type=int)
    selected_source = None
    if selected_source_id:
        selected_source = get_url_source(selected_source_id, uid)

    # Get recent freeform prompts for reuse
    recent_prompts = list_recent_prompts(uid, limit=20)
    recent_prompts_list = [
        {
            'content': p['source_content'],
            'preview': p['source_content'][:80] + ('...' if len(p['source_content']) > 80 else ''),
            'created_at': p['created_at'],
        }
        for p in recent_prompts
    ]
    
    # Get recent image prompt+image combos for reuse
    recent_img = list_recent_image_prompts(uid, limit=10)
    recent_image_prompts_list = [
        {
            'prompt': r['source_content'] or '',
            'image_url': r['image_url'],
            'preview': (r['source_content'][:60] + '...' if r['source_content'] and len(r['source_content']) > 60
                        else r['source_content']) or '(no prompt)',
            'created_at': r['created_at'],
        }
        for r in recent_img
    ]

    # Local drafts for the composer's right-hand column: manual posts that no
    # platform has marked used yet, grouped so one post aimed at several targets
    # shows once. Derived from the in-memory groups — no extra DB pass.
    draft_groups = [g for g in groups
                    if g['head']['source_type'] == 'manual'
                    and not any(r['used'] for r in g['platforms'].values())]
    local_drafts = [{
        'id': g['head']['id'],
        'content': g['head']['content'],
        'created_at': g['head']['created_at'],
    } for g in draft_groups[:5]]

    # Curated, named prompts from the reusable library
    library_prompts_list = _serialize_library_prompts()

    return render_template(
        'compose.html',
        post_groups=post_groups,
        group_total=len(groups),
        compose_platforms=list(COMPOSE_PLATFORMS),
        push_platforms={
            p: {"label": domestic_publisher.display_name(p),
                "enabled": p in domestic_publisher.enabled_platforms()}
            for p in domestic_publisher.DOMESTIC_ARTICLE_PLATFORMS},
        agent_online=agent_hub.agent_online(),
        login_states=agent_hub.login_states_view(),
        page_size=POSTS_PAGE_SIZE,
        max_batch=POSTS_MAX_BATCH,
        brief_filter_options=brief_filter_options,
        local_drafts=local_drafts,
        saved_sources=saved_sources,
        selected_source=selected_source,
        recent_prompts=recent_prompts_list,
        recent_image_prompts=recent_image_prompts_list,
        library_prompts=library_prompts_list,
        starter_prompt_groups=grouped_starter_prompts(),
        model_options=provider_model_options(),
        file_formats=[f for f in document_extractor.supported_formats() if f['available']],
        file_accept=document_extractor.accept_attribute(),
    )

@app.route('/compose/posts/more')
def compose_posts_more():
    """Return a page of saved post cards as an HTML fragment.

    Honours the Compose filter bar. The page renders only the first
    POSTS_PAGE_SIZE cards, so filtering in the browser could only ever reveal
    matches that happened to be loaded already — a post further down the list
    stayed invisible no matter what the filter said. Paging through the
    *filtered* set here is what lets a filter reach every saved post.

    ``total`` is how many cards match, which the caller needs for the count
    badge and the "Load more (N of total)" label.

    ``limit`` sets how many cards to return (default POSTS_PAGE_SIZE, at most
    POSTS_MAX_BATCH); "Load all" and scroll loading page with it.
    """
    uid = current_user()['id']
    offset = request.args.get('offset', 0, type=int) or 0
    if offset < 0:
        offset = 0
    limit = request.args.get('limit', POSTS_PAGE_SIZE, type=int) or POSTS_PAGE_SIZE
    limit = max(1, min(limit, POSTS_MAX_BATCH))

    groups, _ = _filtered_post_groups(request.args)

    total = len(groups)
    page = groups[offset:offset + limit]
    has_more = total > offset + len(page)

    ids = [row['id'] for group in page for row in group['platforms'].values()]
    scheduled_info = get_pending_schedules_for_standalone_posts(ids, uid) if ids else {}
    posted_info = get_posted_info_for_standalone_posts(ids, uid) if ids else {}
    brief_names = {brief['id']: brief['name'] for brief in list_content_briefs(uid)}
    enriched = [
        _enrich_post_group(group, scheduled_info, posted_info, brief_names)
        for group in page
    ]

    html = render_template(
        'partials/post_items.html',
        groups=enriched,
    )
    return jsonify({
        "html": html,
        "has_more": has_more,
        "next_offset": offset + len(page),
        "total": total,
    })

def _valid_post_platform(raw):
    """Normalize an optional ?platform= filter, returning None when unset."""
    platform = (raw or '').strip()
    return platform or None

# The Compose filter bar's non-platform filters, in the same vocabulary the
# <select> options use, so the server can reproduce the client's predicate.
_POST_FILTER_KEYS = ('used', 'queued', 'image', 'source', 'brief')

# Sort orders for the Compose list, keyed by the <select> values. The default
# ('' / unknown) is the newest-first order list_standalone_posts() already
# returns. Sorts are ascending by key: 'longest' negates the length rather than
# reversing so that ties keep the newest-first order in every case.
_POST_SORT_KEYS = {
    'oldest': lambda row: row['created_at'] or '',
    'longest': lambda row: -len(row['content'] or ''),
    'shortest': lambda row: len(row['content'] or ''),
}

def _sort_post_groups(groups, sort):
    """Order matched cards for the Compose sort control.

    Applied after filtering, and after the display indexes are taken, so a post
    keeps the same "Post N" label whichever order it is shown in. Each card
    sorts by its ``head`` row — the one whose position put the card where the
    unsorted list had it — so a card can't change places just because one of its
    platforms was added later.
    """
    key = _POST_SORT_KEYS.get((sort or '').strip())
    if key is None:
        return groups
    return sorted(groups, key=lambda group: key(group['head']))

def _group_matches_filters(rows, filters, scheduled_info):
    """Mirror the client-side filter bar's predicate for one card.

    A card is the same copy on one or more platforms, so the filters that read
    per-platform state match on *any* of them: a post already queued on one
    platform but not on another still belongs in the "Not Queued" list, or its
    tick could never be queued from there. A platform filter narrows that to
    the platform being asked about.
    """
    platform = filters.get('platform')
    if platform and platform not in {row['platform'] for row in rows}:
        return False

    used = filters.get('used')
    all_used = all(row['used'] for row in rows)
    if used == 'used' and not all_used:
        return False
    if used == 'unused' and all_used:
        return False

    queued = filters.get('queued')
    if queued:
        asked = [row for row in rows if not platform or row['platform'] == platform]
        states = [bool(scheduled_info.get(row['id'], {}).get(row['platform'])) for row in asked]
        if queued == 'queued' and not any(states):
            return False
        if queued == 'not-queued' and states and all(states):
            return False

    image = filters.get('image')
    has_image = any(row['image_url'] for row in rows)
    if image == 'has-image' and not has_image:
        return False
    if image == 'no-image' and has_image:
        return False

    source = filters.get('source')
    if source:
        kinds = {'agent' if row['source_type'] == 'agent' else 'manual' for row in rows}
        if source not in kinds:
            return False

    brief = filters.get('brief')
    if brief:
        brief_ids = {
            str((row['brief_id'] if 'brief_id' in row.keys() else None) or '')
            for row in rows
        }
        if brief not in brief_ids:
            return False

    return True

def _filtered_post_groups(args):
    """Return (matching cards, index_by_id) for the Compose filters in ``args``.

    Reproduces the filter bar server-side so "select all across every page",
    "Load more" and Find & Replace all act on exactly the filtered set rather
    than only the posts currently rendered.

    Cards are grouped over *every* saved post before filtering, so a card always
    shows every platform its copy goes to — including platforms the current
    filter excludes, which is what the ticks have to reflect to be honest.
    ``index_by_id`` maps each row to its card's 1-based position in the
    unfiltered list: the "Post N" label the page and Find & Replace both show.

    ``args`` is a request.args multidict or a plain dict of the same keys.
    """
    uid = current_user()['id']
    filters = {key: (args.get(key) or '').strip() for key in _POST_FILTER_KEYS}
    filters['platform'] = _valid_post_platform(args.get('platform'))

    groups = _group_standalone_posts(list_standalone_posts(uid))

    index_by_id = {}
    for position, group in enumerate(groups, start=1):
        group['display_index'] = position
        for row in group['platforms'].values():
            index_by_id[row['id']] = position

    # Pending schedules are only needed (and only paid for) by the queued filter.
    scheduled_info = {}
    if filters['queued']:
        ids = list(index_by_id)
        scheduled_info = get_pending_schedules_for_standalone_posts(ids, uid) if ids else {}

    matched = [
        group for group in groups
        if _group_matches_filters(_ordered_group_rows(group), filters, scheduled_info)
    ]
    return _sort_post_groups(matched, args.get('sort')), index_by_id

def _filtered_standalone_posts(args):
    """Return (rows behind the matching cards, index_by_id) for ``args``.

    Find & Replace and the bulk actions still work one post row at a time, so
    they take the rows of every matching card. Deriving both from
    _filtered_post_groups is what keeps them acting on exactly the cards the
    page is showing — there is no second copy of the predicate to drift.

    A platform filter narrows to that platform's rows: with the list showing
    only one platform, a bulk delete must not take its sibling rows with it.
    """
    groups, index_by_id = _filtered_post_groups(args)
    return _rows_for_groups(groups, _valid_post_platform(args.get('platform'))), index_by_id

def _selected_standalone_posts(payload, user_id: int):
    """Resolve a Compose bulk-action selection to the rows it should act on.

    Two payload shapes:
      ``{"post_ids": [...]}``  the checkboxes ticked on the page
      ``{"filters": {...}}``   "select all across every page" — the server
                               re-derives the matching set from the filter bar,
                               so the browser never has to ship (or go stale on)
                               thousands of ids.

    Ids naming another user's rows are dropped silently: the caller asked for
    "these posts of mine", and those were never among them.

    Returns ``(rows, error)`` where ``error`` is a ready-to-return response.
    """
    filters = payload.get('filters')
    if filters is not None:
        if not isinstance(filters, dict):
            return None, (jsonify({"error": "filters must be an object"}), 400)
        rows, _ = _filtered_standalone_posts(filters)
        return rows, None

    raw_ids = payload.get('post_ids') or []
    if not raw_ids:
        return None, (jsonify({"error": "No posts selected"}), 400)
    try:
        wanted = {int(pid) for pid in raw_ids}
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Invalid post IDs"}), 400)
    return [r for r in list_standalone_posts(user_id) if r['id'] in wanted], None

@app.route('/compose/posts/ids')
def compose_posts_ids():
    """Return the id + platform of saved posts matching the Compose filters.

    Bulk actions resolve their own target set server-side from the filters, so
    this is only for the paths that genuinely need per-post ids in the browser
    (Post Now, which publishes one post per request). Pass ``count_only=1`` to
    get just the totals (used to size the "select all" offer banner).
    """
    groups, _ = _filtered_post_groups(request.args)
    rows = _rows_for_groups(groups, _valid_post_platform(request.args.get('platform')))

    by_platform = {}
    for row in rows:
        by_platform[row['platform']] = by_platform.get(row['platform'], 0) + 1

    payload = {
        "success": True,
        # Rows are what the actions touch; cards are what the page counts.
        "count": len(rows),
        "group_count": len(groups),
        "by_platform": by_platform,
    }
    if not request.args.get('count_only'):
        payload["posts"] = [{'id': row['id'], 'platform': row['platform']} for row in rows]
    return jsonify(payload)

# How many matching posts the Find & Replace modal renders at once. Replace All
# still applies to every match — the cap only bounds the preview list.
POST_SEARCH_RESULT_LIMIT = 200

def _search_flag(value):
    """Read a checkbox-style query/JSON parameter."""
    return str(value if value is not None else '').lower() in ('1', 'true', 'yes', 'on')

def _search_exclude_words(raw):
    """Parse the modal's comma-separated 'exclude posts containing' list."""
    if isinstance(raw, list):
        parts = raw
    else:
        parts = str(raw or '').split(',')
    return [w.strip().lower() for w in parts if w and w.strip()]

def _post_search_pattern(find_text, case_sensitive, whole_word):
    """The Find & Replace pattern — identical to the one the replace applies."""
    flags = 0 if case_sensitive else re.IGNORECASE
    body = re.escape(find_text)
    if whole_word:
        body = r'\b' + body + r'\b'
    return re.compile(body, flags)

@app.route('/compose/posts/search')
def compose_posts_search():
    """Run the Find & Replace search over every saved post, server-side.

    The modal used to download all post text and match it in JavaScript. Doing
    it here keeps the response to the posts that actually match, and makes the
    preview use the exact same regex that /compose/posts/replace will apply —
    previously JS did the finding and Python did the replacing, so the two could
    disagree on what matched.
    """
    find_text = request.args.get('find') or ''
    if not find_text.strip():
        return jsonify({"error": "find is required"}), 400

    case_sensitive = _search_flag(request.args.get('case_sensitive'))
    whole_word = _search_flag(request.args.get('whole_word'))
    exclude_words = _search_exclude_words(request.args.get('exclude'))
    limit = request.args.get('limit', POST_SEARCH_RESULT_LIMIT, type=int)
    if limit is None or limit < 0:
        limit = POST_SEARCH_RESULT_LIMIT

    # One result per card, not per platform row. The rows behind a card all hold
    # the same copy, so listing them separately would show the same hit two or
    # three times over — and replacing in only some of them would split the card.
    groups, _ = _filtered_post_groups(request.args)
    platform = _valid_post_platform(request.args.get('platform'))
    pattern = _post_search_pattern(find_text, case_sensitive, whole_word)

    posts = []
    matched_posts = 0
    total_matches = 0
    excluded_posts = 0
    for group in groups:
        rows = _rows_for_groups([group], platform)
        if not rows:
            continue
        primary = rows[0]
        content = primary['content'] or ''
        if not content.strip():
            continue
        if exclude_words:
            lowered = content.lower()
            if any(word in lowered for word in exclude_words):
                excluded_posts += 1
                continue

        count = len(pattern.findall(content))
        if not count:
            continue

        matched_posts += 1
        total_matches += count
        if len(posts) < limit:
            posts.append({
                'id': primary['id'],
                'post_ids': [row['id'] for row in rows],
                'platform': primary['platform'],
                'platforms': [row['platform'] for row in rows],
                'content': content,
                'index': group.get('display_index'),
                'match_count': count,
            })

    return jsonify({
        "success": True,
        "posts": posts,
        "matched_posts": matched_posts,
        "total_matches": total_matches,
        "excluded_posts": excluded_posts,
        "searched": len(groups),
        "truncated": matched_posts > len(posts),
    })

@app.route('/compose/posts/replace', methods=['POST'])
def compose_posts_replace():
    """Apply Find & Replace to every saved post matching the search criteria.

    Takes the same criteria as /compose/posts/search rather than a list of ids:
    the preview list is capped, so the ids the browser knows about are not
    necessarily the whole match set. ``deselected_ids`` carries the posts the
    user unticked, and ``excluded_matches`` the individual matches they clicked
    to skip (both only exist for posts that were rendered).
    """
    uid = current_user()['id']
    from database import bulk_replace_post_content

    payload = request.get_json(silent=True) or {}
    find_text = payload.get('find') or ''
    if not find_text.strip():
        return jsonify({"error": "Find text is required"}), 400
    replace_text = payload.get('replace') or ''

    filters = payload.get('filters') or {}
    if not isinstance(filters, dict):
        return jsonify({"error": "filters must be an object"}), 400

    case_sensitive = _search_flag(payload.get('case_sensitive'))
    whole_word = _search_flag(payload.get('whole_word'))
    exclude_words = _search_exclude_words(payload.get('exclude'))
    excluded_matches = payload.get('excluded_matches') or {}

    try:
        deselected = {int(pid) for pid in (payload.get('deselected_ids') or [])}
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid post IDs"}), 400

    groups, _ = _filtered_post_groups(filters)
    platform = _valid_post_platform(filters.get('platform'))
    rows = _rows_for_groups(groups, platform)

    # The results list shows one entry per card, so a post the user unticked —
    # or an individual match they clicked to skip — has to carry to every row
    # behind that card. Applying it to the listed row alone would leave the other
    # platforms with the old text, splitting the card in two.
    siblings = {}
    for group in groups:
        ids = [row['id'] for row in _rows_for_groups([group], platform)]
        for pid in ids:
            siblings[pid] = ids

    deselected = {sid for pid in deselected for sid in siblings.get(pid, [pid])}

    # excluded_matches is keyed "<post id>-<match index>"; re-key each entry onto
    # every row of its card.
    expanded_matches = {}
    for key, value in excluded_matches.items():
        listed_id, _, match_index = str(key).partition('-')
        if not listed_id.isdigit():
            continue
        for sid in siblings.get(int(listed_id), [int(listed_id)]):
            expanded_matches[f"{sid}-{match_index}"] = value
    excluded_matches = expanded_matches

    pattern = _post_search_pattern(find_text, case_sensitive, whole_word)

    target_ids = []
    for row in rows:
        if row['id'] in deselected:
            continue
        content = row['content'] or ''
        if exclude_words:
            lowered = content.lower()
            if any(word in lowered for word in exclude_words):
                continue
        if pattern.search(content):
            target_ids.append(row['id'])

    if not target_ids:
        return jsonify({"success": True, "affected_count": 0})

    try:
        affected_count = bulk_replace_post_content(
            find_text=find_text,
            replace_text=replace_text,
            post_type='standalone',
            user_id=uid,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            post_ids=target_ids,
            excluded_matches=excluded_matches,
        )
    except Exception as exc:
        app.logger.exception("Failed to replace in saved posts")
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "success": True,
        "affected_count": affected_count,
    })

@app.route('/compose/recent-prompts')
def compose_recent_prompts():
    """Return recent freeform prompts for reuse via AJAX."""
    uid = current_user()['id']
    prompts = list_recent_prompts(uid, limit=20)
    prompts_list = [
        {
            'content': p['source_content'],
            'preview': p['source_content'][:80] + ('...' if len(p['source_content']) > 80 else ''),
            'created_at': p['created_at'],
        }
        for p in prompts
    ]
    return jsonify({
        "success": True,
        "prompts": prompts_list,
    })

@app.route('/compose/clear-prompts', methods=['POST'])
def compose_clear_prompts():
    """Clear the recent prompts history."""
    uid = current_user()['id']
    try:
        count = clear_recent_prompts(uid)
        return jsonify({
            "success": True,
            "message": f"Cleared {count} prompt(s) from history",
            "count": count,
        })
    except Exception as e:
        app.logger.exception("Failed to clear prompts")
        return jsonify({"error": str(e)}), 500

@app.route('/compose/delete-prompt', methods=['POST'])
def compose_delete_prompt():
    """Delete a single prompt from history."""
    uid = current_user()['id']
    prompt_content = request.form.get('prompt', '').strip()

    if not prompt_content:
        return jsonify({"error": "Prompt content is required"}), 400

    try:
        count = delete_prompt_by_content(prompt_content, uid)
        return jsonify({
            "success": True,
            "message": f"Deleted prompt from history",
            "count": count,
        })
    except Exception as e:
        app.logger.exception("Failed to delete prompt")
        return jsonify({"error": str(e)}), 500

@app.route('/compose/delete-prompts-bulk', methods=['POST'])
def compose_delete_prompts_bulk():
    """Delete multiple prompts from history."""
    uid = current_user()['id']
    data = request.get_json() or {}
    prompts = data.get('prompts', [])

    if not prompts:
        return jsonify({"error": "No prompts provided"}), 400

    try:
        count = delete_prompts_bulk(prompts, uid)
        return jsonify({
            "success": True,
            "message": f"Deleted {len(prompts)} prompt(s) from history",
            "count": count,
        })
    except Exception as e:
        app.logger.exception("Failed to delete prompts")
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Prompt Library (curated, named reusable prompts)
# ---------------------------------------------------------------------------

def _serialize_library_prompts():
    """Return the saved library prompts as plain dicts for JSON / templates."""
    uid = current_user()['id']
    result = []
    for p in list_library_prompts(uid):
        content = p['content'] or ''
        result.append({
            'id': p['id'],
            'title': p['title'],
            'content': content,
            'preview': content[:80] + ('...' if len(content) > 80 else ''),
            'updated_at': p['updated_at'],
        })
    return result

@app.route('/compose/library')
def compose_library_list():
    """Return all saved library prompts as JSON."""
    return jsonify({"success": True, "prompts": _serialize_library_prompts()})

@app.route('/compose/library/save', methods=['POST'])
def compose_library_save():
    """Create a new named prompt in the library."""
    uid = current_user()['id']
    data = request.get_json(silent=True) or request.form
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()

    if not title:
        return jsonify({"error": "A name for the prompt is required"}), 400
    if not content:
        return jsonify({"error": "Prompt text cannot be empty"}), 400

    try:
        prompt_id = add_library_prompt(title, content, uid)
        return jsonify({
            "success": True,
            "message": "Prompt saved to library",
            "prompts": _serialize_library_prompts(),
            "id": prompt_id,
        })
    except Exception as e:
        app.logger.exception("Failed to save library prompt")
        return jsonify({"error": str(e)}), 500

@app.route('/compose/library/update', methods=['POST'])
def compose_library_update():
    """Update an existing library prompt's name and/or text."""
    uid = current_user()['id']
    data = request.get_json(silent=True) or request.form
    try:
        prompt_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid prompt id is required"}), 400
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()

    if not title:
        return jsonify({"error": "A name for the prompt is required"}), 400
    if not content:
        return jsonify({"error": "Prompt text cannot be empty"}), 400

    if not get_library_prompt(prompt_id, uid):
        return jsonify({"error": "Prompt not found"}), 404

    try:
        update_library_prompt(prompt_id, uid, title, content)
        return jsonify({
            "success": True,
            "message": "Prompt updated",
            "prompts": _serialize_library_prompts(),
        })
    except Exception as e:
        app.logger.exception("Failed to update library prompt")
        return jsonify({"error": str(e)}), 500

@app.route('/compose/library/delete', methods=['POST'])
def compose_library_delete():
    """Delete a library prompt by id."""
    uid = current_user()['id']
    data = request.get_json(silent=True) or request.form
    try:
        prompt_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid prompt id is required"}), 400

    try:
        count = delete_library_prompt(prompt_id, uid)
        if not count:
            return jsonify({"error": "Prompt not found"}), 404
        return jsonify({
            "success": True,
            "message": "Prompt deleted from library",
            "prompts": _serialize_library_prompts(),
            "count": count,
        })
    except Exception as e:
        app.logger.exception("Failed to delete library prompt")
        return jsonify({"error": str(e)}), 500

def _parse_ai_provider(form):
    """Parse the compose AI-provider selection into ``(provider, model, use_local)``.

    ``provider`` is "openai"/"anthropic" for an explicit cloud choice, or ``None``
    to use the configured default (``LLM_PROVIDER``). ``use_local`` is True for the
    local (Ollama) option. Falls back to the legacy ``use_local`` form field when
    no ``provider`` field is sent.
    """
    provider = (form.get('provider') or '').strip().lower() or None
    model = (form.get('model') or '').strip() or None
    if provider == 'cloud':          # legacy value => configured cloud default
        provider = None
    use_local = provider in ('local', 'ollama') or (
        provider is None
        and form.get('use_local', 'false').lower() in ('true', '1', 'yes')
    )
    if use_local:
        provider = None              # local is signalled via use_local, not provider
    return provider, model, use_local

def _condense_notice(meta):
    """Build a user-facing notice when long source content was condensed.

    ``meta`` is the dict returned by ``condense_document_text``; returns ``None``
    when no condensation happened (short input) so callers can skip the toast.
    """
    if not meta or not meta.get('condensed'):
        return None
    chunks = meta.get('chunks', 0)
    notice = (
        f"Condensed {meta.get('original_chars', 0):,} characters of source content into "
        f"{chunks} section summar{'y' if chunks == 1 else 'ies'} before generating, "
        "so the whole thing informs the posts."
    )
    if meta.get('chunks_dropped'):
        notice += " Only the first part of a very large source was used."
    return notice

@app.route('/compose/generate', methods=['POST'])
def compose_generate():
    """Generate social media posts using LLM based on source type."""
    uid = current_user()['id']
    source_type = request.form.get('source_type', 'freeform')
    content = request.form.get('content', '').strip()
    platforms = request.form.getlist('platforms')
    tone = request.form.get('tone', 'professional')
    posts_per_platform = request.form.get('posts_per_platform', 1, type=int)
    extra_context = request.form.get('extra_context', '').strip() or None
    topic = request.form.get('topic', '').strip() or None
    image_url = request.form.get('image_url', '').strip() or None
    ai_provider, ai_model, use_local = _parse_ai_provider(request.form)

    if source_type not in ('image', 'file') and not content:
        return jsonify({"error": "Content is required"}), 400

    if not platforms:
        platforms = ['xiaohongshu', 'douyin', 'wechat']
    

    # Copy is generated once per platform; the accounts decide how many rows
    # that copy is saved into, so picking two accounts for the same platform
    # costs no extra generation and produces one card aimed at both.
    accounts_by_platform = _requested_accounts_by_platform(set(platforms))

    posts_per_platform = max(1, min(posts_per_platform, 10))
    
    try:
        # Generate posts based on source type
        source_data = None
        notice = None
        if source_type == 'freeform':
            generated = generate_posts_from_prompt(
                prompt=content,
                platforms=platforms,
                tone=tone,
                posts_per_platform=posts_per_platform,
                extra_context=extra_context,
                use_local=use_local,
                provider=ai_provider,
                model=ai_model,
            )
        elif source_type == 'url':
                result = generate_posts_from_url(
                    url=content,
                    platforms=platforms,
                    tone=tone,
                    posts_per_platform=posts_per_platform,
                    extra_context=extra_context,
                    use_local=use_local,
                    provider=ai_provider,
                    model=ai_model,
                )
                # New structure: {"posts": {...}, "source_data": {...}}
                generated = result.get("posts", result)
                source_data = result.get("source_data")
                notice = _condense_notice(result.get("condense_meta"))

                # Auto-save URL content to url_sources
                if source_data:
                    source_id = add_url_source(
                        url=source_data.get("url", content),
                        title=source_data.get("title", ""),
                        description=source_data.get("description", ""),
                        content=source_data.get("content", ""),
                        user_id=uid,
                        og_image=source_data.get("og_image"),
                    )
                    source_data["source_id"] = source_id
                if not image_url and source_data and source_data.get("og_image"):
                    image_url = source_data["og_image"]
        elif source_type == 'text':
            # Long pasted text is condensed (map-reduce) rather than truncated to
            # the first ~5000 chars, so the whole thing informs generation.
            text_for_gen, condense_meta = condense_document_text(
                content,
                provider=ai_provider,
                model=ai_model,
                use_local=use_local,
            )
            notice = _condense_notice(condense_meta)
            generated = generate_posts_from_text(
                text=text_for_gen,
                platforms=platforms,
                tone=tone,
                topic=topic,
                posts_per_platform=posts_per_platform,
                extra_context=extra_context,
                use_local=use_local,
                provider=ai_provider,
                model=ai_model,
            )
        elif source_type == 'file':
            upload = request.files.get('file')
            if not upload or not upload.filename:
                return jsonify({"error": "Please choose a document to upload"}), 400
            if not document_extractor.is_supported(upload.filename):
                return jsonify({
                    "error": f"Unsupported file type. Supported formats: "
                             f"{document_extractor.accept_attribute()}"
                }), 400
            try:
                file_bytes = upload.read()
                extracted_text = document_extractor.extract_text(file_bytes, upload.filename)
            except document_extractor.ExtractionError as exc:
                return jsonify({"error": str(exc)}), 400

            file_display_name = secure_filename(upload.filename) or upload.filename
            # Use the document's name as the topic unless the user gave one.
            file_topic = topic or os.path.splitext(file_display_name)[0].replace('_', ' ').replace('-', ' ')
            # Long documents are condensed (map-reduce) rather than truncated, so
            # the whole document informs generation instead of just the first page.
            condensed_text, condense_meta = condense_document_text(
                extracted_text,
                provider=ai_provider,
                model=ai_model,
                use_local=use_local,
            )
            notice = _condense_notice(condense_meta)
            generated = generate_posts_from_text(
                text=condensed_text,
                platforms=platforms,
                tone=tone,
                topic=file_topic,
                posts_per_platform=posts_per_platform,
                extra_context=extra_context,
                use_local=use_local,
                provider=ai_provider,
                model=ai_model,
            )
        elif source_type == 'image':
            import base64 as b64module
            import urllib.request as _urlreq
            import mimetypes
            files = request.files.getlist('images')
            recall_urls = request.form.getlist('recall_image_urls')

            images_payload = []
            first_image_bytes = None

            if files and files[0].filename:
                # Fresh file uploads
                if len(files) > 4:
                    return jsonify({"error": "Maximum 4 images allowed"}), 400
                for f in files:
                    if not allowed_file(f.filename):
                        return jsonify({"error": f"File type not allowed: {f.filename}"}), 400
                    raw = f.read()
                    if len(raw) > 16 * 1024 * 1024:
                        return jsonify({"error": f"File too large: {f.filename} (max 16MB)"}), 400
                    if first_image_bytes is None:
                        first_image_bytes = raw
                        first_image_ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else 'jpg'
                    mime = f.content_type or 'image/jpeg'
                    images_payload.append({
                        "base64": b64module.b64encode(raw).decode(),
                        "mime_type": mime,
                    })
            elif recall_urls:
                # Recalled from recent image prompts -- fetch by URL through SSRF guard
                for url in recall_urls[:4]:
                    try:
                        raw, ctype, _final = _fetch_safely(
                            url,
                            max_bytes=16 * 1024 * 1024,
                            timeout=15,
                            allowed_content_types=("image/",),
                        )
                        mime = ctype or mimetypes.guess_type(url)[0] or 'image/jpeg'
                        if first_image_bytes is None:
                            first_image_bytes = raw
                        image_url = image_url or url
                        images_payload.append({
                            "base64": b64module.b64encode(raw).decode(),
                            "mime_type": mime,
                        })
                    except (UnsafeURLError, RuntimeError, requests.RequestException) as exc:
                        app.logger.warning("Could not fetch recalled image %s: %s", url, exc)
                    except Exception:
                        app.logger.warning("Could not fetch recalled image: %s", url)
                if not images_payload:
                    return jsonify({"error": "Could not fetch the recalled image(s)"}), 400
            else:
                return jsonify({"error": "At least one image is required"}), 400

            generated = generate_posts_from_images(
                images=images_payload,
                prompt=content or None,
                platforms=platforms,
                tone=tone,
                posts_per_platform=posts_per_platform,
                extra_context=extra_context,
                use_local=use_local,
                provider=ai_provider,
                model=ai_model,
            )

            if first_image_bytes and not image_url:
                try:
                    from io import BytesIO
                    from PIL import Image
                    img = Image.open(BytesIO(first_image_bytes))
                    img.verify()
                    if cloudinary_ready():
                        result = cloudinary.uploader.upload(
                            first_image_bytes,
                            folder="insights",
                            resource_type="image",
                        )
                        image_url = result['secure_url']
                    else:
                        unique_fn = f"{uuid.uuid4().hex}.{first_image_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_fn)
                        with open(filepath, 'wb') as fp:
                            fp.write(first_image_bytes)
                        image_url = f"{request.host_url}static/uploads/{unique_fn}"
                except Exception:
                    app.logger.debug("Could not auto-upload first image for attachment")
        else:
            return jsonify({"error": f"Unknown source type: {source_type}"}), 400
        
        # Save generated posts to database
        if source_type == 'file':
            source_label = f"[File: {file_display_name}]"
        elif content:
            source_label = content[:1000]
        else:
            source_label = f"[{len(request.files.getlist('images'))} image(s)]"
        requested_platforms = {p.lower() for p in platforms}
        saved_posts = {}
        for platform, post_data in generated.items():
            if platform == 'raw':
                continue
            
            # Normalize platform name (LLMs sometimes capitalise names)
            norm_platform = platform.lower().strip()

            # Drop platforms the user didn't ask for (local models hallucinate extras)
            if norm_platform not in requested_platforms:
                continue

            posts_list = post_data if isinstance(post_data, list) else [post_data]
            if norm_platform not in saved_posts:
                saved_posts[norm_platform] = []
            
            for post_content in posts_list:
                # One row per selected account on this platform, all holding the
                # same copy, so the generated post arrives as one card aimed at
                # every account rather than as separate posts per account.
                ids = _save_post_for_accounts(
                    accounts_by_platform.get(norm_platform),
                    user_id=uid,
                    source_type=source_type,
                    source_content=source_label,
                    platform=norm_platform,
                    content=post_content,
                    image_url=image_url,
                )
                saved_posts[norm_platform].append({
                    'id': ids[0],
                    'post_ids': ids,
                    'content': post_content,
                    'image_url': image_url,
                })
                if not image_url:
                    _maybe_attach_link_image(ids[0], post_content, user_id=uid,
                                             sibling_ids=ids[1:])

        response_data = {
            "success": True,
            "generated": generated,
            "saved_posts": saved_posts,
        }
        if source_data:
            response_data["source_data"] = source_data
        if notice:
            response_data["notice"] = notice

        return jsonify(response_data)
        
    except Exception as e:
        app.logger.exception("Failed to generate posts")
        return jsonify({"error": str(e)}), 500

@app.route('/compose/ollama-status', methods=['GET'])
def compose_ollama_status():
    """Return Ollama availability and installed vision models."""
    return jsonify(check_ollama_status())

@app.route('/compose/post/<int:post_id>', methods=['GET'])
def compose_get_post(post_id: int):
    """Get a standalone post by ID."""
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    return jsonify(dict(post))

@app.route('/compose/post/create', methods=['POST'])
def compose_create_post():
    """Create a standalone post manually (no AI generation).

    Takes one ``platform`` or several (repeated or comma-joined ``platforms``),
    or ``targets`` when a platform's account matters: the composer writes one
    row per ticked target, which is exactly the set of rows the resulting card
    is a view over. Ticking two accounts for a platform writes two rows
    and posts the same copy to both.

    An optional ``video`` file or ``video_url`` is attached to every one of
    those rows; ``video_warnings`` in the reply says what each platform will
    make of it.
    """
    uid = current_user()['id']
    targets, target_errors = _requested_targets('targets')
    if not targets:
        # No explicit targets: every ticked platform goes to its default account.
        platforms = _requested_platforms('platforms') or _requested_platforms('platform')
        invalid = [p for p in platforms if p not in COMPOSE_PLATFORMS]
        if invalid:
            return jsonify({
                "error": f"Invalid platform. Must be one of: {', '.join(COMPOSE_PLATFORMS)}"
            }), 400
        targets = [
            {'platform': p, 'account_id': resolve_account_id(p)} for p in platforms
        ]
    content = request.form.get('content', '').strip()
    image_url = request.form.get('image_url', '').strip() or None

    if not targets:
        return jsonify({"error": target_errors[0] if target_errors
                        else "Platform is required"}), 400
    if not content:
        return jsonify({"error": "Content is required"}), 400

    # Last, so a request that was going to be refused anyway never uploads a
    # video; and before any row is written, so a video that cannot be used
    # leaves no half-made post behind.
    video_url, video_report, error = _requested_video()
    if error:
        return error

    # One call per platform: _save_post_for_accounts writes the same copy once
    # per ticked account on it, every row owned by the composer.
    post_ids = []
    for platform in dict.fromkeys(target['platform'] for target in targets):
        platform_targets = [t for t in targets if t['platform'] == platform]
        post_ids.extend(_save_post_for_accounts(
            [t['account_id'] for t in platform_targets],
            user_id=uid,
            source_type='manual',
            source_content='Manual post',
            platform=platform,
            content=content,
            image_url=image_url,
            video_url=video_url,
        ))

    if not image_url and not video_url:
        # One fetch for the card, applied to every target's row, since rows with
        # different images would stop being one card. A card with a video posts
        # the video, so there is no image worth fetching for it.
        _maybe_attach_link_image(post_ids[0], content, user_id=uid,
                                 sibling_ids=post_ids[1:])

    return jsonify({
        "success": True,
        "post": {
            "id": post_ids[0],
            "platform": targets[0]['platform'],
            "content": content,
            "image_url": image_url,
            "video_url": video_url,
        },
        "post_ids": post_ids,
        "platforms": [t['platform'] for t in targets],
        "targets": targets,
        "warnings": target_errors,
        "video_warnings": video_report.get('warnings', []),
    })

# ---------------------------------------------------------------------------
# Compose card helpers
#
# A card is one piece of copy plus the platforms it goes to. The database still
# stores a row per (platform, content), so most per-card actions have to touch
# every row behind the card: editing the text or the image of one platform's row
# alone would split the card in two, since (content, image) is what groups the
# rows in the first place.
# ---------------------------------------------------------------------------

def _requested_platforms(field):
    """Read a repeated or comma-joined platform field, lowercased and deduped."""
    values = request.form.getlist(field)
    if not values:
        payload = request.get_json(silent=True) or {}
        raw = payload.get(field)
        values = raw if isinstance(raw, list) else ([raw] if raw else [])

    platforms = []
    for value in values:
        for part in str(value).split(','):
            part = part.strip().lower()
            if part and part not in platforms:
                platforms.append(part)
    return platforms

def _save_post_for_accounts(account_ids, user_id: int, **kwargs):
    """Write the same copy once per account, returning the row ids.

    An empty or missing account list means the platform's default account, which
    is what every caller did before accounts existed. ``user_id`` is the owner
    of every row written; the background link-image worker has no request
    context, so it must always be passed explicitly.
    """
    if not account_ids:
        return [add_standalone_post(user_id=user_id, **kwargs)]
    return [add_standalone_post(account_id=account_id, user_id=user_id, **kwargs)
            for account_id in account_ids]

def _requested_accounts_by_platform(platforms):
    """Which accounts each requested platform should be written for.

    Reads the same ``targets`` field the composer sends elsewhere. A platform
    named without any account falls back to its default account, so ticking
    "xiaohongshu" and ticking "xiaohongshu: Work, xiaohongshu: Studio" both work
    and mean what they say.
    """
    targets, _errors = _requested_targets('targets')
    grouped = {}
    for target in targets:
        if target['platform'] in platforms:
            grouped.setdefault(target['platform'], []).append(target['account_id'])
    return grouped

def _requested_targets(field='targets'):
    """Read the (platform, account) pairs a card is aimed at.

    A target is written ``platform`` or ``platform:account_id``, so the same
    field carries "xiaohongshu" and "xiaohongshu: the second one" without the caller
    having to send two parallel lists. A bare platform means whichever account
    is that platform's default, decided when the row is written rather than
    when it publishes, so the saved post records a concrete target.

    Returns ``(targets, errors)``: a target that names an unknown account, or an
    account belonging to another platform, is reported rather than silently
    dropped, because quietly posting to the wrong account is the failure this
    whole feature exists to avoid.
    """
    targets, errors, seen = [], [], set()
    for raw in _requested_platforms(field):
        platform, _, account_part = raw.partition(':')
        platform = platform.strip()
        if platform not in COMPOSE_PLATFORMS:
            errors.append(f"Invalid platform: {platform or raw}")
            continue

        account_id = None
        account_part = account_part.strip()
        if account_part:
            if not account_part.isdigit():
                errors.append(f"Invalid account for {platform}")
                continue
            account_id = resolve_account_id(platform, int(account_part))
            if not account_id:
                errors.append(f"That {domestic_publisher.display_name(platform)} account is not connected")
                continue
        else:
            account_id = resolve_account_id(platform)

        key = (platform, account_id)
        if key in seen:
            continue
        seen.add(key)
        targets.append({'platform': platform, 'account_id': account_id})
    return targets, errors

def _requested_post_ids(default_id, field='post_ids'):
    """The card's rows as sent by the page, always including ``default_id``.

    Accepts repeated or comma-joined ``post_ids`` in a form body or JSON. When
    it is absent (older callers, direct API use) the action stays a single-post
    action, exactly as it behaved before cards existed.

    ``field`` reads a different list under the same rules. A fan-out uses it to
    read ``target_ids``, the subset of the card being published this time, where
    ``default_id`` is deliberately not forced in: narrowing to "just these two"
    has to be able to exclude the row the request was addressed to.
    """
    values = request.form.getlist(field)
    if not values:
        payload = request.get_json(silent=True) or {}
        raw = payload.get(field)
        values = raw if isinstance(raw, list) else ([raw] if raw else [])

    ids = []
    for value in values:
        for part in str(value).split(','):
            part = part.strip()
            if part.isdigit() and int(part) not in ids:
                ids.append(int(part))
    if field == 'post_ids' and default_id not in ids:
        ids.append(default_id)
    return ids

def _card_rows(post, post_ids, user_id: int):
    """The rows of ``post_ids`` that really are ``post``'s card, ``post`` first.

    Ids from the browser can be stale — a twin may have been edited, deleted or
    regrouped in another tab — so a row only counts while it still holds the
    same copy. That keeps a card-wide edit from rewriting an unrelated post.
    Rows are read through the owner's scope, so a foreign id can never join.
    """
    rows = [post]
    for pid in post_ids:
        if pid == post['id']:
            continue
        row = get_standalone_post(pid, user_id)
        if row and (row['content'] or '') == (post['content'] or ''):
            rows.append(row)
    return rows

def _apply_card_image(post, image_url, user_id: int):
    """Set an image on every row of ``post``'s card; returns the ids written.

    Every path that attaches an image (upload, URL, library, stock, og:image,
    source refresh) goes through here so the card's rows keep matching images —
    they would otherwise drift apart into separate cards.
    """
    rows = _card_rows(post, _requested_post_ids(post['id']), user_id)
    for row in rows:
        update_standalone_post_image(row['id'], image_url, user_id)
    return [row['id'] for row in rows]

def _apply_card_video(post, video_url, user_id: int):
    """Set (or with None, clear) the video on every row of ``post``'s card.

    The video is part of what groups a card's rows, just as the image is, so
    attaching it to one platform's row alone would split the card in two.
    Returns the ids written.
    """
    rows = _card_rows(post, _requested_post_ids(post['id']), user_id)
    for row in rows:
        update_standalone_post_video(row['id'], video_url, user_id)
    return [row['id'] for row in rows]

def _post_card_html(post_ids, user_id: int, display_index=None):
    """Render one Compose card for ``post_ids``, or '' if none of them survive.

    Handing the freshly rendered card back to the page is what keeps a platform
    toggle simple: ticking a platform brings a whole set of platform-specific
    controls with it, and re-rendering beats patching them in by hand.
    """
    rows = [row for row in (get_standalone_post(pid, user_id) for pid in post_ids) if row]
    if not rows:
        return ''

    group = {
        # Keyed by (platform, account) like the grouper does, so a card with two
        # accounts on one platform re-renders with both chips rather than one.
        'platforms': {_row_target_key(row): row for row in rows},
        'head': rows[0],
        'display_index': display_index,
    }
    ids = [row['id'] for row in rows]
    card = _enrich_post_group(
        group,
        get_pending_schedules_for_standalone_posts(ids, user_id),
        get_posted_info_for_standalone_posts(ids, user_id),
        {brief['id']: brief['name'] for brief in list_content_briefs(user_id)},
    )
    return render_template('partials/post_items.html', groups=[card])

@app.route('/compose/post/<int:post_id>/card', methods=['POST'])
def compose_render_post_card(post_id: int):
    """Re-render one Compose card.

    Used when a card loses some of its platforms but not all of them — a bulk
    delete run under a platform filter, say — where patching the leftover DOM
    is more error-prone than asking for the card again.
    """
    uid = current_user()['id']
    if not get_standalone_post(post_id, uid):
        return jsonify({"error": "Post not found"}), 404

    return jsonify({
        "success": True,
        "html": _post_card_html(
            _requested_post_ids(post_id), uid,
            request.form.get('display_index', type=int)
        ),
    })

def _find_target_row(rows, platform, account_id):
    """The row in ``rows`` that already is this (platform, account) target.

    A row saved before it carried an account is drawn by its card as the
    platform's default account (see _enrich_post_group), so it has to match here
    the same way. Otherwise the chip would read as ticked while ticking it again
    wrote a duplicate, and ticking it off would leave the row stranded.
    """
    for row in rows:
        if row['platform'] != platform:
            continue
        row_account = (row['account_id'] if 'account_id' in row.keys() else None) \
            or _default_account_for(platform)
        if row_account == account_id:
            return row
    return None

def _copy_post_to_target(post, platform, account_id, user_id: int, siblings=()):
    """Save ``post``'s copy, image, video and brief as a new row for one target.

    This is what "also post it there" means in the database: the row per
    (platform, copy) is the only record that a card goes to that platform.
    Returns the new row's id.
    """
    return add_standalone_post(
        source_type=post['source_type'],
        source_content=post['source_content'],
        platform=platform,
        content=post['content'],
        user_id=user_id,
        image_url=post['image_url'],
        video_url=_row_video(post) or None,
        brief_id=post['brief_id'] if 'brief_id' in post.keys() else None,
        brief_run_id=post['brief_run_id'] if 'brief_run_id' in post.keys() else None,
        account_id=account_id,
    )

@app.route('/compose/post/<int:post_id>/platform', methods=['POST'])
def compose_toggle_post_platform(post_id: int):
    """Tick or untick one of a card's targets.

    A target is a platform plus the account it publishes as. ``action=add``
    copies the card's copy and image into a new saved post for that target, so
    ticking a platform is what creates the post for that platform, and ticking
    a second account on it is what creates the second post.
    ``action=remove`` deletes that target's row, which is the only thing that
    ever recorded "this post also goes there".

    Removing is guarded: a row that is queued or already published should not go
    on a stray click, so those answer 409 until the caller repeats the request
    with ``force=1``. Forcing a queued row also takes it out of the queue, since
    leaving a schedule pointing at a deleted post would publish nothing.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    platform = (request.form.get('platform') or '').strip().lower()
    if platform not in COMPOSE_PLATFORMS:
        return jsonify({
            "error": f"Invalid platform. Must be one of: {', '.join(COMPOSE_PLATFORMS)}"
        }), 400

    # Naming no account means the platform's default one, which is how the
    # single-account chips have always behaved.
    raw_account = (request.form.get('account_id') or '').strip()
    if raw_account:
        account_id = resolve_account_id(platform, int(raw_account)) \
            if raw_account.isdigit() else None
        if not account_id:
            return jsonify({
                "error": f"That {domestic_publisher.display_name(platform)} account is not connected"
            }), 400
    else:
        account_id = resolve_account_id(platform)

    action = (request.form.get('action') or 'add').strip().lower()
    if action not in ('add', 'remove'):
        return jsonify({"error": "action must be 'add' or 'remove'"}), 400

    rows = _card_rows(post, _requested_post_ids(post_id), uid)
    display_index = request.form.get('display_index', type=int)
    existing = _find_target_row(rows, platform, account_id)

    target_name = domestic_publisher.display_name(platform)
    if account_id:
        account = get_social_account(account_id)
        if account:
            target_name = _account_label(account, platform)

    if action == 'add':
        if existing:
            return jsonify({"error": f"This post already goes to {target_name}"}), 400

        new_id = _copy_post_to_target(post, platform, account_id, uid, siblings=rows)
        ids = [row['id'] for row in rows] + [new_id]
        return jsonify({
            "success": True,
            "action": "add",
            "platform": platform,
            "account_id": account_id,
            "account_label": target_name,
            "post_id": new_id,
            "post_ids": ids,
            "html": _post_card_html(ids, uid, display_index),
        })

    if existing is None:
        return jsonify({"error": f"This post does not go to {target_name}"}), 400

    target_id = existing['id']
    delete_standalone_post(target_id, uid)
    ids = [row['id'] for row in rows if row['id'] != target_id]
    return jsonify({
        "success": True,
        "action": "remove",
        "platform": platform,
        "account_id": account_id,
        "account_label": target_name,
        "post_id": target_id,
        "post_ids": ids,
        "html": _post_card_html(ids, uid, display_index) if ids else '',
    })

def _selected_post_groups(payload, user_id: int):
    """Resolve a Compose bulk-action selection to the whole cards it names.

    The card-level sibling of _selected_standalone_posts, for actions that treat
    a card as one unit. Two payload shapes:
      ``{"post_ids": [...]}``  any row of each ticked card. A list filtered to
                               one platform only knows that platform's row id, so
                               the card is found from whichever row it is given.
      ``{"filters": {...}}``   "select all across every page", re-derived from the
                               filter bar exactly as the list itself is.

    Cards are drawn from the owner's rows only, so an id naming another user's
    post finds no card of its own.

    Returns ``(groups, error)`` where ``error`` is a ready-to-return response.
    """
    filters = payload.get('filters')
    if filters is not None:
        if not isinstance(filters, dict):
            return None, (jsonify({"error": "filters must be an object"}), 400)
        groups, _ = _filtered_post_groups(filters)
        return groups, None

    raw_ids = payload.get('post_ids')
    if not raw_ids:
        return None, (jsonify({"error": "No posts selected"}), 400)
    if not isinstance(raw_ids, list):
        return None, (jsonify({"error": "post_ids must be a list"}), 400)
    try:
        wanted = {int(pid) for pid in raw_ids}
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Invalid post IDs"}), 400)

    groups = _group_standalone_posts(list_standalone_posts(user_id))
    for position, group in enumerate(groups, start=1):
        group['display_index'] = position
    selected = [
        group for group in groups
        if any(row['id'] in wanted for row in group['platforms'].values())
    ]
    if not selected:
        return None, (jsonify({"error": "The selected posts no longer exist"}), 400)
    return selected, None

# The most skipped targets one bulk request lists by name. The count is always
# exact; the list only bounds the size of the reply.
BULK_SKIP_LIST_LIMIT = 50

def _target_display_name(platform, account_id):
    """What to call a (platform, account) target in a message."""
    return _row_account_label({'platform': platform, 'account_id': account_id}) \
        or domestic_publisher.display_name(platform)

SCHEDULABLE_PLATFORMS: tuple = ()

def platform_name(platform: str) -> str:
    """The platform's name as a person writes it."""
    return domestic_publisher.display_name(platform)


def _queue_row(row, already, card_rows=None, no_slots=None):
    """Queue one saved post row into its platform's next free slot.

    Kept for the bulk-add ``queue`` option: the Western posting APIs are gone,
    so no domestic platform is schedulable from this server yet, and every row
    is refused with the way out instead of being silently dropped.
    """
    platform = row['platform']
    target_name = _row_account_label(row) or platform_name(platform)

    def skip(error):
        return None, {"post_id": row['id'], "platform": platform,
                      "target": target_name, "error": error}

    if platform in DRAFT_ONLY_PLATFORMS:
        return skip(f"{platform} is draft-only: copy the post and publish it manually.")
    if platform not in SCHEDULABLE_PLATFORMS:
        return skip(f"{target_name} does not support scheduling yet")
    if already.get(row['id']):
        return skip(f"Already queued for {target_name}")
    return skip("No available time slots")


def _bulk_platform_request():
    """Read the body the bulk add and remove endpoints share.

    A JSON object naming the cards (``post_ids`` or ``filters``) and the targets
    to change. Returns ``(payload, targets, groups, error)``, where ``error`` is
    a ready-to-return response.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, None, None, (jsonify({"error": "Send a JSON body"}), 400)

    targets, errors = _requested_targets('targets')
    if errors:
        return None, None, None, (jsonify({"error": errors[0]}), 400)
    if not targets:
        return None, None, None, (jsonify({"error": "Choose at least one platform"}), 400)

    uid = current_user()['id']
    groups, error = _selected_post_groups(payload, uid)
    if error:
        return None, None, None, error
    return payload, targets, groups, None

@app.route('/compose/posts/bulk-add-platform', methods=['POST'])
def compose_bulk_add_platform():
    """Give every selected card the chosen posting targets, optionally queueing them.

    Body: ``{"post_ids": [...] | "filters": {...}, "targets": ["xiaohongshu",
    "xiaohongshu:<account id>", ...], "queue": bool, "render": bool}``.

    For each card and target this does what ticking that chip does one card at a
    time: a card that already goes there is left alone, otherwise its copy, image
    and brief are copied into a new row for that target. With ``queue`` every
    target then takes its own next free slot, existing rows included, so the end
    state is "these cards are queued for these targets". Rows that are used,
    already queued or already published are never queued again.

    Nothing here is all-or-nothing: each row is its own write, a target that
    cannot be queued is reported rather than aborting the batch, and running the
    same request again is a no-op, which is what makes a half-finished run safe
    to repeat.

    ``render`` adds a freshly drawn card for every card that changed, so the page
    can swap them in without a reload.
    """
    payload, targets, groups, error = _bulk_platform_request()
    if error:
        return error

    uid = current_user()['id']
    queue = _search_flag(payload.get('queue'))
    render = _search_flag(payload.get('render'))

    row_ids = [row['id'] for group in groups for row in group['platforms'].values()]
    pending = get_pending_schedules_for_standalone_posts(row_ids, uid) if queue else {}
    posted = get_posted_info_for_standalone_posts(row_ids, uid) if queue else {}

    by_target = [
        {'platform': t['platform'], 'account_id': t['account_id'],
         'label': _target_display_name(t['platform'], t['account_id']),
         'added': 0, 'already': 0, 'queued': 0, 'already_queued': 0, 'skipped': 0}
        for t in targets
    ]
    skipped = []
    skipped_count = 0
    no_slots = set()
    results = []

    for group in groups:
        original_ids = [row['id'] for row in group['platforms'].values()]
        card_rows = _ordered_group_rows(group)
        head = group['head']
        changed = False
        landed = []

        for target, stats in zip(targets, by_target):
            row = _find_target_row(card_rows, target['platform'], target['account_id'])
            is_new = row is None
            if is_new:
                row = get_standalone_post(
                    _copy_post_to_target(head, target['platform'], target['account_id'],
                                         uid, siblings=card_rows), uid)
                card_rows.append(row)
                stats['added'] += 1
                changed = True
            else:
                stats['already'] += 1
            landed.append((stats, row, is_new))

        if queue:
            for stats, row, is_new in landed:
                reason = None
                if not is_new:
                    if pending.get(row['id']):
                        stats['already_queued'] += 1
                        continue
                    if posted.get(row['id']):
                        reason = "Already published"
                    elif row['used']:
                        reason = "Marked as used"
                if reason is None and row['platform'] in DRAFT_ONLY_PLATFORMS:
                    # Draft-only platforms have no posting API: a queued entry
                    # could never go out, so queueing is refused with the way
                    # out, exactly as the single-card queue route does.
                    reason = f"{row['platform']} is draft-only: copy the post and publish it manually."
                if reason is None and stats['account_id'] is None:
                    reason = f"Connect {platform_name(row['platform'])} before queueing"
                if reason is None:
                    entry, skip = _queue_row(row, pending, card_rows, no_slots)
                    if entry:
                        stats['queued'] += 1
                        changed = True
                        continue
                    reason = skip['error']

                stats['skipped'] += 1
                skipped_count += 1
                if len(skipped) < BULK_SKIP_LIST_LIMIT:
                    skipped.append({'post_id': row['id'], 'platform': row['platform'],
                                    'target': stats['label'], 'reason': reason})

        if render and changed:
            ids = [row['id'] for row in card_rows]
            results.append({
                'anchor_ids': original_ids,
                'post_ids': ids,
                'html': _post_card_html(ids, uid, group.get('display_index')),
            })

    reply = {
        "success": True,
        "cards": len(groups),
        "added": sum(t['added'] for t in by_target),
        "already": sum(t['already'] for t in by_target),
        "queued": sum(t['queued'] for t in by_target),
        "already_queued": sum(t['already_queued'] for t in by_target),
        "skipped": skipped,
        "skipped_count": skipped_count,
        "by_target": by_target,
    }
    if render:
        reply["results"] = results
    return jsonify(reply)

@app.route('/compose/posts/bulk-remove-platform', methods=['POST'])
def compose_bulk_remove_platform():
    """Take the chosen posting targets off every selected card.

    Body: ``{"post_ids": [...] | "filters": {...}, "targets": ["douyin",
    "xiaohongshu:<account id>", ...], "force": bool, "render": bool}``.

    The bulk form of unticking a chip: each matching row is deleted, and a card
    that never went to a target is left alone and counted. Three things are kept
    rather than removed, and reported per row with a ``code``:

      ``queued``     the row is in the queue. ``force`` takes it out of the queue
                     and removes it, as confirming the single-chip prompt does.
      ``published``  the row has been posted. ``force`` removes it anyway; the
                     posting history stays, and it is not unpublished.
      ``last``       removing would leave the card with no platform at all, which
                     deletes the post and its copy. ``force`` does not override
                     this: Delete Selected is the action for that.

    A card is judged as a whole, so asking for every one of its platforms keeps
    all of them rather than removing whichever came first. As with adding, each
    card is its own write and running the same request again is a no-op.

    ``render`` adds a freshly drawn card for every card that changed.
    """
    payload, targets, groups, error = _bulk_platform_request()
    if error:
        return error

    uid = current_user()['id']
    force = _search_flag(payload.get('force'))
    render = _search_flag(payload.get('render'))

    row_ids = [row['id'] for group in groups for row in group['platforms'].values()]
    pending = get_pending_schedule_ids_for_standalone_posts(row_ids, uid)
    posted = get_posted_info_for_standalone_posts(row_ids, uid)

    by_target = [
        {'platform': t['platform'], 'account_id': t['account_id'],
         'label': _target_display_name(t['platform'], t['account_id']),
         'removed': 0, 'absent': 0, 'skipped': 0}
        for t in targets
    ]
    skipped = []
    skipped_count = 0
    unqueued = 0
    results = []

    def keep(stats, row, code, reason):
        nonlocal skipped_count
        stats['skipped'] += 1
        skipped_count += 1
        if len(skipped) < BULK_SKIP_LIST_LIMIT:
            skipped.append({'post_id': row['id'], 'platform': row['platform'],
                            'target': stats['label'], 'code': code, 'reason': reason})

    for group in groups:
        original_ids = [row['id'] for row in group['platforms'].values()]
        card_rows = _ordered_group_rows(group)

        going = []
        for target, stats in zip(targets, by_target):
            row = _find_target_row(card_rows, target['platform'], target['account_id'])
            if row is None:
                stats['absent'] += 1
                continue
            # Published wins over queued: a row can be both if it was posted and
            # then queued again, and the published part is the one that cannot
            # be undone.
            if not force and posted.get(row['id']):
                keep(stats, row, 'published', 'Already published')
            elif not force and pending.get(row['id']):
                keep(stats, row, 'queued', 'Queued')
            else:
                going.append((stats, row))

        if not going:
            continue
        if len(going) == len(card_rows):
            for stats, row in going:
                keep(stats, row, 'last', "The post's only platform")
            continue

        going_ids = [row['id'] for _stats, row in going]
        schedule_ids = [sid for rid in going_ids for sid in pending.get(rid, [])]
        # The queue entries go first: deleting a post under a pending entry
        # leaves an entry that would publish nothing.
        unqueued += delete_scheduled_posts_bulk(schedule_ids)
        delete_standalone_posts_bulk(going_ids, uid)
        for stats, _row in going:
            stats['removed'] += 1

        if render:
            left = [row['id'] for row in card_rows if row['id'] not in going_ids]
            results.append({
                'anchor_ids': original_ids,
                'post_ids': left,
                'html': _post_card_html(left, uid, group.get('display_index')),
            })

    reply = {
        "success": True,
        "cards": len(groups),
        "removed": sum(t['removed'] for t in by_target),
        "absent": sum(t['absent'] for t in by_target),
        "unqueued": unqueued,
        "skipped": skipped,
        "skipped_count": skipped_count,
        "by_target": by_target,
    }
    if render:
        reply["results"] = results
    return jsonify(reply)

@app.route('/compose/import', methods=['POST'])
def compose_import_file():
    """Import posts from a CSV or XLSX file into the Command Center."""
    uid = current_user()['id']
    import csv as csv_mod

    # Chinese names import as their platform id too: a sheet written in
    # Chinese should not have to know the internal identifiers.
    IMPORT_PLATFORMS = {'xiaohongshu', 'douyin', 'wechat', 'zhihu', 'toutiao',
                        '小红书', '抖音', '公众号', '知乎', '头条'}
    TRUTHY = {'true', '1', 'yes', 'y', 't'}

    def _is_truthy(val) -> bool:
        return str(val or '').strip().lower() in TRUTHY

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    filename = file.filename.lower()
    if not (filename.endswith('.csv') or filename.endswith('.xlsx') or filename.endswith('.xls')):
        return jsonify({"error": "Unsupported file type. Please upload a .csv or .xlsx file."}), 400

    rows = []
    try:
        if filename.endswith('.csv'):
            stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
            reader = csv_mod.DictReader(stream)
            header_map = {k.strip().lower(): k for k in (reader.fieldnames or [])}
            platform_col = header_map.get('platform')
            copy_col = header_map.get('copy')
            video_col = header_map.get('video')
            repost_col = header_map.get('repost')
            if not platform_col or not copy_col:
                return jsonify({"error": "CSV must have 'Platform' and 'copy' columns. "
                                f"Found: {', '.join(reader.fieldnames or [])}"}), 400
            for row in reader:
                rows.append({
                    'platform': (row.get(platform_col) or '').strip(),
                    'copy': (row.get(copy_col) or '').strip(),
                    'video': (row.get(video_col) or '').strip() if video_col else '',
                    'repost': (row.get(repost_col) or '').strip() if repost_col else '',
                })
        else:
            import openpyxl
            wb = openpyxl.load_workbook(file.stream, read_only=True, data_only=True)
            ws = wb.active
            raw_rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if not raw_rows:
                return jsonify({"error": "The spreadsheet is empty."}), 400
            headers = [str(h).strip().lower() if h else '' for h in raw_rows[0]]
            try:
                platform_idx = headers.index('platform')
            except ValueError:
                return jsonify({"error": "Spreadsheet must have a 'Platform' column. "
                                f"Found: {', '.join(str(h) for h in raw_rows[0])}"}), 400
            try:
                copy_idx = headers.index('copy')
            except ValueError:
                return jsonify({"error": "Spreadsheet must have a 'copy' column. "
                                f"Found: {', '.join(str(h) for h in raw_rows[0])}"}), 400
            video_idx = headers.index('video') if 'video' in headers else None
            repost_idx = headers.index('repost') if 'repost' in headers else None
            for row in raw_rows[1:]:
                rows.append({
                    'platform': str(row[platform_idx]).strip() if row[platform_idx] else '',
                    'copy': str(row[copy_idx]).strip() if row[copy_idx] else '',
                    'video': str(row[video_idx]).strip() if video_idx is not None and row[video_idx] else '',
                    'repost': str(row[repost_idx]).strip() if repost_idx is not None and row[repost_idx] is not None else '',
                })
    except Exception as e:
        return jsonify({"error": f"Failed to parse file: {str(e)}"}), 400

    existing = get_existing_standalone_content(uid)

    imported = 0
    skipped = 0
    skipped_details = []
    by_platform = {}
    original_filename = file.filename

    for i, row in enumerate(rows, start=2):
        raw_platform = row['platform']
        content = row['copy']

        content_preview = (content[:80] + '...') if len(content) > 80 else content

        if not content:
            skipped += 1
            skipped_details.append({
                "row": i,
                "reason": "empty_content",
                "message": "Content is empty",
                "fix": "Add post text to the 'copy' column in this row.",
                "platform": raw_platform or "(blank)",
                "preview": "",
            })
            continue

        tokens = [t.strip() for t in re.split(r'[,;|]', raw_platform) if t.strip()]
        seen = set()
        platforms = []
        for token in tokens:
            normalized = token.lower()
            normalized = {'小红书': 'xiaohongshu', '抖音': 'douyin', '知乎': 'zhihu',
                          '头条': 'toutiao', '公众号': 'wechat', '微信': 'wechat'}.get(normalized, normalized)
            if normalized in seen:
                continue
            seen.add(normalized)
            platforms.append((normalized, token))

        if not platforms:
            skipped += 1
            skipped_details.append({
                "row": i,
                "reason": "unsupported_platform",
                "message": f"Unsupported platform '{raw_platform}'",
                "fix": "Change the Platform value to one of: xiaohongshu, douyin, wechat, zhihu, toutiao (or 小红书, 抖音, 公众号, 知乎, 头条). Use commas to list multiple (e.g. '小红书,抖音').",
                "platform": raw_platform or "(blank)",
                "preview": content_preview,
            })
            continue

        source_content = original_filename
        if row.get('video'):
            source_content = f"{original_filename} | {row['video']}"

        is_repost = _is_truthy(row.get('repost'))

        for platform, original_token in platforms:
            if platform not in IMPORT_PLATFORMS:
                skipped += 1
                skipped_details.append({
                    "row": i,
                    "reason": "unsupported_platform",
                    "message": f"Unsupported platform '{original_token}'",
                    "fix": "将 Platform 列改为：小红书、抖音、公众号（可用逗号分隔写多个，如 '小红书,抖音'）。",
                    "platform": original_token or "(blank)",
                    "preview": content_preview,
                })
                continue

            existing_id = existing.get((platform, content))
            if existing_id is not None and not is_repost:
                skipped += 1
                skipped_details.append({
                    "row": i,
                    "reason": "duplicate",
                    "message": f"Duplicate — identical {platform} post already exists (Post #{existing_id})",
                    "fix": "This post is already in the Command Center. Edit the copy in the file to make it unique, delete the existing post first, or set the 'repost' column to true to allow a duplicate.",
                    "platform": platform,
                    "preview": content_preview,
                    "duplicate_post_id": existing_id,
                })
                continue

            new_id = add_standalone_post(
                source_type='import',
                source_content=source_content,
                platform=platform,
                content=content,
                user_id=uid,
                repost=is_repost,
            )
            if not is_repost:
                existing[(platform, content)] = new_id
            imported += 1
            by_platform[platform] = by_platform.get(platform, 0) + 1
            _maybe_attach_link_image(new_id, content, user_id=uid)

    return jsonify({
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "skipped_details": skipped_details,
        "by_platform": by_platform,
    })

@app.route('/compose/post/<int:post_id>/edit', methods=['POST'])
def compose_edit_post(post_id: int):
    """Edit a standalone post's content.

    Applies to every row of the card (see _requested_post_ids): the copy is the
    card, so editing it on one platform only would silently split the card.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    new_content = request.form.get('content', '').strip()
    if not new_content:
        return jsonify({"error": "Content is required"}), 400

    rows = _card_rows(post, _requested_post_ids(post_id), uid)
    for row in rows:
        update_standalone_post(row['id'], uid, new_content)

    # If the edit introduced a URL and the post still has no image, kick off a
    # background fetch of the og:image. Skip when the body did not change or
    # already contained the same URLs.
    post_keys = post.keys() if hasattr(post, 'keys') else []
    existing_image = post['image_url'] if 'image_url' in post_keys else None
    existing_content = post['content'] if 'content' in post_keys else ''
    if not existing_image:
        old_urls = set(extract_urls_from_text(existing_content or ''))
        new_urls = extract_urls_from_text(new_content)
        if any(u not in old_urls for u in new_urls):
            _maybe_attach_link_image(
                post_id, new_content, user_id=uid,
                sibling_ids=[r['id'] for r in rows if r['id'] != post_id]
            )

    return jsonify({
        "success": True,
        "content": new_content,
        "updated_ids": [row['id'] for row in rows],
    })

@app.route('/compose/post/<int:post_id>/image', methods=['POST'])
def compose_update_post_image(post_id: int):
    """Update a standalone post's image URL.

    Applies to every row of the card: the image is part of what groups them.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    image_url = request.form.get('image_url', '').strip() or None

    return jsonify({
        "success": True,
        "image_url": image_url,
        "updated_ids": _apply_card_image(post, image_url, uid),
    })

def _requested_video():
    """The video a request carries, checked and ready to attach.

    Reads an uploaded ``video`` file (put on Cloudinary) or a ``video_url``; an
    empty ``video_url`` and no file means no video. Returns
    ``(video_url, report, error_response)``: exactly one of ``error_response``
    (a ready-to-return ``(json, status)``) or the first two is meaningful.
    ``report`` is what the composer needs to judge the video: its size, its
    duration when known, and the per-platform ``warnings``.
    """
    uploaded = request.files.get('video')
    if uploaded is not None and uploaded.filename:
        payload, error, status = _store_uploaded_video(uploaded)
        if error:
            return None, {}, (jsonify({"error": error}), status)
        return payload['video_url'], {
            key: payload[key] for key in ('size_bytes', 'duration', 'warnings')
        }, None

    video_url = request.form.get('video_url', '').strip() or None
    if not video_url:
        return None, {}, None
    try:
        video_url = video_media.check_url(video_url)
    except video_media.VideoError as exc:
        return None, {}, (jsonify({"error": str(exc)}), 400)
    size = video_media.head_size(video_url)
    return video_url, {
        'size_bytes': size,
        'duration': None,
        'warnings': video_media.check_compat(size_bytes=size),
    }, None

@app.route('/compose/post/<int:post_id>/video', methods=['POST'])
def compose_update_post_video(post_id: int):
    """Attach, replace or clear a standalone post's video.

    Takes a ``video_url`` (an empty one clears the video) or a ``video`` file to
    upload. Applies to every row of the card, since the video is part of what
    groups them. The reply carries ``warnings``: what each platform will make of
    a video this size and length, so a problem shows when the video is attached
    rather than when the post fails.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    video_url, report, error = _requested_video()
    if error:
        return error

    return jsonify({
        "success": True,
        "video_url": video_url,
        "updated_ids": _apply_card_video(post, video_url, uid),
        **report,
    })

@app.route('/compose/post/<int:post_id>/stock-image', methods=['GET'])
def compose_get_stock_images(post_id: int):
    """Search for stock images based on post content.
    ---
    tags:
      - Compose
    parameters:
      - name: post_id
        in: path
        type: integer
        required: true
      - name: count
        in: query
        type: integer
        default: 5
        description: Number of images to return
    responses:
      200:
        description: List of stock images
      404:
        description: Post not found
      503:
        description: No stock image API configured
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    if not stock_images_configured():
        return jsonify({
            "error": "No stock image API configured. Add UNSPLASH_ACCESS_KEY, PEXELS_API_KEY, or PIXABAY_API_KEY to your .env file.",
            "configured": False,
        }), 503
    
    count = request.args.get('count', 5, type=int)
    count = min(max(count, 1), 20)  # Clamp between 1 and 20
    
    content = post['content'] if post['content'] else ''
    keywords = extract_keywords_from_text(content)
    images = get_images_for_post(content, count=count)
    
    return jsonify({
        "success": True,
        "keywords": keywords,
        "images": images,
        "configured_services": get_stock_image_services(),
    })

@app.route('/compose/post/<int:post_id>/stock-image', methods=['POST'])
def compose_apply_stock_image(post_id: int):
    """Apply a stock image to a post, saving it to the library first.
    ---
    tags:
      - Compose
    parameters:
      - name: post_id
        in: path
        type: integer
        required: true
      - name: body
        in: body
        schema:
          type: object
          properties:
            image_url:
              type: string
            save_to_library:
              type: boolean
              default: true
    responses:
      200:
        description: Image applied successfully
      404:
        description: Post not found
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    data = request.get_json() or {}
    image_url = data.get('image_url', '').strip()
    save_to_library = data.get('save_to_library', True)

    if not image_url:
        return jsonify({"error": "No image URL provided"}), 400

    # If save_to_library is true, download and save the stock image
    saved_url = image_url
    if save_to_library:
        try:
            saved_url = save_stock_image_to_library(image_url, user_id=uid)
        except Exception as e:
            app.logger.warning(f"Failed to save stock image to library: {e}, using original URL")
            saved_url = image_url

    return jsonify({
        "success": True,
        "image_url": saved_url,
        "saved_to_library": saved_url != image_url,
        "updated_ids": _apply_card_image(post, saved_url, uid),
    })

@app.route('/compose/post/<int:post_id>/link-image', methods=['GET', 'POST'])
def compose_apply_link_image(post_id: int):
    """Fetch the og:image of a URL referenced by the post and attach it.
    ---
    tags:
      - Compose
    parameters:
      - name: post_id
        in: path
        type: integer
        required: true
      - name: body
        in: body
        schema:
          type: object
          properties:
            url:
              type: string
              description: Optional override URL. Must be one of the URLs detected in the post body unless `allow_external` is true.
            allow_external:
              type: boolean
              default: false
    responses:
      200:
        description: Image attached successfully (POST) or detected URLs (GET)
      400:
        description: No URL available or override URL not in post body
      404:
        description: Post not found
      422:
        description: Could not extract an og:image from the URL
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    detected = extract_urls_from_text(post.get('content') or '')

    if request.method == 'GET':
        return jsonify({
            "success": True,
            "detected_urls": detected,
            "image_url": post.get('image_url'),
        })

    data = request.get_json(silent=True) or {}
    override = (data.get('url') or '').strip()
    allow_external = bool(data.get('allow_external'))

    target = override or (detected[0] if detected else None)
    if not target:
        return jsonify({
            "error": "No URL found in post and none provided.",
            "detected_urls": detected,
        }), 400

    if override and override not in detected and not allow_external:
        return jsonify({
            "error": "Override URL is not present in the post body.",
            "detected_urls": detected,
        }), 400

    try:
        _assert_safe_url(target)
    except UnsafeURLError as exc:
        return jsonify({"error": f"URL rejected: {exc}", "detected_urls": detected}), 400

    og_image = fetch_og_image_for_url(target)
    if not og_image:
        return jsonify({
            "error": "Could not find an og:image at that URL.",
            "detected_urls": detected,
            "source_url": target,
        }), 422

    try:
        saved_url = save_stock_image_to_library(og_image, user_id=uid)
    except (UnsafeURLError, RuntimeError, ValueError, requests.RequestException) as exc:
        app.logger.warning("Failed to save og:image %s for post %s: %s", og_image, post_id, exc)
        return jsonify({
            "error": f"Failed to download og:image: {exc}",
            "detected_urls": detected,
            "source_url": target,
            "og_image_url": og_image,
        }), 422

    return jsonify({
        "success": True,
        "image_url": saved_url,
        "source_url": target,
        "og_image_url": og_image,
        "detected_urls": detected,
        "updated_ids": _apply_card_image(post, saved_url, uid),
    })

@app.route('/compose/post/<int:post_id>/refresh-source-image', methods=['POST'])
def compose_refresh_source_image(post_id: int):
    """Re-pull the image from the post's original source (YouTube/article).

    Posts generated from a saved source store the source URL in
    ``source_content`` (with ``source_type`` of 'saved_source' or 'url'). This
    re-fetches the YouTube thumbnail or article og:image so an updated source
    image can be applied to the post.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    source_type = post['source_type'] if 'source_type' in post.keys() else None
    source_content = post['source_content'] if 'source_content' in post.keys() else None
    url = (source_content or '').strip()
    if source_type not in ('saved_source', 'url') or not url.startswith(('http://', 'https://')):
        return jsonify({"error": "This post is not tied to a YouTube/article source."}), 400

    og_image = None
    if is_youtube_url(url):
        video_id = get_youtube_video_id(url)
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                og_image = info.get("thumbnail")
        except Exception:
            og_image = None
        if not og_image and video_id:
            og_image = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    else:
        og_image = fetch_og_image_for_url(url)

    if not og_image:
        return jsonify({
            "error": "Could not find an image at the source.",
            "source_url": url,
        }), 422

    try:
        _assert_safe_url(og_image)
    except UnsafeURLError as exc:
        return jsonify({"error": f"Source image rejected: {exc}", "source_url": url}), 422

    try:
        saved_url = save_stock_image_to_library(og_image, user_id=uid)
    except (UnsafeURLError, RuntimeError, ValueError, requests.RequestException) as exc:
        app.logger.warning("Failed to save source image %s for post %s: %s", og_image, post_id, exc)
        return jsonify({
            "error": f"Failed to download source image: {exc}",
            "source_url": url,
            "og_image_url": og_image,
        }), 422

    return jsonify({
        "success": True,
        "image_url": saved_url,
        "source_url": url,
        "og_image_url": og_image,
        "updated_ids": _apply_card_image(post, saved_url, uid),
    })

def _maybe_attach_link_image(
    post_id: int,
    content: str | None,
    user_id: int,
    sibling_ids: list | None = None,
) -> None:
    """Schedule a background fetch of the first URL's og:image for the post.

    No-op if the post already has an image, has no URLs, or all fetches fail.
    Designed to be called from request handlers without blocking the response.
    `sibling_ids` are the other rows of the same Compose card: one fetch, one
    image, applied to all of them so they stay a single card.

    ``user_id`` is required, not derived from the request: the fetch runs on an
    executor thread with no request context, so the caller hands over the row's
    owner and every read and write inside stays scoped to them.
    """
    if not content:
        return
    urls = extract_urls_from_text(content)
    if not urls:
        return

    loader, updater = get_standalone_post, update_standalone_post_image

    def _worker(pid: int, candidate_urls: list) -> None:
        try:
            current = loader(pid, user_id)
            if not current:
                return
            if current['image_url'] if 'image_url' in current.keys() else None:
                return
            for candidate in candidate_urls:
                try:
                    _assert_safe_url(candidate)
                except UnsafeURLError as exc:
                    app.logger.info("Skipping unsafe URL %s for post %s: %s", candidate, pid, exc)
                    continue
                og_image = fetch_og_image_for_url(candidate)
                if not og_image:
                    continue
                try:
                    saved_url = save_stock_image_to_library(og_image, user_id=user_id)
                except (UnsafeURLError, RuntimeError, ValueError, requests.RequestException) as exc:
                    app.logger.info(
                        "Could not save og:image %s for post %s: %s", og_image, pid, exc
                    )
                    continue
                for target in [pid] + list(sibling_ids or []):
                    latest = loader(target, user_id)
                    latest_image = latest['image_url'] if latest and 'image_url' in latest.keys() else None
                    if latest and not latest_image:
                        updater(target, saved_url, user_id)
                        app.logger.info(
                            "Auto-attached og:image to post %s from %s", target, candidate
                        )
                return
        except Exception:
            app.logger.exception("Background link-image worker failed for post %s", pid)

    try:
        _link_image_executor.submit(_worker, post_id, urls)
    except RuntimeError:
        # Executor shut down (e.g. during reload); run inline as a best effort
        _worker(post_id, urls)

@app.route('/compose/stock-images/search', methods=['GET'])
def compose_search_stock_images():
    """Search for stock images by custom query.
    ---
    tags:
      - Compose
    parameters:
      - name: q
        in: query
        type: string
        required: true
        description: Search query
      - name: count
        in: query
        type: integer
        default: 5
    responses:
      200:
        description: List of stock images
      503:
        description: No stock image API configured
    """
    if not stock_images_configured():
        return jsonify({
            "error": "No stock image API configured",
            "configured": False,
        }), 503
    
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    
    count = request.args.get('count', 5, type=int)
    count = min(max(count, 1), 20)
    
    images = search_stock_images(query, per_page=count)
    
    return jsonify({
        "success": True,
        "query": query,
        "images": images,
        "configured_services": get_stock_image_services(),
    })

@app.route('/compose/stock-images/status', methods=['GET'])
def compose_stock_images_status():
    """Check if stock image APIs are configured.
    ---
    tags:
      - Compose
    responses:
      200:
        description: Configuration status
    """
    return jsonify({
        "configured": stock_images_configured(),
        "services": get_stock_image_services(),
    })

@app.route('/compose/upload-image', methods=['POST'])
def compose_upload_image():
    """Upload an image file and return its URL."""
    uid = current_user()['id']
    if 'image' not in request.files:
        return jsonify({"error": "No image file provided"}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Check extension first (fast rejection)
    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    # Manual size cap for images (the global MAX_CONTENT_LENGTH is raised for video)
    file.seek(0, os.SEEK_END)
    if file.tell() > MAX_IMAGE_BYTES:
        file.seek(0)
        return jsonify({"error": "Image too large (max 16MB)."}), 400
    file.seek(0)

    # Validate image content and re-encode to strip embedded data
    try:
        cleaned_bytes, ext = validate_and_clean_image(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    
    # Upload to Cloudinary if configured (for public URLs)
    if cloudinary_ready():
        try:
            result = cloudinary.uploader.upload(
                cleaned_bytes,
                folder="insights",
                resource_type="image"
            )
            image_url = result['secure_url']
            filename = result['public_id'].split('/')[-1]
            file_size = result.get('bytes', len(cleaned_bytes))
            
            # Save to database for image library
            add_uploaded_image(
                filename=filename,
                url=image_url,
                storage='cloudinary',
                size=file_size,
                user_id=uid,
            )

            return jsonify({
                "success": True,
                "image_url": image_url,
                "filename": filename,
                "storage": "cloudinary"
            })
        except Exception as e:
            app.logger.error("Cloudinary upload failed: %s", str(e))
            # Fall back to local storage

    # Local storage fallback
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

    with open(filepath, 'wb') as f:
        f.write(cleaned_bytes)

    image_url = f"{request.host_url}static/uploads/{unique_filename}"

    # Save to database for image library
    add_uploaded_image(
        filename=unique_filename,
        url=f"/static/uploads/{unique_filename}",
        storage='local',
        size=len(cleaned_bytes),
        user_id=uid,
    )
    
    return jsonify({
        "success": True,
        "image_url": image_url,
        "filename": unique_filename,
        "storage": "local",
        "warning": "Image stored locally - may not work with external platforms" if not cloudinary_ready() else None
    })

def _store_uploaded_video(file):
    """Validate an uploaded video and put it on Cloudinary.

    Returns ``(payload, error, status)``: ``payload`` on success, otherwise a
    readable ``error`` and the HTTP status to send it with. The payload carries
    what the composer needs to judge the video: its size and, when ffprobe is
    installed, its duration, plus the per-platform ``warnings`` for both.

    Requires Cloudinary (the platforms' fetchers can't reach local
    /static/uploads in most deploys). No transcoding: a platform that rejects
    the codec, shape or length says so through the publish flow. For large files,
    paste a public URL instead.
    """
    uid = current_user()['id']
    if not allowed_video_file(file.filename):
        return None, f"Video type not allowed. Allowed types: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}", 400

    if not cloudinary_ready():
        return None, (
            "Video upload needs Cloudinary. Configure Cloudinary on the "
            "settings page, or paste a public video URL instead."
        ), 400

    video_bytes = file.read()
    if not video_bytes:
        return None, "Empty video file", 400

    # Probe the local copy for its length (ffprobe, when installed) so the reply
    # can warn about platforms it is too short or long for. It only warns: a
    # video one platform refuses still goes out to the others.
    duration = None
    suffix = os.path.splitext(file.filename)[1].lower() or '.mp4'
    # The copy is written, closed and removed rather than held open: on Windows
    # a file Python still holds open denies every other handle, so ffprobe read
    # nothing and every upload came back with no duration and no length
    # warnings.
    fd, probe_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as probe_copy:
            probe_copy.write(video_bytes)
        duration = video_media.probe_file(probe_path).get('duration')
    finally:
        try:
            os.remove(probe_path)
        except OSError:
            pass

    try:
        result = cloudinary.uploader.upload(
            video_bytes,
            folder="insights",
            resource_type="video",
        )
    except Exception as e:
        app.logger.error("Cloudinary video upload failed: %s", str(e))
        return None, (
            f"Video upload failed: {e}. Try a smaller/shorter clip or paste a public URL."
        ), 400

    video_url = result['secure_url']
    filename = result['public_id'].split('/')[-1]
    size = result.get('bytes', len(video_bytes))
    add_uploaded_image(
        filename=filename,
        url=video_url,
        storage='cloudinary',
        size=size,
        media_type='video',
        user_id=uid,
    )
    return {
        "video_url": video_url,
        "filename": filename,
        "storage": "cloudinary",
        "size_bytes": size,
        "duration": duration,
        "warnings": video_media.check_compat(size_bytes=size, duration_seconds=duration),
    }, None, 200

@app.route('/compose/upload-video', methods=['POST'])
def compose_upload_video():
    """Upload a video for a post."""
    if 'video' not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    payload, error, status = _store_uploaded_video(file)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"success": True, **payload})

def _load_image_for_fit(url: str) -> bytes:
    """Load raw image bytes for the media-fit endpoint.

    Local /static/uploads URLs are read from disk; everything else goes
    through the SSRF-hardened fetcher.
    """
    parsed = urlparse(url)
    path = parsed.path or ''
    if path.startswith('/static/uploads/'):
        filename = os.path.basename(path)
        if not allowed_file(filename):
            raise ValueError("unsupported local image type")
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.isfile(filepath):
            raise ValueError("local image not found")
        if os.path.getsize(filepath) > MAX_IMAGE_BYTES:
            raise ValueError("image too large (max 16MB)")
        with open(filepath, 'rb') as f:
            return f.read()
    data, _content_type, _final_url = _fetch_safely(
        url,
        max_bytes=MAX_IMAGE_BYTES,
        timeout=15,
        allowed_content_types=("image/",),
    )
    return data

@app.route('/compose/media/annotate', methods=['POST'])
def compose_annotate_media():
    """Bake text layers onto an image or burn them into a video.
    ---
    tags:
      - Compose
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            url:
              type: string
              description: Source media URL (upload, library, or public URL)
            kind:
              type: string
              enum: [image, video]
            layers:
              type: array
              description: >
                Text layers: {text, x, y (0..1 center), size (fraction of
                media width), color (#rrggbb), bg (none|dark|light|black|white)}
    responses:
      200:
        description: New media URL
      400:
        description: Validation or processing error
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    kind = (data.get("kind") or "image").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    if kind not in ("image", "video"):
        return jsonify({"error": "kind must be image or video"}), 400
    layers, err = _clean_overlay_layers(data.get("layers"))
    if err:
        return jsonify({"error": err}), 400

    font_path = _find_overlay_font()

    if kind == "image":
        try:
            raw = _load_image_for_fit(url)
            img = Image.open(io.BytesIO(raw))
            img.load()
            annotated = _annotate_image(img, layers, font_path)
            output = io.BytesIO()
            annotated.save(output, format="JPEG", quality=90, optimize=True)
        except (UnsafeURLError, ValueError, RuntimeError, requests.RequestException) as e:
            return jsonify({"error": f"Could not load image: {e}"}), 400
        except Exception as e:
            app.logger.error("Image annotate failed for %s: %s", url, e)
            return jsonify({"error": f"Could not add text: {e}"}), 400
        try:
            new_url = _store_fitted_image(output.getvalue(), "igtext")
        except OSError as e:
            app.logger.error("Could not store annotated image: %s", e)
            return jsonify({"error": "Could not save the image."}), 500
        return jsonify({"success": True, "image_url": new_url, "kind": "image"})

    # Video path — needs ffmpeg on the server
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return jsonify({
            "error": "Adding text to video needs ffmpeg installed on the server "
                     "(brew install ffmpeg).",
        }), 400
    if not font_path:
        return jsonify({"error": "No usable system font found for video text."}), 400
    workdir = tempfile.mkdtemp(prefix="igtext_")
    try:
        try:
            src = _load_video_for_annotate(url, workdir)
            out_path = _annotate_video(src, layers, font_path, workdir)
        except (UnsafeURLError, ValueError, RuntimeError, requests.RequestException,
                subprocess.TimeoutExpired) as e:
            return jsonify({"error": f"Could not add text to video: {e}"}), 400
        try:
            new_url = _store_annotated_video(out_path)
        except Exception as e:
            app.logger.error("Could not store annotated video: %s", e)
            return jsonify({"error": f"Could not save the video: {e}"}), 500
        return jsonify({"success": True, "video_url": new_url, "kind": "video"})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

@app.route('/compose/list-images', methods=['GET'])
def compose_list_images():
    """List uploaded media from the database and local folder.

    The library holds videos as well as images, so ``?type=`` picks which:
    ``image`` (the default, which is what every image picker wants) or ``video``.
    """
    from datetime import datetime as dt

    uid = current_user()['id']
    wanted = 'video' if request.args.get('type') == 'video' else 'image'
    images = []
    seen_urls = set()

    # First, get images from the database (includes Cloudinary images)
    db_images = list_uploaded_images(uid)
    for img in db_images:
        kind = (img['media_type'] if 'media_type' in img.keys() else None) or 'image'
        if kind != wanted:
            continue
        # Convert ISO datetime string to timestamp for consistent sorting
        created_at = img['created_at']
        try:
            if created_at:
                timestamp = dt.fromisoformat(created_at).timestamp()
            else:
                timestamp = 0
        except (ValueError, TypeError):
            timestamp = 0
        
        images.append({
            'id': img['id'],
            'filename': img['filename'],
            'url': img['url'],
            'size': img['size'] or 0,
            'storage': img['storage'],
            'created_at': created_at,
            'modified': timestamp,
            'media_type': kind,
        })
        seen_urls.add(img['url'])
    
    # Also scan local uploads folder for any images not in database (backward compatibility)
    upload_dir = app.config['UPLOAD_FOLDER']
    if wanted == 'image' and os.path.exists(upload_dir):
        for filename in os.listdir(upload_dir):
            if allowed_file(filename):
                local_url = f"/static/uploads/{filename}"
                if local_url not in seen_urls:
                    filepath = os.path.join(upload_dir, filename)
                    stat = os.stat(filepath)
                    images.append({
                        'filename': filename,
                        'url': local_url,
                        'size': stat.st_size,
                        'storage': 'local',
                        'modified': stat.st_mtime
                    })
    
    # Sort by most recently modified/created first (all values are now floats)
    images.sort(key=lambda x: x.get('modified', 0), reverse=True)
    
    return jsonify({'success': True, 'images': images})

@app.route('/compose/post/<int:post_id>/delete', methods=['POST'])
def compose_delete_post(post_id: int):
    """Delete a standalone post — the whole card when the page sends its rows."""
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    rows = _card_rows(post, _requested_post_ids(post_id), uid)
    for row in rows:
        delete_standalone_post(row['id'], uid)
    return jsonify({"success": True, "deleted_ids": [row['id'] for row in rows]})

@app.route('/compose/posts/delete-bulk', methods=['POST'])
def compose_delete_bulk():
    """Delete the selected standalone posts.

    Takes either ``post_ids`` (the ticked checkboxes) or ``filters`` (a "select
    all across every page" selection, resolved server-side).
    """
    uid = current_user()['id']
    data = request.get_json(silent=True) or {}
    rows, error = _selected_standalone_posts(data, uid)
    if error:
        return error

    post_ids = [row['id'] for row in rows]
    deleted = delete_standalone_posts_bulk(post_ids, uid) if post_ids else 0

    return jsonify({
        "success": True,
        "deleted_count": deleted,
    })

@app.route('/compose/posts/bulk-image', methods=['POST'])
def compose_bulk_update_images():
    """Bulk update images for multiple standalone posts.
    ---
    tags:
      - Compose
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            post_ids:
              type: array
              items:
                type: integer
            image_url:
              type: string
              description: Image URL to set, or null/empty to remove
    responses:
      200:
        description: Images updated successfully
      400:
        description: No posts selected
    """
    uid = current_user()['id']
    data = request.get_json()
    post_ids = data.get('post_ids', [])
    image_url = data.get('image_url')  # None or empty string to remove

    if not post_ids:
        return jsonify({"error": "No posts selected"}), 400

    # Convert to integers
    post_ids = [int(pid) for pid in post_ids]

    # Update each post's image
    updated = 0
    for post_id in post_ids:
        post = get_standalone_post(post_id, uid)
        if post:
            update_standalone_post_image(post_id, image_url if image_url else None, uid)
            updated += 1
    
    return jsonify({
        "success": True,
        "updated_count": updated,
        "image_url": image_url
    })

@app.route('/compose/post/<int:post_id>/toggle-used', methods=['POST'])
def compose_toggle_used(post_id: int):
    """Toggle a standalone post's used status, card-wide.

    A card reads as used only once every platform it targets is done, so the
    toggle flips them together rather than leaving a half-used card.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    rows = _card_rows(post, _requested_post_ids(post_id), uid)
    new_status = not all(bool(row['used']) for row in rows)
    for row in rows:
        mark_standalone_post_used(row['id'], new_status, user_id=uid)
    return jsonify({
        "success": True,
        "used": new_status,
        "updated_ids": [row['id'] for row in rows],
    })

@app.route('/compose/posts/bulk-toggle-used', methods=['POST'])
def compose_bulk_toggle_used():
    """Bulk mark/unmark standalone posts as used.

    Takes either ``post_ids`` (the ticked checkboxes) or ``filters`` (a "select
    all across every page" selection, resolved server-side).
    """
    uid = current_user()['id']
    data = request.get_json(silent=True) or {}
    used = data.get('used', True)

    rows, error = _selected_standalone_posts(data, uid)
    if error:
        return error

    updated = 0
    for row in rows:
        mark_standalone_post_used(row['id'], used, user_id=uid)
        updated += 1

    return jsonify({
        "success": True,
        "updated_count": updated,
        "used": used,
    })

# ---------------------------------------------------------------------------
# Publishing a saved post
#
# Every "post now" in the composer lands here, whichever platform it names and
# however many targets it fans out to. The five per-platform endpoints are one
# call each, and the card-wide publish is the same call in a loop, so a post
# sent to one account and the same post sent to six take exactly the same route
# through token refresh, character limits and error reporting.
# ---------------------------------------------------------------------------

@app.route('/compose/post/<int:post_id>/push', methods=['POST'])
def compose_push_post(post_id: int):
    """Queue one domestic article card for the local agent.

    The agent, not this process, does the pushing, so an offline agent is a
    409 with the way out — not a queued job that would sit until someone
    starts the agent and nobody remembers why. A ``run_at`` flips that: a
    scheduled push queues regardless of the agent, because waiting for the
    agent to come online is exactly what the schedule is for.
    """
    uid = current_user()['id']
    post = get_standalone_post(post_id, uid)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    target = (request.form.get('target') or '').strip().lower()
    if target not in domestic_publisher.DOMESTIC_ARTICLE_PLATFORMS:
        return jsonify({"error": f"{target or '该平台'} 不支持草稿推送"}), 400
    if target != post['platform']:
        return jsonify({"error": "该卡片不是这个平台的内容"}), 400
    if target not in domestic_publisher.enabled_platforms():
        return jsonify({"error": "平台未在设置页启用"}), 400
    run_after = None
    run_at = (request.form.get('run_at') or '').strip()
    if run_at:
        # The browser sends datetime-local's wall time plus its own offset
        # from UTC in minutes (UTC+8 -> -480), so a schedule means what the
        # user's clock says, not what the server's timezone does. Absent
        # offset keeps the value as submitted.
        tz_offset = request.form.get('tz_offset', type=int) or 0
        try:
            run_after = (datetime.fromisoformat(run_at)
                         + timedelta(minutes=tz_offset)).replace(
                tzinfo=timezone.utc).isoformat(timespec='seconds')
        except (ValueError, OverflowError):
            return jsonify({"error": "时间格式不对"}), 400
    elif not agent_hub.agent_online():
        return jsonify({"error": "推送离线：请启动浏览器扩展（Insights 发布助手），或本机 agent"}), 409
    outcome = domestic_publisher.enqueue_card_push(post, run_after=run_after)
    return jsonify({"job_id": outcome["job_id"], "already": outcome["already"],
                    "platform": target})

@app.route('/agent-jobs/<int:job_id>.json')
def agent_job_json(job_id: int):
    job = database.get_agent_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)

@app.route('/agent/login-states.json')
def agent_login_states_json():
    """The compose strip's data: one merged per-platform login view."""
    return jsonify(agent_hub.login_states_view())

@app.route('/agent/status.json')
def agent_status_json():
    """One boolean for the queue page's connection pill: is the助手 alive.

    age is seconds since the last heartbeat the process saw, null when none
    ever arrived — the pill's tooltip reads it as「N 秒前有心跳」.
    """
    snap = agent_hub.heartbeat_snapshot()
    return jsonify({"online": agent_hub.agent_online(),
                    "age": None if snap is None else snap.get("age")})

@app.route("/agent-jobs/pair-code", methods=["POST"])
def agent_pair_code():
    """Mint a pairing code for the queue page's「配对浏览器扩展」button.

    Session-only by the login wall (not in PUBLIC_ENDPOINTS): minting is an
    owner action, while redeeming on /agent/pair is the extension's one
    unauthenticated bootstrap.
    """
    code = agent_hub.create_pairing_code()
    return jsonify({"code": code, "expires_in": 300})

@app.route("/agent/pair", methods=["POST"])
def agent_pair():
    """The extension's bootstrap: one live pairing code -> the bearer token.

    Deliberately outside the bearer door — this is how a fresh extension
    gets the token at all. The code's short life, single use and five-try
    lockout are the rate limit; every redeem attempt lands in the activity
    log via the auto-tracker, which is the audit trail here.
    """
    body = request.get_json(silent=True)
    code = body.get("code") if isinstance(body, dict) else None
    token = agent_hub.redeem_pairing_code(code)
    if not token:
        return jsonify({"error": "配对码无效或已过期"}), 403
    return jsonify({"token": token})

@app.route('/agent-jobs')
def agent_jobs_page():
    """The queue, readably: what was pushed where, and what came of it.

    The payload and the result are stored as JSON, so the page is where they
    are turned back into words — the platforms a job was for carry their
    display names, and each platform's own outcome stands on its own line.
    """
    jobs = database.list_agent_jobs(limit=100)
    for job in jobs:
        payload = json.loads(job['payload'] or '{}')
        job['targets'] = '、'.join(
            domestic_publisher.display_name(i.get('target', ''))
            for i in payload.get('items') or [])
        job['title'] = payload.get('title') or ''
        job['results'] = json.loads(job['result'] or '[]')
    return render_template('agent_jobs.html', jobs=jobs,
                           agent_online=agent_hub.agent_online())

@app.route("/agent/heartbeat", methods=["POST"])
def agent_heartbeat():
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    agent_hub.record_heartbeat(request.get_json(silent=True) or {})
    return jsonify({"ok": True})

@app.route("/agent/jobs/claim", methods=["POST"])
def agent_claim():
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    # The body carries only the agent-name hint, so a foreign client posting a
    # truthy non-object (a bare list, say) is tolerated as {} rather than
    # blown up by `.get` into a 500 — unlike /events, where the body is the
    # payload and a non-object is refused. The executor and platform list are
    # the extension's lane declaration; anything non-string/non-list reads as
    # the old agent's unmarked body.
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    platforms = body.get("platforms")
    if not isinstance(platforms, list) or not all(isinstance(p, str) for p in platforms):
        platforms = None
    raw_executor = body.get("executor")
    job = database.claim_agent_job(
        kinds=("article_sync", "video_publish"),
        agent=body.get("agent", ""),
        executor=raw_executor if isinstance(raw_executor, str) else None,
        platforms=platforms,
    )
    if not job:
        return "", 204
    return jsonify(job)

@app.route("/agent/jobs/<int:job_id>/events", methods=["POST"])
def agent_job_events(job_id: int):
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    event = request.get_json(silent=True) or {}
    if not isinstance(event, dict):
        return jsonify({"error": "event must be an object"}), 400
    try:
        job = database.record_agent_job_event(job_id, event)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)

@app.route('/compose/clear-all', methods=['POST'])
def compose_clear_all():
    """Clear all standalone posts."""
    uid = current_user()['id']
    posts = list_standalone_posts(uid)
    if not posts:
        return jsonify({"success": True, "message": "No posts to clear"})

    post_ids = [p['id'] for p in posts]
    deleted = delete_standalone_posts_bulk(post_ids, uid)

    return jsonify({
        "success": True,
        "deleted_count": deleted,
        "message": f"Cleared {deleted} posts",
    })

# ============================================================================
# URL Sources Management
# ============================================================================

@app.route('/sources')
def sources_page():
    """Display saved URL sources."""
    uid = current_user()['id']
    sources = list_url_sources(uid)
    post_counts = count_standalone_posts_by_source_urls(
        [s['url'] for s in sources if s['url']], uid
    )
    sources_with_status = []
    for s in sources:
        s_dict = dict(s)
        s_dict['is_youtube'] = is_youtube_url(s_dict.get('url', ''))
        s_dict['post_count'] = post_counts.get(s_dict.get('url', ''), 0)
        if s_dict['is_youtube']:
            ep = get_episode(s_dict['url'], uid)
            if ep:
                s_dict['episode_status'] = ep['status']
                s_dict['episode_id'] = ep['id']
            else:
                s_dict['episode_status'] = None
                s_dict['episode_id'] = None
        sources_with_status.append(s_dict)
    return render_template('sources.html', sources=sources_with_status)

@app.route('/sources/<int:source_id>/posts')
def source_posts(source_id: int):
    """Return the standalone posts generated from a saved source as JSON."""
    uid = current_user()['id']
    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404

    posts = list_standalone_posts_by_source_url(source['url'], uid)
    post_ids = [p['id'] for p in posts]
    posted_info = get_posted_info_for_standalone_posts(post_ids, uid) if post_ids else {}

    result = []
    for p in posts:
        p_keys = p.keys()
        platform = p['platform']
        posted = None
        platform_posted = posted_info.get(p['id'], {}).get(platform)
        if platform_posted:
            posted = {
                'url': platform_posted.get('url'),
                'posted_at': platform_posted.get('posted_at'),
            }
        result.append({
            'id': p['id'],
            'platform': platform,
            'content': p['content'],
            'image_url': p['image_url'] if 'image_url' in p_keys else None,
            'created_at': p['created_at'],
            'used': bool(p['used']) if 'used' in p_keys else False,
            'posted': posted,
        })

    return jsonify({
        'source': {'id': source['id'], 'url': source['url'], 'title': source['title']},
        'posts': result,
    })

@app.route('/sources', methods=['POST'])
def add_source():
    """Add a new URL source by extracting content from a URL."""
    import trafilatura
    uid = current_user()['id']

    # Accept URL from JSON or form data
    if request.is_json:
        url = request.json.get('url', '').strip()
    else:
        url = request.form.get('url', '').strip()
    
    if not url:
        return jsonify({"error": "URL is required"}), 400
    
    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Check if URL already exists for this user; another user keeping their
    # own row for the same URL is expected, not a conflict.
    existing = get_url_source_by_url(url, uid)
    if existing:
        return jsonify({
            "error": "This URL has already been saved",
            "existing_id": existing['id']
        }), 409
    
    try:
        if is_youtube_url(url):
            video_id = get_youtube_video_id(url)
            title, description, og_image = url, "", None
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get("title", title)
                    description = info.get("description", "")
                    og_image = info.get("thumbnail")
            except Exception:
                pass
            if not og_image and video_id:
                og_image = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
            source_id = add_url_source(
                url=url, title=title,
                description=description, content="", og_image=og_image,
                user_id=uid,
            )
            return jsonify({
                "success": True,
                "is_youtube": True,
                "source": {
                    "id": source_id,
                    "url": url,
                    "title": title,
                    "description": description,
                    "content": "",
                    "og_image": og_image,
                }
            })

        # Fetch the URL content using trafilatura
        downloaded = trafilatura.fetch_url(url)
        
        if not downloaded:
            return jsonify({"error": "Failed to fetch URL content. Please check the URL is accessible."}), 400
        
        # Extract main article content
        body_content = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
        
        # Extract metadata
        metadata = trafilatura.extract_metadata(downloaded)
        
        title = ""
        description = ""
        og_image = None
        
        if metadata:
            title = metadata.title or ""
            description = metadata.description or ""
            og_image = metadata.image
        
        # Fallback metadata extraction from HTML if needed
        if not title or not description or not og_image:
            import re as re_module
            
            if not title:
                title_match = re_module.search(r'<title>([^<]+)</title>', downloaded, re_module.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                
                og_title_match = re_module.search(
                    r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re_module.IGNORECASE
                )
                if og_title_match:
                    title = og_title_match.group(1)
            
            if not description:
                # Try og:description
                og_desc_match = re_module.search(
                    r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re_module.IGNORECASE
                )
                if og_desc_match:
                    description = og_desc_match.group(1)
                else:
                    # Try meta description
                    meta_desc_match = re_module.search(
                        r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
                        downloaded, re_module.IGNORECASE
                    )
                    if meta_desc_match:
                        description = meta_desc_match.group(1)
            
            if not og_image:
                og_image_match = re_module.search(
                    r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re_module.IGNORECASE
                )
                if og_image_match:
                    og_image = og_image_match.group(1)
        
        # Use URL as title fallback
        if not title:
            title = url
        
        # Save to database
        source_id = add_url_source(
            url=url,
            title=title,
            description=description,
            content=body_content,
            og_image=og_image,
            user_id=uid,
        )
        
        return jsonify({
            "success": True,
            "source": {
                "id": source_id,
                "url": url,
                "title": title,
                "description": description,
                "content": body_content,
                "og_image": og_image,
            }
        })
        
    except Exception as e:
        app.logger.exception("Failed to add URL source %s: %s", url, str(e))
        return jsonify({"error": f"Failed to extract content: {str(e)}"}), 500

@app.route('/sources/<int:source_id>')
def get_source(source_id: int):
    """Get a single URL source by ID."""
    uid = current_user()['id']
    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404
    return jsonify(dict(source))

@app.route('/sources/<int:source_id>', methods=['DELETE'])
def delete_source(source_id: int):
    """Delete a URL source."""
    uid = current_user()['id']
    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404

    deleted = delete_url_source(source_id, uid)
    return jsonify({"success": deleted})

@app.route('/sources/<int:source_id>/transcribe', methods=['POST'])
def transcribe_source(source_id: int):
    """Queue a YouTube source for background audio transcription."""
    uid = current_user()['id']
    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404
    if not is_youtube_url(source['url']):
        return jsonify({"error": "Only YouTube sources can be transcribed"}), 400
    existing = get_episode(source['url'], uid)
    if existing and existing['status'] in ('queued', 'processing'):
        return redirect(url_for('status_page'))
    queue_episode(source['url'], source['title'] or source['url'], None, None, user_id=uid)
    _user = current_user()
    task_queue.put({
        "url": source['url'],
        "title": source['title'] or source['url'],
        "feed_id": None,
        "published": None,
        "user_id": uid,
        "username": _user['username'] if _user else None,
    })
    return redirect(url_for('status_page'))

@app.route('/sources/<int:source_id>/reextract', methods=['POST'])
def reextract_source(source_id: int):
    """Re-extract content from a URL source using improved extraction."""
    import trafilatura

    uid = current_user()['id']
    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404
    
    url = source['url']
    
    try:
        if is_youtube_url(url):
            video_id = get_youtube_video_id(url)
            title, description, og_image = source['title'] or url, source['description'] or "", source['og_image']
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get("title", title)
                    description = info.get("description", "")
                    og_image = info.get("thumbnail")
            except Exception:
                pass
            if not og_image and video_id:
                og_image = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
            updated = update_url_source_content(
                source_id=source_id,
                user_id=uid,
                title=title,
                description=description,
                content="",
                og_image=og_image,
            )
            if not updated:
                return jsonify({"error": "Failed to update source"}), 500
            return jsonify({
                "success": True,
                "source": {
                    "id": source_id,
                    "url": url,
                    "title": title,
                    "description": description,
                    "content": "",
                    "og_image": og_image,
                }
            })

        # Use trafilatura for robust article extraction
        downloaded = trafilatura.fetch_url(url)
        
        if not downloaded:
            return jsonify({"error": "Failed to fetch URL content"}), 500
        
        # Extract main article content
        body_content = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
        
        # Extract metadata
        metadata = trafilatura.extract_metadata(downloaded)
        
        title = ""
        description = ""
        og_image = None
        
        if metadata:
            title = metadata.title or ""
            description = metadata.description or ""
            og_image = metadata.image
        
        # Fallback metadata extraction from HTML if needed
        if not title or not description:
            import re
            
            if not title:
                title_match = re.search(r'<title>([^<]+)</title>', downloaded, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                
                og_title_match = re.search(
                    r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re.IGNORECASE
                )
                if og_title_match:
                    title = og_title_match.group(1)
            
            if not description:
                og_desc_match = re.search(
                    r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re.IGNORECASE
                )
                if og_desc_match:
                    description = og_desc_match.group(1)
            
            if not og_image:
                og_image_match = re.search(
                    r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
                    downloaded, re.IGNORECASE
                )
                if og_image_match:
                    og_image = og_image_match.group(1)
        
        # Update the source in the database
        updated = update_url_source_content(
            source_id=source_id,
            user_id=uid,
            title=title,
            description=description,
            content=body_content,
            og_image=og_image,
        )
        
        if not updated:
            return jsonify({"error": "Failed to update source"}), 500
        
        # Return the updated source data
        return jsonify({
            "success": True,
            "source": {
                "id": source_id,
                "url": url,
                "title": title,
                "description": description,
                "content": body_content,
                "og_image": og_image,
            }
        })
        
    except Exception as e:
        app.logger.exception("Failed to re-extract source %d: %s", source_id, str(e))
        return jsonify({"error": f"Extraction failed: {str(e)}"}), 500

@app.route('/compose/generate-from-source', methods=['POST'])
def compose_generate_from_source():
    """Generate posts from a saved URL source."""
    uid = current_user()['id']
    source_id = request.form.get('source_id', type=int)
    platforms = request.form.getlist('platforms')
    tone = request.form.get('tone', 'professional')
    posts_per_platform = request.form.get('posts_per_platform', 1, type=int)
    extra_context = request.form.get('extra_context', '').strip() or None
    image_url = request.form.get('image_url', '').strip() or None
    ai_provider, ai_model, use_local = _parse_ai_provider(request.form)

    if not source_id:
        return jsonify({"error": "Source ID is required"}), 400

    source = get_url_source(source_id, uid)
    if not source:
        return jsonify({"error": "Source not found"}), 404

    if not image_url and source['og_image']:
        image_url = source['og_image']

    if is_youtube_url(source['url']):
        credit = f"IMPORTANT: Include this YouTube video URL as a reference/credit in every post: {source['url']}"
        extra_context = f"{credit}\n{extra_context}" if extra_context else credit
    if not platforms:
        platforms = ['xiaohongshu', 'douyin', 'wechat']
    

    # Copy is generated once per platform; the accounts decide how many rows
    # that copy is saved into, so picking two accounts for the same platform
    # costs no extra generation and produces one card aimed at both.
    accounts_by_platform = _requested_accounts_by_platform(set(platforms))

    posts_per_platform = max(1, min(posts_per_platform, 10))
    
    try:
        from insights import generate_posts_from_text
        
        source_text = f"TITLE: {source['title']}\n\n"
        if source['description']:
            source_text += f"DESCRIPTION: {source['description']}\n\n"
        source_text += f"CONTENT: {source['content']}\n\n"
        source_text += f"ORIGINAL URL: {source['url']}"
        
        generated = generate_posts_from_text(
            text=source_text,
            platforms=platforms,
            tone=tone,
            topic=source['title'],
            posts_per_platform=posts_per_platform,
            extra_context=extra_context,
            use_local=use_local,
            source_url=source['url'],
            provider=ai_provider,
            model=ai_model,
        )

        update_url_source_last_used(source_id, uid)
        
        # Normalize platform names and filter hallucinated platforms
        requested_platforms = {p.lower() for p in platforms}
        saved_posts = {}
        for platform, post_data in generated.items():
            if platform == 'raw':
                continue
            
            norm_platform = platform.lower().strip()

            if norm_platform not in requested_platforms:
                continue

            posts_list = post_data if isinstance(post_data, list) else [post_data]
            if norm_platform not in saved_posts:
                saved_posts[norm_platform] = []
            
            for post_content in posts_list:
                ids = _save_post_for_accounts(
                    accounts_by_platform.get(norm_platform),
                    user_id=uid,
                    source_type='saved_source',
                    source_content=source['url'][:1000],
                    platform=norm_platform,
                    content=post_content,
                    image_url=image_url,
                )
                saved_posts[norm_platform].append({
                    'id': ids[0],
                    'post_ids': ids,
                    'content': post_content,
                    'image_url': image_url,
                })
                if not image_url:
                    _maybe_attach_link_image(ids[0], post_content, user_id=uid,
                                             sibling_ids=ids[1:])
        
        return jsonify({
            "success": True,
            "generated": generated,
            "saved_posts": saved_posts,
            "source_title": source['title'],
        })
        
    except Exception as e:
        app.logger.exception("Failed to generate posts from source")
        return jsonify({"error": str(e)}), 500

# ============================================================================
# YouTube Thumbnail Generator
# ============================================================================

THUMBNAIL_UPLOAD_DIR = os.path.join(UPLOAD_FOLDER, 'youtube_thumbnails')
os.makedirs(THUMBNAIL_UPLOAD_DIR, exist_ok=True)

@app.route('/thumbnails')
def thumbnails_page():
    """YouTube Thumbnail Generator page."""
    return render_template('thumbnails.html')

@app.route('/thumbnails/metadata', methods=['POST'])
def thumbnails_fetch_metadata():
    """Fetch metadata for a YouTube video URL."""
    url = (request.form.get('url') or '').strip()
    if not url:
        return jsonify({"error": "YouTube URL is required"}), 400

    if not is_youtube_url(url):
        return jsonify({"error": "Please enter a valid YouTube URL"}), 400

    if classify_youtube_url(url) != 'video':
        return jsonify({"error": "Please enter a single YouTube video URL (not a channel or playlist)"}), 400

    try:
        metadata = fetch_youtube_metadata(url)
        return jsonify({"success": True, "metadata": metadata})
    except Exception as e:
        app.logger.exception("Failed to fetch YouTube metadata")
        return jsonify({"error": str(e)}), 500

@app.route('/thumbnails/suggest-prompt', methods=['POST'])
def thumbnails_suggest_prompt():
    """Return the auto-generated prompt for given URL / aspect / style (no image cost)."""
    url = (request.form.get('url') or '').strip()
    aspect = request.form.get('aspect', '16:9')
    style = request.form.get('style', 'bold')

    if not url:
        return jsonify({"error": "YouTube URL is required"}), 400
    if not is_youtube_url(url):
        return jsonify({"error": "Please enter a valid YouTube URL"}), 400
    if aspect not in ('16:9', '9:16'):
        return jsonify({"error": "Aspect ratio must be 16:9 or 9:16"}), 400
    if style not in ('bold', 'minimal', 'cinematic'):
        return jsonify({"error": "Style must be bold, minimal, or cinematic"}), 400

    try:
        metadata = fetch_youtube_metadata(url)
        prompt = suggested_thumbnail_prompt(metadata, aspect, style)
        return jsonify({"success": True, "prompt": prompt})
    except Exception as e:
        app.logger.exception("Failed to build suggested prompt")
        return jsonify({"error": str(e)}), 500

@app.route('/thumbnails/generate', methods=['POST'])
def thumbnails_generate():
    """Generate a thumbnail for a YouTube video, persist it, and return the result."""
    import base64 as b64module
    import uuid as uuid_mod

    uid = current_user()['id']

    url = (request.form.get('url') or '').strip()
    aspect = request.form.get('aspect', '16:9')
    style = request.form.get('style', 'bold')
    custom_prompt = (request.form.get('prompt') or '').strip() or None

    if not url:
        return jsonify({"error": "YouTube URL is required"}), 400
    if not is_youtube_url(url):
        return jsonify({"error": "Please enter a valid YouTube URL"}), 400
    if aspect not in ('16:9', '9:16'):
        return jsonify({"error": "Aspect ratio must be 16:9 or 9:16"}), 400
    if style not in ('bold', 'minimal', 'cinematic'):
        return jsonify({"error": "Style must be bold, minimal, or cinematic"}), 400

    try:
        result = generate_youtube_thumbnail(
            url=url,
            aspect=aspect,
            style=style,
            custom_prompt=custom_prompt,
        )

        # Persist the PNG to disk
        filename = f"{uuid_mod.uuid4().hex}.png"
        filepath = os.path.join(THUMBNAIL_UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(b64module.b64decode(result["image_base64"]))

        image_relpath = f"static/uploads/youtube_thumbnails/{filename}"
        image_url = f"/static/uploads/youtube_thumbnails/{filename}"

        meta = result["metadata"]
        saved_id = add_generated_thumbnail(
            youtube_url=url,
            video_id=meta.get("video_id"),
            title=meta.get("title", ""),
            channel=meta.get("channel", ""),
            aspect=aspect,
            style=style,
            prompt=result["prompt"],
            image_relpath=image_relpath,
            user_id=uid,
        )

        return jsonify({
            "success": True,
            "image_base64": result["image_base64"],
            "prompt": result["prompt"],
            "metadata": meta,
            "saved_id": saved_id,
            "image_url": image_url,
        })
    except Exception as e:
        app.logger.exception("Failed to generate thumbnail")
        return jsonify({"error": str(e)}), 500

@app.route('/thumbnails/saved')
def thumbnails_saved_list():
    """Return all saved thumbnails as JSON."""
    uid = current_user()['id']
    rows = list_generated_thumbnails(uid, limit=100)
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "youtube_url": r["youtube_url"],
            "video_id": r["video_id"],
            "title": r["title"],
            "channel": r["channel"],
            "aspect": r["aspect"],
            "style": r["style"],
            "prompt": r["prompt"],
            "image_url": f"/{r['image_relpath']}" if r["image_relpath"] else None,
            "created_at": r["created_at"],
        })
    return jsonify({"success": True, "thumbnails": items})

@app.route('/thumbnails/saved/<int:thumb_id>', methods=['DELETE'])
def thumbnails_saved_delete(thumb_id: int):
    """Delete a saved thumbnail (DB row + file on disk)."""
    uid = current_user()['id']
    row = get_generated_thumbnail(thumb_id, uid)
    if not row:
        return jsonify({"error": "Thumbnail not found"}), 404

    if row["image_relpath"]:
        try:
            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), row["image_relpath"])
            if os.path.isfile(filepath):
                os.remove(filepath)
        except OSError:
            app.logger.warning("Could not delete thumbnail file: %s", row["image_relpath"])

    delete_generated_thumbnail(thumb_id, uid)
    return jsonify({"success": True})

def _backfill_youtube_channels_bg():
    """One-time background task to populate channel for existing YouTube episodes."""
    episodes = get_youtube_episodes_missing_channel()
    if not episodes:
        return
    app.logger.info("Backfilling channel for %d YouTube episodes", len(episodes))
    for ep in episodes:
        try:
            meta = fetch_youtube_metadata(ep["url"])
            channel = meta.get("channel") or None
            if channel:
                set_episode_channel(ep["id"], channel)
        except Exception:
            continue
    app.logger.info("YouTube channel backfill complete")

# ── Content Agent (agentic content preparation) ──────────────────────────────

def _parse_brief_form(form) -> dict:
    """Extract + validate content-brief fields from a POST form."""
    def _b(name: str) -> bool:
        return form.get(name, 'false').lower() in ('true', '1', 'yes', 'on')

    keywords = [k.strip() for k in re.split(r'[\n,]', form.get('must_include_keywords', '')) if k.strip()]
    focus = [s.strip() for s in re.split(r'[\n,]', form.get('focus_sources', '')) if s.strip()]
    run_days = [int(x) for x in form.getlist('run_days') if x.isdigit()]
    ppp = form.get('posts_per_platform', 3, type=int) or 3
    return {
        'name': (form.get('name') or '').strip() or 'Untitled brief',
        'instructions': (form.get('instructions') or '').strip(),
        'content_type': form.get('content_type', 'posts'),
        'platforms': form.getlist('platforms'),
        'tone': form.get('tone', 'professional'),
        'posts_per_platform': max(1, min(ppp, 10)),
        'article_count': max(0, form.get('article_count', 0, type=int) or 0),
        'article_style': form.get('article_style', 'blog'),
        'focus_sources': focus,
        'must_include_keywords': keywords,
        'audience_persona': (form.get('audience_persona') or '').strip() or None,
        'use_web_search': _b('use_web_search'),
        'use_saved_sources': _b('use_saved_sources'),
        'cadence': form.get('cadence', 'manual'),
        'run_time': (form.get('run_time') or '').strip() or None,
        'run_days': run_days,
        'auto_queue': _b('auto_queue'),
        'review_window_hours': max(0, form.get('review_window_hours', 24, type=int) or 24),
        'max_sources_per_run': max(1, form.get('max_sources_per_run', 5, type=int) or 5),
        'max_cost_cny': max(0.0, form.get('max_cost_cny', 0.5, type=float) or 0.5),
        'max_drafts_per_run': max(1, form.get('max_drafts_per_run', 30, type=int) or 30),
    }

@app.route('/briefs')
def briefs_page():
    """List content briefs with their latest run status."""
    uid = current_user()['id']
    briefs = []
    for row in list_content_briefs(uid):
        d = dict(row)
        try:
            d['platforms_list'] = json.loads(d.get('platforms') or '[]')
        except Exception:
            d['platforms_list'] = []
        try:
            d['keywords_list'] = json.loads(d.get('must_include_keywords') or '[]')
        except Exception:
            d['keywords_list'] = []
        try:
            d['focus_list'] = json.loads(d.get('focus_sources') or '[]')
        except Exception:
            d['focus_list'] = []
        try:
            d['run_days_list'] = json.loads(d.get('run_days') or '[]')
        except Exception:
            d['run_days_list'] = []
        runs = list_brief_runs(d['id'], uid, limit=1)
        d['last_run'] = dict(runs[0]) if runs else None
        briefs.append(d)
    return render_template('briefs.html', briefs=briefs)

@app.route('/briefs/create', methods=['POST'])
def brief_create():
    uid = current_user()['id']
    data = _parse_brief_form(request.form)
    if not data['instructions']:
        return jsonify({"error": "Instructions (the prompt) are required"}), 400
    # Stamp the creator so the brief's AI usage (scheduled or manual) is
    # attributed to them on the /usage page.
    data['user_id'] = uid
    brief_id = create_content_brief(**data)
    if data['cadence'] != 'manual':
        set_content_brief_schedule(brief_id, uid, next_run_at=_compute_next_run_at(get_content_brief(brief_id, uid)))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "id": brief_id})
    return redirect(url_for('briefs_page'))

@app.route('/briefs/<int:brief_id>/edit', methods=['POST'])
def brief_edit(brief_id):
    uid = current_user()['id']
    if not get_content_brief(brief_id, uid):
        return jsonify({"error": "Brief not found"}), 404
    data = _parse_brief_form(request.form)
    update_content_brief(brief_id, uid, **data)
    next_at = _compute_next_run_at(get_content_brief(brief_id, uid)) if data['cadence'] != 'manual' else None
    set_content_brief_schedule(brief_id, uid, next_run_at=next_at)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "id": brief_id})
    return redirect(url_for('briefs_page'))

@app.route('/briefs/<int:brief_id>/delete', methods=['POST'])
def brief_delete(brief_id):
    uid = current_user()['id']
    if not get_content_brief(brief_id, uid):
        return jsonify({"error": "Brief not found"}), 404
    delete_content_brief(brief_id, uid)
    return jsonify({"success": True})

@app.route('/briefs/<int:brief_id>/toggle', methods=['POST'])
def brief_toggle(brief_id):
    uid = current_user()['id']
    brief = get_content_brief(brief_id, uid)
    if not brief:
        return jsonify({"error": "Brief not found"}), 404
    enabled = request.form.get('enabled', 'true').lower() in ('1', 'true', 'yes', 'on')
    set_content_brief_enabled(brief_id, uid, enabled)
    if enabled and (dict(brief).get('cadence') or 'manual') != 'manual':
        set_content_brief_schedule(brief_id, uid, next_run_at=_compute_next_run_at(brief))
    return jsonify({"success": True, "enabled": enabled})

@app.route('/briefs/<int:brief_id>/run', methods=['POST'])
def brief_run_now(brief_id):
    """Kick off an on-demand run in a background thread; UI polls for status."""
    uid = current_user()['id']
    if not get_content_brief(brief_id, uid):
        return jsonify({"error": "Brief not found"}), 404
    if get_active_brief_run(brief_id, uid):
        return jsonify({"error": "A run is already in progress for this brief"}), 409
    # The runner is the brief's owner: the list is already scoped, so a user
    # can only ever start runs on their own briefs.
    run_id = create_brief_run(brief_id, uid, trigger='manual')
    run_lang = g.lang  # read in request context; the background thread has none
    runner = current_user()  # same: captured here so usage is attributed to them

    def _bg():
        with usage_meter.usage_context("proactive"), \
                usage_meter.user_context(runner["id"], runner["username"]):
            try:
                content_agent.run_brief(brief_id, trigger='manual', run_id=run_id,
                                        lang=run_lang)
            except Exception:
                app.logger.exception("Manual brief run %s failed", brief_id)
                try:
                    finalize_brief_run(run_id, uid, status='error', error_message='unhandled')
                except Exception:
                    pass

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"success": True, "run_id": run_id, "status": "running"})

@app.route('/briefs/<int:brief_id>/runs')
def brief_runs(brief_id):
    uid = current_user()['id']
    if not get_content_brief(brief_id, uid):
        return jsonify({"error": "Brief not found"}), 404
    return jsonify({"runs": [dict(r) for r in list_brief_runs(brief_id, uid, limit=20)]})

@app.route('/briefs/runs/<int:run_id>')
def brief_run_status(run_id):
    uid = current_user()['id']
    run = get_brief_run(run_id, uid)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    return jsonify(dict(run))

def _compute_next_run_at(brief) -> "str | None":
    """Next local ISO run time for a recurring brief, or None for manual/paused.

    ``run_days`` uses Python weekday ints (Monday=0 .. Sunday=6). Local time is
    used throughout to stay consistent with get_next_available_slot.
    """
    d = dict(brief)
    cadence = (d.get("cadence") or "manual").lower()
    if cadence == "manual":
        return None
    try:
        hh, mm = (int(x) for x in (d.get("run_time") or "09:00").split(":")[:2])
    except Exception:
        hh, mm = 9, 0
    now = datetime.now()
    if cadence == "daily":
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")
    if cadence == "weekly":
        try:
            days = [int(x) for x in json.loads(d.get("run_days") or "[]") if 0 <= int(x) <= 6]
        except Exception:
            days = []
        if not days:
            days = [now.weekday()]
        for offset in range(0, 8):
            cand = (now + timedelta(days=offset)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand.weekday() in days and cand > now:
                return cand.isoformat(timespec="seconds")
        return (now + timedelta(days=7)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        ).isoformat(timespec="seconds")
    return None

def content_agent_worker() -> None:
    """Poll for due content briefs and run them; usage metered as proactive.

    A resilient sleep loop that never dies on a
    single brief's failure. A per-brief active-run guard prevents a scheduled run
    from overlapping an in-flight manual run.
    """
    tick = int(os.getenv("CONTENT_AGENT_TICK_SECONDS", "60") or 60)
    with usage_meter.usage_context("proactive"):
        while True:
            try:
                time.sleep(tick)
                now_iso = datetime.now().isoformat(timespec="seconds")
                for brief in get_due_content_briefs(now_iso):
                    bid = brief["id"]
                    owner_id = brief["user_id"]
                    if owner_id is None:
                        # Defensive: legacy ownerless briefs cannot produce an
                        # attributed run (every content write is owner-scoped).
                        # Log and skip; the row stays invisible to all users.
                        app.logger.error(
                            "Scheduled brief %s has no owner; run skipped", bid)
                        continue
                    if get_active_brief_run(bid, owner_id):
                        continue  # a manual run is already in flight
                    run_id = create_brief_run(bid, owner_id, trigger="scheduled")
                    # Attribute this brief's usage to its owner; legacy rows with
                    # a NULL user_id resolve to no override (events stay NULL).
                    owner = get_user_by_id(owner_id) if owner_id is not None else None
                    try:
                        with usage_meter.user_context(
                            owner["id"] if owner else None,
                            owner["username"] if owner else None,
                        ):
                            content_agent.run_brief(bid, trigger="scheduled", run_id=run_id)
                    except Exception:
                        app.logger.exception("Scheduled brief %s failed", bid)
                    finally:
                        run = get_brief_run(run_id, owner_id)
                        status = dict(run)["status"] if run else "error"
                        set_content_brief_schedule(
                            bid, owner_id, next_run_at=_compute_next_run_at(brief),
                            last_run_at=now_iso, last_run_status=status,
                        )
            except Exception:
                app.logger.exception("content_agent_worker loop error")

def start_workers():
    """Start all background worker threads."""
    # Episode processing worker (usage metered as proactive/background)
    episode_worker = threading.Thread(target=_proactive_worker, daemon=True)
    episode_worker.start()
    app.logger.info("Episode processing worker started")

    # Content agent worker (recurring briefs). On-demand runs work even if disabled.
    if os.getenv("CONTENT_AGENT_ENABLED", "true").lower() in ("1", "true", "yes"):
        content_worker = threading.Thread(target=content_agent_worker, daemon=True)
        content_worker.start()
        app.logger.info("Content agent worker started")

    # One-time backfill for YouTube channel names
    backfill_thread = threading.Thread(target=_backfill_youtube_channels_bg, daemon=True)
    backfill_thread.start()

if __name__ == '__main__':
    # In debug mode with reloader, Flask spawns two processes:
    # - Parent process (reloader): WERKZEUG_RUN_MAIN is NOT set
    # - Child process (actual server): WERKZEUG_RUN_MAIN='true'
    # We only want ONE set of workers to avoid duplicate posts
    #
    # WERKZEUG_RUN_MAIN will be:
    # - 'true' in the child process (actual server) when using reloader
    # - Not set in the parent process (reloader)
    # - Not set when running without reloader (production)
    
    use_reloader = True  # Set to False for production
    
    if use_reloader:
        # Only start workers in the child process (not the reloader parent)
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            start_workers()
    else:
        # No reloader, just start the workers
        start_workers()
    
    # Run the Flask app
    app.run(debug=use_reloader, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), use_reloader=use_reloader)
