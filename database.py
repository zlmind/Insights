"""Simple SQLite helpers for Insights.

This module abstracts the small SQLite database used by both the CLI and the
web interface. Each function wraps a query so callers don't need to know SQL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from typing import Dict, Iterable, Optional, List
from datetime import datetime, timedelta

DB_PATH = "insights.db"

# Rebuild DDLs for the tables born with `url TEXT UNIQUE`: column for
# column the same as their CREATE TABLE in init_db (user_id included, no
# UNIQUE on url), only created under a temporary _new name by the migration.
_NEW_DDL = {
    "feeds": """
        CREATE TABLE feeds_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT,
            feed_type TEXT,
            last_post TEXT,
            item_count INTEGER,
            last_checked TEXT,
            user_id INTEGER REFERENCES users(id)
        )
    """,
    "episodes": """
        CREATE TABLE episodes_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id INTEGER,
            url TEXT,
            title TEXT,
            transcript TEXT,
            summary TEXT,
            action_items TEXT,
            status TEXT,
            published TEXT,
            processed_at TEXT,
            channel TEXT,
            user_id INTEGER REFERENCES users(id),
            FOREIGN KEY(feed_id) REFERENCES feeds(id)
        )
    """,
    "url_sources": """
        CREATE TABLE url_sources_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT,
            description TEXT,
            content TEXT,
            og_image TEXT,
            created_at TEXT,
            last_used_at TEXT,
            user_id INTEGER REFERENCES users(id)
        )
    """,
    "uploaded_images": """
        CREATE TABLE uploaded_images_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            url TEXT,
            storage TEXT,
            size INTEGER,
            created_at TEXT,
            media_type TEXT DEFAULT 'image',
            user_id INTEGER REFERENCES users(id)
        )
    """,
}

# Explicit column lists for the copy step — named, not SELECT *, so the copy
# is independent of physical column order.
_COPY_COLS = {
    "feeds": "id, url, title, feed_type, last_post, item_count, last_checked, user_id",
    "episodes": ("id, feed_id, url, title, transcript, summary, action_items, "
                 "status, published, processed_at, channel, user_id"),
    "url_sources": ("id, url, title, description, content, og_image, "
                    "created_at, last_used_at, user_id"),
    "uploaded_images": ("id, filename, url, storage, size, created_at, "
                        "media_type, user_id"),
}

def init_db(db_path: str = DB_PATH) -> None:
    """Create tables if the database file is empty."""
    with sqlite3.connect(db_path) as conn:
        # Table storing RSS feeds that users have added
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                feed_type TEXT,
                last_post TEXT,
                item_count INTEGER,
                last_checked TEXT,
                user_id INTEGER REFERENCES users(id)
            )
            """
        )
        # Upgrade existing feeds table with new columns
        cur = conn.execute("PRAGMA table_info(feeds)")
        columns = [row[1] for row in cur.fetchall()]
        if "feed_type" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN feed_type TEXT")
        if "last_post" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_post TEXT")
        if "item_count" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN item_count INTEGER")
        if "last_checked" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_checked TEXT")
        # Each processed episode is stored here along with its state
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER,
                url TEXT,
                title TEXT,
                transcript TEXT,
                summary TEXT,
                action_items TEXT,
                status TEXT,
                published TEXT,
                processed_at TEXT,
                user_id INTEGER REFERENCES users(id),
                FOREIGN KEY(feed_id) REFERENCES feeds(id)
            )
            """
        )
        # Generated articles are stored here
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER,
                topic TEXT,
                style TEXT,
                content TEXT,
                created_at TEXT,
                FOREIGN KEY(episode_id) REFERENCES episodes(id)
            )
            """
        )
        # Social media posts generated for articles
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                platform TEXT,
                content TEXT,
                image_url TEXT,
                created_at TEXT,
                used INTEGER DEFAULT 0,
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )
        # Upgrade social_posts table to include image_url if missing
        cur = conn.execute("PRAGMA table_info(social_posts)")
        social_columns = [row[1] for row in cur.fetchall()]
        if "image_url" not in social_columns:
            conn.execute("ALTER TABLE social_posts ADD COLUMN image_url TEXT")
        # Scheduled posts queue (posting slots; server-side publishing retired)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                social_post_id INTEGER,
                article_id INTEGER,
                standalone_post_id INTEGER,
                post_type TEXT,
                platform TEXT DEFAULT 'linkedin',
                scheduled_for TEXT,
                status TEXT DEFAULT 'pending',
                linkedin_post_urn TEXT,
                error_message TEXT,
                created_at TEXT,
                posted_at TEXT,
                retry_count INTEGER DEFAULT 0,
                FOREIGN KEY(social_post_id) REFERENCES social_posts(id),
                FOREIGN KEY(article_id) REFERENCES articles(id),
                FOREIGN KEY(standalone_post_id) REFERENCES standalone_posts(id)
            )
            """
        )
        # Upgrade scheduled_posts table to include missing columns
        cur = conn.execute("PRAGMA table_info(scheduled_posts)")
        sched_columns = [row[1] for row in cur.fetchall()]
        if "standalone_post_id" not in sched_columns:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN standalone_post_id INTEGER")
        if "retry_count" not in sched_columns:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN retry_count INTEGER DEFAULT 0")
        if "account_id" not in sched_columns:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN account_id INTEGER")
        # Schedule settings for configurable time slots
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_at TEXT
            )
            """
        )
        # Time slots for queue-based scheduling
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_time_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_of_week INTEGER,
                time_slot TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT
            )
            """
        )
        # Platform assignments for time slots (many-to-many)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS time_slot_platforms (
                slot_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                PRIMARY KEY (slot_id, platform),
                FOREIGN KEY(slot_id) REFERENCES schedule_time_slots(id) ON DELETE CASCADE
            )
            """
        )
        # Platform daily posting limits
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_daily_limits (
                platform TEXT PRIMARY KEY,
                max_posts_per_day INTEGER DEFAULT 0
            )
            """
        )
        # Standalone posts for the Command Center (not tied to articles)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS standalone_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT,
                source_content TEXT,
                platform TEXT,
                content TEXT,
                image_url TEXT,
                created_at TEXT,
                used INTEGER DEFAULT 0,
                repost INTEGER DEFAULT 0
            )
            """
        )
        # Upgrade standalone_posts table to include newer columns if missing
        cur = conn.execute("PRAGMA table_info(standalone_posts)")
        standalone_columns = [row[1] for row in cur.fetchall()]
        if "image_url" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN image_url TEXT")
        if "repost" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN repost INTEGER DEFAULT 0")
        # Retired Instagram-only columns are still migrated in so a pre-existing
        # database keeps its rows readable; the domestic compose flow only writes
        # image_url / video_url on a post.
        if "ig_post_type" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN ig_post_type TEXT")
        if "media_items" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN media_items TEXT")
        if "ig_user_tags" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN ig_user_tags TEXT")
        # video_url is the post's one video, card-wide like image_url: the rows of
        # a card share it.
        if "video_url" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN video_url TEXT")
        if "ig_reel_options" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN ig_reel_options TEXT")
        # ig_collaborators / ig_reel_tags: retired Instagram people fields, kept
        # for legacy rows only.
        if "ig_collaborators" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN ig_collaborators TEXT")
        if "ig_reel_tags" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN ig_reel_tags TEXT")
        if "account_id" not in standalone_columns:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN account_id INTEGER")
        # A Reel's collaborators were first kept inside ig_reel_options. They now share
        # ig_collaborators with every other format, so one list is what Compose shows
        # and what publishes. Move any still in the old place; a row is touched only
        # while it holds them there, so running this again changes nothing.
        for row_id, raw_options, current in conn.execute(
            "SELECT id, ig_reel_options, ig_collaborators FROM standalone_posts "
            "WHERE ig_reel_options LIKE '%collaborators%'"
        ).fetchall():
            try:
                options = json.loads(raw_options)
            except (TypeError, ValueError):
                continue
            if not isinstance(options, dict) or "collaborators" not in options:
                continue
            moved = options.pop("collaborators")
            names = list(dict.fromkeys(
                str(name).strip().lstrip("@").lower()
                for name in (moved if isinstance(moved, list) else []) if str(name).strip()
            ))
            conn.execute(
                "UPDATE standalone_posts SET ig_collaborators = ?, ig_reel_options = ? WHERE id = ?",
                (current or (json.dumps(names) if names else None),
                 json.dumps(options) if options else None, row_id),
            )
        # URL sources - stores extracted content from URLs for reuse
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS url_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                description TEXT,
                content TEXT,
                og_image TEXT,
                created_at TEXT,
                last_used_at TEXT,
                user_id INTEGER REFERENCES users(id)
            )
            """
        )
        # Uploaded images library - tracks images uploaded to Cloudinary or
        # locally. url is NOT globally unique: the same stock-image link can
        # belong to two users as independent rows; add_uploaded_image dedupes
        # per user on (url, user_id).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                url TEXT,
                storage TEXT,
                size INTEGER,
                created_at TEXT,
                media_type TEXT DEFAULT 'image',
                user_id INTEGER REFERENCES users(id)
            )
            """
        )
        # Upgrade uploaded_images to track media type (image vs video) if missing
        cur = conn.execute("PRAGMA table_info(uploaded_images)")
        uploaded_images_columns = [row[1] for row in cur.fetchall()]
        if "media_type" not in uploaded_images_columns:
            conn.execute("ALTER TABLE uploaded_images ADD COLUMN media_type TEXT DEFAULT 'image'")
        # Prompt library - curated, named prompts the user can reuse anywhere
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        # Generated YouTube thumbnails
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_thumbnails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_url TEXT,
                video_id TEXT,
                title TEXT,
                channel TEXT,
                aspect TEXT,
                style TEXT,
                prompt TEXT,
                image_relpath TEXT,
                created_at TEXT
            )
            """
        )
        # Application users for login authentication
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP
            )
            """
        )
        # Existing databases upgrade in place; the first account becomes the
        # admin so a deployed instance keeps working after the update.
        cur = conn.execute("PRAGMA table_info(users)")
        user_columns = [row[1] for row in cur.fetchall()]
        if user_columns and "is_admin" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        # Audit log of mutating actions and named auth events
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                method TEXT,
                path TEXT,
                endpoint TEXT,
                target TEXT,
                status_code INTEGER,
                ip TEXT,
                user_agent TEXT,
                duration_ms INTEGER,
                details TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_log_ts ON activity_log(ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_log_user ON activity_log(user_id, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_log_action ON activity_log(action, ts DESC)"
        )
        # AI usage metering: one row per paid (or local) generation call, with
        # tokens + estimated cost and whether it ran proactively or reactively.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL,
                category TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                audio_seconds REAL NOT NULL DEFAULT 0,
                images INTEGER NOT NULL DEFAULT 0,
                cost_cny REAL NOT NULL DEFAULT 0,
                user_id INTEGER,
                username TEXT,
                details TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_mode ON usage_events(mode, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_category ON usage_events(category, ts DESC)"
        )
        # Content briefs: reusable "content requests" the agent runs to procure
        # source material and prepare draft posts/articles for review.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                instructions TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'posts',
                platforms TEXT,
                tone TEXT DEFAULT 'professional',
                posts_per_platform INTEGER DEFAULT 3,
                article_count INTEGER DEFAULT 0,
                article_style TEXT DEFAULT 'blog',
                focus_sources TEXT,
                must_include_keywords TEXT,
                audience_persona TEXT,
                use_web_search INTEGER DEFAULT 1,
                use_saved_sources INTEGER DEFAULT 1,
                cadence TEXT NOT NULL DEFAULT 'manual',
                run_time TEXT,
                run_days TEXT,
                next_run_at TEXT,
                enabled INTEGER DEFAULT 1,
                auto_queue INTEGER DEFAULT 0,
                review_window_hours INTEGER DEFAULT 24,
                max_sources_per_run INTEGER DEFAULT 5,
                max_cost_cny REAL DEFAULT 0.5,
                max_drafts_per_run INTEGER DEFAULT 30,
                last_run_at TEXT,
                last_run_status TEXT,
                created_at TEXT,
                updated_at TEXT,
                user_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        # Per-run history / live status for content brief executions.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content_brief_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL,
                trigger TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT,
                finished_at TEXT,
                sources_found INTEGER DEFAULT 0,
                sources_used INTEGER DEFAULT 0,
                posts_created INTEGER DEFAULT 0,
                articles_created INTEGER DEFAULT 0,
                cost_cny REAL DEFAULT 0,
                error_message TEXT,
                log TEXT,
                FOREIGN KEY(brief_id) REFERENCES content_briefs(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_brief_runs_brief ON content_brief_runs(brief_id, started_at DESC)"
        )
        # Link agent-generated drafts back to the brief/run that produced them.
        cur = conn.execute("PRAGMA table_info(standalone_posts)")
        sp_cols = [row[1] for row in cur.fetchall()]
        if "brief_id" not in sp_cols:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN brief_id INTEGER")
        if "brief_run_id" not in sp_cols:
            conn.execute("ALTER TABLE standalone_posts ADD COLUMN brief_run_id INTEGER")
        cur = conn.execute("PRAGMA table_info(articles)")
        art_cols = [row[1] for row in cur.fetchall()]
        if "brief_id" not in art_cols:
            conn.execute("ALTER TABLE articles ADD COLUMN brief_id INTEGER")
        if "brief_run_id" not in art_cols:
            conn.execute("ALTER TABLE articles ADD COLUMN brief_run_id INTEGER")
        if "source_type" not in art_cols:
            conn.execute("ALTER TABLE articles ADD COLUMN source_type TEXT")
        # ── Content Library ────────────────────────────────────────────────
        # Catalogue of an on-disk media archive, sorted by year and category.
        # A "root" is a folder the user has registered: either a source archive
        # to classify, or a hand-sorted folder whose subfolder names supply the
        # taxonomy.

        # Upgrade any existing DB with newer columns
        cur = conn.execute("PRAGMA table_info(episodes)")
        columns = [row[1] for row in cur.fetchall()]
        if "status" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN status TEXT")
            conn.execute("UPDATE episodes SET status = 'complete'")
        if "published" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN published TEXT")
        if "processed_at" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN processed_at TEXT")
        if "channel" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN channel TEXT")
        # Usage costs switched from USD to CNY estimates: same numbers going
        # forward, honest column names. Brief budgets convert at 7.2 so an old
        # dollar cap keeps guarding the same amount of real spend.
        for table in ("usage_events", "content_brief_runs"):
            cur = conn.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]
            if "cost_usd" in cols:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN cost_usd TO cost_cny")
        cur = conn.execute("PRAGMA table_info(content_briefs)")
        cols = [row[1] for row in cur.fetchall()]
        if "max_cost_usd" in cols:
            conn.execute("ALTER TABLE content_briefs RENAME COLUMN max_cost_usd TO max_cost_cny")
            conn.execute("UPDATE content_briefs SET max_cost_cny = ROUND(max_cost_cny * 7.2, 4)")
        # ------------------------------------------------------------------
        # Connected social accounts
        #
        # Every platform used to hold exactly one login: each *_tokens table was
        # read with "LIMIT 1" and written with an upsert, so connecting a second
        # login of the same platform overwrote the first. social_accounts is the
        # identity those
        # token rows now hang off, one row per (platform, external_id), which is
        # what lets a platform carry several logins at once.
        #
        # Everything that publishes keys on an account id rather than a bare
        # platform name: a saved post, a queue entry and a token all name the
        # account they belong to. account_id is left nullable throughout so rows
        # written before this existed still resolve: a NULL means "whichever
        # account is that platform's default", decided at publish time.
        # ------------------------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                external_id TEXT,
                label TEXT,
                handle TEXT,
                display_name TEXT,
                avatar_url TEXT,
                is_default INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                user_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        # Ownership for connected accounts: an account belongs to the user who
        # connected it, so the same platform identity can exist once per user.
        # An upgraded database gets the column in place and its pre-user rows
        # settle with the oldest account (admin) — unlike the content tables,
        # a login nobody owns is a login nobody can see or re-connect.
        acct_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(social_accounts)").fetchall()]
        if acct_cols and "user_id" not in acct_cols:
            conn.execute("ALTER TABLE social_accounts ADD COLUMN user_id INTEGER")
        # The old key was (platform, external_id); it must go before the new
        # one arrives, or the widened key never takes effect.
        conn.execute("DROP INDEX IF EXISTS idx_social_accounts_identity")
        for stmt in (
            # One row per login per user. external_id is the platform's own id
            # for the account, so re-running OAuth for an account a user already
            # connected updates it instead of adding a duplicate.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_identity_user "
            "ON social_accounts(platform, external_id, user_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_accounts_platform "
            "ON social_accounts(platform, is_default DESC, id)",
        ):
            conn.execute(stmt)
        conn.execute(
            """
            UPDATE social_accounts SET user_id = (SELECT MIN(id) FROM users)
            WHERE user_id IS NULL AND EXISTS (SELECT 1 FROM users)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'article_sync',
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                run_after TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                result TEXT,
                claimed_by TEXT,
                claimed_at TEXT,
                article_id INTEGER,
                created_by INTEGER,
                user_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_jobs_queue "
            "ON agent_jobs(status, run_after, id)"
        )

        # Ownership for agent jobs: every job belongs to the user whose token
        # enqueued it, so claim/report/read can be scoped. An upgraded
        # database gets the column in place; its pre-user rows are assigned
        # to the oldest account — a NULL owner is a job nobody can claim or
        # read, which is a silent stall, not a neutral state.
        job_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(agent_jobs)").fetchall()]
        if job_cols and "user_id" not in job_cols:
            conn.execute("ALTER TABLE agent_jobs ADD COLUMN user_id INTEGER")
        conn.execute(
            """
            UPDATE agent_jobs SET user_id = (SELECT MIN(id) FROM users)
            WHERE user_id IS NULL AND EXISTS (SELECT 1 FROM users)
            """
        )

        # Per-user agent tokens for the /agent/* door and browser extension.
        # Plaintext tokens are never stored; only sha256 hashes land here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                label TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_seen TEXT,
                revoked INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tokens_hash "
            "ON agent_tokens(token_hash)"
        )

        # An instance upgraded from before admins existed has no admin yet;
        # the oldest account is the one that set the instance up.
        if user_columns:
            admin = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1"
            ).fetchone()[0]
            if not admin:
                conn.execute(
                    "UPDATE users SET is_admin = 1 "
                    "WHERE id = (SELECT MIN(id) FROM users)"
                )

        # ── Multi-user content scoping: nullable ownership on every content table ──
        # Rows written before users existed stay ownerless (user_id NULL);
        # nothing is backfilled.
        _CONTENT_TABLES = ("feeds", "episodes", "articles", "social_posts",
                           "standalone_posts", "url_sources", "uploaded_images",
                           "generated_thumbnails", "prompt_library",
                           "content_brief_runs", "scheduled_posts")
        for table in _CONTENT_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if cols and "user_id" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")

        # feeds/episodes/url_sources were born with `url TEXT UNIQUE` — a
        # table-level constraint SQLite cannot drop (its autoindex has no
        # name). Rebuild each table by the official 12-step recipe when the
        # old definition is still in sqlite_master; the composite unique
        # indexes below then let two users hold the same URL as independent
        # rows. Rows carry over untouched, user_id stays NULL. Create-new,
        # copy, drop-old, rename is the order that keeps other tables'
        # FOREIGN KEY references (episodes→feeds, articles→episodes) pointing
        # at the real name throughout — a plain RENAME of the old table would
        # rewrite those references onto the legacy name and leave them
        # dangling.
        # The rebuild below assumes connection-level PRAGMA foreign_keys stays OFF (project convention: it has never been enabled); if FKs are ever turned on, run `PRAGMA foreign_keys=OFF` before this migration.
        for table in ("feeds", "episodes", "url_sources", "uploaded_images"):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if not row or not re.search(r"url\s+TEXT\s+UNIQUE", row[0] or "", re.I):
                continue
            # One transaction per table: a crash between the DROP of the old
            # table and the RENAME must roll the whole rebuild back, not leave
            # an empty table where the data used to be. The connection may
            # already carry an outer transaction (the is_admin backfill above
            # opens one), and a SAVEPOINT behaves the same either way — with
            # no outer transaction its RELEASE commits, otherwise it only
            # collapses into the enclosing one.
            sp = f"{table}_rebuild"
            conn.execute(f"SAVEPOINT {sp}")
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table}_new")
                conn.execute(_NEW_DDL[table])
                conn.execute(f"INSERT INTO {table}_new ({_COPY_COLS[table]}) "
                             f"SELECT {_COPY_COLS[table]} FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
                conn.execute(f"RELEASE {sp}")
            except BaseException:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
                raise

        for stmt in (
            # Same URL twice only across users, never within one.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_feeds_url_user "
            "ON feeds(url, user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_url_user "
            "ON episodes(url, user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_url_sources_url_user "
            "ON url_sources(url, user_id)",
            # Per-user listing on the busiest tables.
            "CREATE INDEX IF NOT EXISTS idx_articles_user ON articles(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_standalone_posts_user "
            "ON standalone_posts(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id)",
        ):
            conn.execute(stmt)

        # Instance-wide settings set from the /settings page by an admin.
        # Reads fall back to environment variables, so an existing .env keeps
        # working until the same key is saved here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # One-time migration: a legacy global AGENT_TOKEN becomes the admin's
        # first per-user token.  It is idempotent: re-running only inserts when
        # the hash is absent.
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", ("AGENT_TOKEN",)
        ).fetchone()
        legacy_token = (row[0] if row else None) or os.environ.get("AGENT_TOKEN")
        if legacy_token:
            admin_row = conn.execute("SELECT MIN(id) FROM users").fetchone()
            admin_id = admin_row[0] if admin_row else None
            if admin_id is not None:
                token_hash = hash_agent_token(legacy_token)
                existing = conn.execute(
                    "SELECT 1 FROM agent_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if not existing:
                    now = datetime.utcnow().isoformat(timespec="seconds")
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO agent_tokens
                            (token_hash, user_id, label, created_at, revoked)
                        VALUES (?, ?, ?, ?, 0)
                        """,
                        (token_hash, admin_id, "存量全局密钥", now),
                    )

        conn.commit()

# ── Instance settings (admin-editable replacements for .env entries) ──────

def get_setting(key: str, default: Optional[str] = None,
                db_path: str = DB_PATH) -> Optional[str]:
    """The saved value for one setting, or the default when it is not set."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else default

def set_setting(key: str, value: str, updated_by: Optional[int] = None,
                db_path: str = DB_PATH) -> None:
    """Save one setting, recording which admin saved it."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_by, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (key, value, updated_by),
        )
        conn.commit()

def delete_setting(key: str, db_path: str = DB_PATH) -> None:
    """Forget a saved setting, falling the key back to its environment value."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        conn.commit()

def list_settings(db_path: str = DB_PATH) -> Dict[str, str]:
    """Every saved setting as a plain {key: value} mapping."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return dict(rows)

def _default_account_id(conn: sqlite3.Connection, platform: str):
    """The account new work for ``platform`` goes to, or None if none exist."""
    cur = conn.execute(
        "SELECT id FROM social_accounts WHERE platform = ? AND status != 'removed' "
        "ORDER BY is_default DESC, id LIMIT 1",
        (platform,),
    )
    row = cur.fetchone()
    return row[0] if row else None

def _upsert_account_row(
    conn: sqlite3.Connection,
    platform: str,
    external_id: str,
    display_name=None,
    handle=None,
    avatar_url=None,
    label=None,
    user_id=None,
    now=None,
) -> int:
    """Insert or refresh one account, returning its id.

    The first account a platform gets becomes its default; later ones do not
    steal that, so connecting a second login never silently redirects posts
    that were already going to the first. ``user_id`` is part of the row's
    identity: the same platform login can exist once per user.
    """
    now = now or datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.execute(
        "SELECT id FROM social_accounts "
        "WHERE platform = ? AND external_id = ? AND user_id IS ?",
        (platform, external_id, user_id),
    )
    existing = cur.fetchone()
    if existing:
        account_id = existing[0]
        # Only overwrite profile fields we were actually given: a callback that
        # skipped the profile endpoint must not blank out a known name.
        sets, params = ["updated_at = ?", "status = 'active'"], [now]
        for column, value in (
            ("display_name", display_name),
            ("handle", handle),
            ("avatar_url", avatar_url),
            ("label", label),
        ):
            if value:
                sets.insert(0, f"{column} = ?")
                params.insert(0, value)
        params.append(account_id)
        conn.execute(
            f"UPDATE social_accounts SET {', '.join(sets)} WHERE id = ?", params
        )
        return account_id

    holder = _default_account_id(conn, platform)
    if holder is None:
        is_default = 1
    else:
        holder_id = conn.execute(
            "SELECT external_id FROM social_accounts WHERE id = ?", (holder,)
        ).fetchone()[0]
        # A placeholder cannot publish anything, so it must not hold the default
        # against an account that can. Without this, connecting a real account
        # after an unidentified one would leave bare-platform posts aimed at the
        # one account that is guaranteed to fail.
        is_default = 1 if (
            str(holder_id or "").startswith("pending:")
            and not external_id.startswith("pending:")
        ) else 0
        if is_default:
            conn.execute(
                "UPDATE social_accounts SET is_default = 0 WHERE platform = ?",
                (platform,),
            )
    cur = conn.execute(
        """
        INSERT INTO social_accounts
            (platform, external_id, label, handle, display_name, avatar_url,
             is_default, status, user_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (platform, external_id, label, handle, display_name, avatar_url,
         is_default, user_id, now, now),
    )
    return cur.lastrowid

def _require_user(user_id) -> int:
    """Content rows are always owned: a missing user_id is a caller bug."""
    if user_id is None:
        raise ValueError("user_id is required for content operations")
    return int(user_id)

def get_feed(url: str, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a user's feed record by its RSS URL."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM feeds WHERE url = ? AND user_id = ?", (url, uid)
        )
        return cur.fetchone()

def get_feed_by_id(feed_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a user's feed row given its integer ``id``."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM feeds WHERE id = ? AND user_id = ?", (feed_id, uid)
        )
        return cur.fetchone()

def list_feeds(user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return a user's stored feeds ordered by title."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM feeds WHERE user_id = ? ORDER BY title", (uid,)
        )
        return cur.fetchall()

def delete_feed(feed_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a user's feed and all associated episodes and articles."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # Delete articles for this user's episodes in this feed
        conn.execute(
            """
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes WHERE feed_id = ? AND user_id = ?
            )
            """,
            (feed_id, uid),
        )
        # Delete this user's episodes for this feed
        conn.execute(
            "DELETE FROM episodes WHERE feed_id = ? AND user_id = ?", (feed_id, uid)
        )
        # Delete the feed itself
        conn.execute("DELETE FROM feeds WHERE id = ? AND user_id = ?", (feed_id, uid))
        conn.commit()

def delete_feeds_bulk(feed_ids: List[int], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple of a user's feeds and all their associated data. Returns count deleted."""
    uid = _require_user(user_id)
    if not feed_ids:
        return 0
    placeholders = ",".join("?" * len(feed_ids))
    with sqlite3.connect(db_path) as conn:
        # Delete articles for this user's episodes in these feeds
        conn.execute(
            f"""
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes
                WHERE feed_id IN ({placeholders}) AND user_id = ?
            )
            """,
            (*feed_ids, uid),
        )
        # Delete this user's episodes for these feeds
        conn.execute(
            f"DELETE FROM episodes WHERE feed_id IN ({placeholders}) AND user_id = ?",
            (*feed_ids, uid),
        )
        # Delete the feeds themselves
        cur = conn.execute(
            f"DELETE FROM feeds WHERE id IN ({placeholders}) AND user_id = ?",
            (*feed_ids, uid),
        )
        conn.commit()
        return cur.rowcount

def add_feed(url: str, title: str, user_id: int, db_path: str = DB_PATH) -> int:
    """Insert a new feed for a user if needed and return its ``id``."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # ``INSERT OR IGNORE`` lets us call this repeatedly with the same URL;
        # the (url, user_id) unique key keeps each user's subscription separate
        cur = conn.execute(
            "INSERT OR IGNORE INTO feeds (url, title, user_id) VALUES (?, ?, ?)",
            (url, title, uid),
        )
        # ``rowcount`` will be >0 when a new row was inserted
        if cur.rowcount:
            feed_id = cur.lastrowid
        else:
            cur = conn.execute(
                "SELECT id FROM feeds WHERE url = ? AND user_id = ?", (url, uid)
            )
            feed_id = cur.fetchone()[0]
        conn.commit()
        return feed_id

def update_feed_metadata(
    feed_id: int,
    feed_type: str,
    last_post: str | None,
    item_count: int,
    user_id: int,
    db_path: str = DB_PATH,
) -> None:
    """Update cached metadata for a user's feed."""
    uid = _require_user(user_id)
    from datetime import datetime
    last_checked = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE feeds
            SET feed_type = ?, last_post = ?, item_count = ?, last_checked = ?
            WHERE id = ? AND user_id = ?
            """,
            (feed_type, last_post, item_count, last_checked, feed_id, uid),
        )
        conn.commit()

def get_episode(url: str, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a user's processed episode by its audio URL."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM episodes WHERE url = ? AND user_id = ?", (url, uid)
        )
        return cur.fetchone()

def save_episode(
    url: str,
    title: str,
    transcript: str,
    summary: str,
    action_items: Iterable[str],
    feed_id: int,
    published: str | None = None,
    channel: str | None = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> None:
    """Persist a fully processed episode for a user."""
    uid = _require_user(user_id)
    actions = "\n".join(action_items)
    processed_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO episodes
                (url, title, transcript, summary, action_items, feed_id, status, published, processed_at, channel, user_id)
            VALUES (?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?, ?)
            """,
            (url, title, transcript, summary, actions, feed_id, published, processed_at, channel, uid),
        )
        conn.commit()

def queue_episode(
    url: str,
    title: str,
    feed_id: int,
    published: str | None = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> None:
    """Mark an episode as awaiting background processing for a user."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # Insert only if this user hasn't seen this URL before; the composite
        # (url, user_id) key gives two users their own row for one URL
        conn.execute(
            """
            INSERT OR IGNORE INTO episodes (url, title, feed_id, status, published, user_id)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (url, title, feed_id, published, uid),
        )
        conn.commit()

def update_episode_status(url: str, status: str, user_id: int, db_path: str = DB_PATH) -> None:
    """Update the processing status for a user's episode."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE episodes SET status = ? WHERE url = ? AND user_id = ?",
            (status, url, uid),
        )  # simple status update used by the worker thread
        conn.commit()

def get_episode_by_id(episode_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a user's episode by its database ID."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM episodes WHERE id = ? AND user_id = ?", (episode_id, uid)
        )
        return cur.fetchone()

def delete_episode_by_id(episode_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a user's episode and all its associated articles."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # Delete associated articles, only for episodes this user owns
        conn.execute(
            """
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes WHERE id = ? AND user_id = ?
            )
            """,
            (episode_id, uid),
        )
        # Delete the episode
        conn.execute(
            "DELETE FROM episodes WHERE id = ? AND user_id = ?", (episode_id, uid)
        )
        conn.commit()

def delete_episodes_bulk(episode_ids: List[int], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple of a user's episodes and all their associated data. Returns count deleted."""
    uid = _require_user(user_id)
    if not episode_ids:
        return 0
    placeholders = ",".join("?" * len(episode_ids))
    with sqlite3.connect(db_path) as conn:
        # Delete associated articles, only for episodes this user owns
        conn.execute(
            f"""
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes WHERE id IN ({placeholders}) AND user_id = ?
            )
            """,
            (*episode_ids, uid),
        )
        # Delete the episodes
        cur = conn.execute(
            f"DELETE FROM episodes WHERE id IN ({placeholders}) AND user_id = ?",
            (*episode_ids, uid),
        )
        conn.commit()
        return cur.rowcount

def reset_episode_for_reprocess(episode_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Clear a user's episode data to prepare for reprocessing."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE episodes
            SET transcript = NULL, summary = NULL, action_items = NULL,
                status = 'queued', processed_at = NULL
            WHERE id = ? AND user_id = ?
            """,
            (episode_id, uid),
        )
        conn.commit()

def list_episodes(feed_id: int, user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return a user's episodes belonging to a particular feed."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM episodes WHERE feed_id = ? AND user_id = ? ORDER BY id",
            (feed_id, uid),
        )
        return cur.fetchall()

def list_all_episodes(order_by: str = "id", user_id: Optional[int] = None,
                      db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List a user's episodes from all feeds ordered by the provided column."""
    uid = _require_user(user_id)
    valid = {"id", "published", "processed_at"}
    column = order_by if order_by in valid else "id"
    direction = "DESC" if column in {"published", "processed_at"} else "ASC"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT * FROM episodes WHERE user_id = ? ORDER BY {column} {direction}",
            (uid,),
        )
        return cur.fetchall()

def get_youtube_episodes_missing_channel(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return YouTube episodes that have no channel value yet."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, url FROM episodes
            WHERE channel IS NULL
              AND (url LIKE '%youtube.com%' OR url LIKE '%youtu.be%')
            """
        )
        return cur.fetchall()

def set_episode_channel(episode_id: int, channel: str, db_path: str = DB_PATH) -> None:
    """Set the channel name on an existing episode."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE episodes SET channel = ? WHERE id = ?",
            (channel, episode_id),
        )
        conn.commit()

def add_article(
    episode_id: Optional[int],
    topic: str,
    style: str,
    content: str,
    user_id: int,
    db_path: str = DB_PATH,
    brief_id: Optional[int] = None,
    brief_run_id: Optional[int] = None,
    source_type: Optional[str] = None,
) -> int:
    """Save a generated article and return its id.

    ``episode_id`` may be ``None`` for agent-generated articles that are not
    tied to a podcast/RSS episode. ``brief_id``/``brief_run_id``/``source_type``
    tag drafts produced by the content agent.
    """
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO articles (episode_id, topic, style, content, created_at, brief_id, brief_run_id, source_type, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (episode_id, topic, style, content, created_at, brief_id, brief_run_id, source_type, uid),
        )
        conn.commit()
        return cur.lastrowid

def get_article(article_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a single article by its id, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                   f.title AS podcast_title, f.url AS podcast_url
            FROM articles a
            LEFT JOIN episodes e ON a.episode_id = e.id
            LEFT JOIN feeds f ON e.feed_id = f.id
            WHERE a.id = ? AND a.user_id = ?
            """,
            (article_id, uid),
        )
        return cur.fetchone()

def update_article(
    article_id: int,
    user_id: int,
    topic: str | None = None,
    style: str | None = None,
    content: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update an existing article's fields, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # Build update query dynamically based on provided fields
        updates = []
        params = []
        if topic is not None:
            updates.append("topic = ?")
            params.append(topic)
        if style is not None:
            updates.append("style = ?")
            params.append(style)
        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if updates:
            params.append(article_id)
            params.append(uid)
            conn.execute(
                f"UPDATE articles SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params,
            )
            conn.commit()

def delete_article(article_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete an article and its social posts by its id, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        # Delete associated social posts first, only for articles this user owns
        conn.execute(
            """
            DELETE FROM social_posts WHERE article_id IN (
                SELECT id FROM articles WHERE id = ? AND user_id = ?
            )
            """,
            (article_id, uid),
        )
        conn.execute("DELETE FROM articles WHERE id = ? AND user_id = ?", (article_id, uid))
        conn.commit()

def list_articles(
    episode_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List a user's articles, optionally filtered by episode."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if episode_id is None:
            cur = conn.execute(
                """
                SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                       f.title AS podcast_title
                FROM articles a
                LEFT JOIN episodes e ON a.episode_id = e.id
                LEFT JOIN feeds f ON e.feed_id = f.id
                WHERE a.user_id = ?
                ORDER BY a.created_at DESC
                """,
                (uid,),
            )
        else:
            cur = conn.execute(
                """
                SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                       f.title AS podcast_title
                FROM articles a
                LEFT JOIN episodes e ON a.episode_id = e.id
                LEFT JOIN feeds f ON e.feed_id = f.id
                WHERE a.episode_id = ? AND a.user_id = ?
                ORDER BY a.created_at DESC
                """,
                (episode_id, uid),
            )
        return cur.fetchall()

# --- Social Posts Functions ---

def add_social_post(
    article_id: int,
    platform: str,
    content: str,
    user_id: int,
    image_url: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Save a generated social media post and return its id."""
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO social_posts (article_id, platform, content, image_url, created_at, used, user_id)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (article_id, platform, content, image_url, created_at, uid),
        )
        conn.commit()
        return cur.lastrowid

def list_social_posts(
    article_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List a user's social posts, optionally filtered by article."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if article_id is None:
            cur = conn.execute(
                """
                SELECT sp.*, a.topic AS article_topic
                FROM social_posts sp
                JOIN articles a ON sp.article_id = a.id
                WHERE sp.user_id = ?
                ORDER BY sp.created_at DESC
                """,
                (uid,),
            )
        else:
            cur = conn.execute(
                """
                SELECT sp.*, a.topic AS article_topic
                FROM social_posts sp
                JOIN articles a ON sp.article_id = a.id
                WHERE sp.article_id = ? AND sp.user_id = ?
                ORDER BY sp.platform, sp.id
                """,
                (article_id, uid),
            )
        return cur.fetchall()

def get_social_post(post_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a single social post by its id, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, a.topic AS article_topic
            FROM social_posts sp
            LEFT JOIN articles a ON sp.article_id = a.id
            WHERE sp.id = ? AND sp.user_id = ?
            """,
            (post_id, uid),
        )
        return cur.fetchone()

def delete_social_post(post_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a social post by its id, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM social_posts WHERE id = ? AND user_id = ?", (post_id, uid))
        conn.commit()

def delete_social_posts_bulk(post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple of a user's social posts. Returns count deleted."""
    uid = _require_user(user_id)
    if not post_ids:
        return 0
    placeholders = ",".join("?" * len(post_ids))
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM social_posts WHERE id IN ({placeholders}) AND user_id = ?",
            (*post_ids, uid),
        )
        conn.commit()
        return cur.rowcount

def delete_social_posts_for_article(article_id: int, user_id: int, db_path: str = DB_PATH) -> int:
    """Delete a user's social posts for an article. Returns count deleted."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM social_posts WHERE article_id = ? AND user_id = ?",
            (article_id, uid),
        )
        conn.commit()
        return cur.rowcount

def mark_social_post_used(
    post_id: int,
    used: bool = True,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> None:
    """Mark a social post as used or unused, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE social_posts SET used = ? WHERE id = ? AND user_id = ?",
            (1 if used else 0, post_id, uid),
        )
        conn.commit()

def update_social_post(post_id: int, content: str, user_id: int, db_path: str = DB_PATH) -> None:
    """Update the content of a social post, scoped to its owner."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE social_posts SET content = ? WHERE id = ? AND user_id = ?",
            (content, post_id, uid),
        )
        conn.commit()

def bulk_replace_post_content(
    find_text: str,
    replace_text: str,
    post_type: str,
    user_id: int,
    case_sensitive: bool = False,
    whole_word: bool = False,
    post_ids: Optional[List[int]] = None,
    excluded_matches: Optional[Dict[str, bool]] = None,
    db_path: str = DB_PATH,
) -> int:
    """Replace text in a user's posts of a given type, optionally filtered by post IDs.

    Args:
        find_text: The text to search for
        replace_text: The text to replace with
        post_type: 'social' for social_posts or 'standalone' for standalone_posts
        user_id: Owner of the posts; replacement never crosses users
        case_sensitive: Whether to do case-sensitive matching
        whole_word: Whether to match whole words only
        post_ids: Optional list of post IDs to limit replacement to
        excluded_matches: Optional dict of excluded matches {"postId-matchIndex": True}

    Returns:
        Number of posts that were modified
    """
    import re

    uid = _require_user(user_id)
    table_name = "social_posts" if post_type == "social" else "standalone_posts"
    excluded_matches = excluded_matches or {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Get posts with their content, optionally filtered by post_ids
        if post_ids:
            placeholders = ",".join("?" for _ in post_ids)
            cur = conn.execute(
                f"SELECT id, content FROM {table_name} WHERE id IN ({placeholders}) AND user_id = ?",
                (*post_ids, uid)
            )
        else:
            cur = conn.execute(
                f"SELECT id, content FROM {table_name} WHERE user_id = ?",
                (uid,)
            )
        posts = cur.fetchall()
        
        affected_count = 0
        flags = 0 if case_sensitive else re.IGNORECASE
        
        # Build pattern with optional word boundaries
        if whole_word:
            pattern = re.compile(r'\b' + re.escape(find_text) + r'\b', flags)
        else:
            pattern = re.compile(re.escape(find_text), flags)
        
        for post in posts:
            post_id = post['id']
            content = post['content'] or ''
            
            # Check if any matches in this post are NOT excluded
            matches = list(pattern.finditer(content))
            if not matches:
                continue
            
            # If there are excluded matches for this post, do selective replacement
            has_exclusions = any(f"{post_id}-{i+1}" in excluded_matches for i in range(len(matches)))
            
            if has_exclusions:
                # Do selective replacement - replace only non-excluded matches
                new_content = []
                last_end = 0
                for i, match in enumerate(matches):
                    match_key = f"{post_id}-{i+1}"
                    # Add content before this match
                    new_content.append(content[last_end:match.start()])
                    # Add either replacement or original based on exclusion
                    if match_key in excluded_matches:
                        new_content.append(match.group())  # Keep original
                    else:
                        new_content.append(replace_text)  # Replace
                    last_end = match.end()
                # Add remaining content after last match
                new_content.append(content[last_end:])
                new_content = ''.join(new_content)
            else:
                # No exclusions, replace all matches. The lambda keeps the
                # replacement literal — re.sub would read backslash escapes in
                # a plain string, so a typed "\n" became a newline and "\1" or
                # a trailing backslash raised. The selective branch above
                # already appends replace_text as-is; this matches it, and what
                # the Find & Replace preview shows.
                new_content = pattern.sub(lambda _: replace_text, content)
            
            if new_content != content:
                conn.execute(
                    f"UPDATE {table_name} SET content = ? WHERE id = ? AND user_id = ?",
                    (new_content, post_id, uid),
                )
                affected_count += 1
        
        conn.commit()
    
    return affected_count

# --- Scheduled Posts Functions ---

def add_scheduled_post(
    scheduled_for: str,
    post_type: str,
    user_id: int,
    social_post_id: int | None = None,
    article_id: int | None = None,
    standalone_post_id: int | None = None,
    platform: str = "linkedin",
    status: str = "pending",
    linkedin_post_urn: str | None = None,
    account_id: int | None = None,
    db_path: str = DB_PATH,
) -> int:
    """Add a post to the schedule queue. Returns the scheduled post id.

    ``account_id`` names which of the platform's connected logins publishes this
    entry. Left out, it is filled from the platform's default account at queue
    time so the queue row records a concrete target instead of re-deciding one
    later, which is what lets two accounts on the same platform sit in the queue
    side by side.
    """
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    posted_at = created_at if status == "posted" else None
    if account_id is None:
        account_id = resolve_account_id(platform, db_path=db_path, user_id=uid)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO scheduled_posts
                (social_post_id, article_id, standalone_post_id, post_type, user_id, platform,
                 scheduled_for, status, linkedin_post_urn, created_at, posted_at, account_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (social_post_id, article_id, standalone_post_id, post_type, uid, platform,
             scheduled_for, status, linkedin_post_urn, created_at, posted_at, account_id),
        )
        conn.commit()
        return cur.lastrowid

def get_scheduled_post(scheduled_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get a single scheduled post by id with joined content data."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, 
                   soc.content AS social_content, soc.platform AS social_platform,
                   soc.image_url AS social_image_url,
                   a.topic AS article_topic, a.content AS article_content,
                   a.episode_id,
                   st.content AS standalone_content, st.platform AS standalone_platform,
                   st.image_url AS standalone_image_url,
                   st.video_url AS standalone_video_url,
                   st.ig_post_type AS standalone_ig_post_type,
                   st.ig_collaborators AS standalone_ig_collaborators,
                   st.ig_reel_tags AS standalone_ig_reel_tags
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            LEFT JOIN articles a ON sp.article_id = a.id
            LEFT JOIN standalone_posts st ON sp.standalone_post_id = st.id
            WHERE sp.id = ? AND sp.user_id = ?
            """,
            (scheduled_id, uid),
        )
        return cur.fetchone()

def get_pending_schedules_for_social_posts(social_post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> dict:
    """Get pending scheduled posts for a list of social post IDs.

    Returns a dict mapping social_post_id -> list of {platform, scheduled_for} dicts
    """
    uid = _require_user(user_id)
    if not social_post_ids:
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in social_post_ids)
        cur = conn.execute(
            f"""
            SELECT social_post_id, platform, scheduled_for
            FROM scheduled_posts
            WHERE social_post_id IN ({placeholders})
            AND status = 'pending'
            AND user_id = ?
            ORDER BY scheduled_for ASC
            """,
            [*social_post_ids, uid],
        )
        
        result = {}
        for row in cur.fetchall():
            post_id = row['social_post_id']
            if post_id not in result:
                result[post_id] = []
            result[post_id].append({
                'platform': row['platform'],
                'scheduled_for': row['scheduled_for'],
            })
        return result

def get_pending_schedules_for_standalone_posts(standalone_post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> dict:
    """Get pending scheduled posts for a list of standalone post IDs.

    Returns a dict mapping standalone_post_id -> {platform: scheduled_for}
    """
    uid = _require_user(user_id)
    if not standalone_post_ids:
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in standalone_post_ids)
        cur = conn.execute(
            f"""
            SELECT standalone_post_id, platform, scheduled_for
            FROM scheduled_posts
            WHERE standalone_post_id IN ({placeholders})
            AND status = 'pending'
            AND user_id = ?
            ORDER BY scheduled_for ASC
            """,
            [*standalone_post_ids, uid],
        )
        
        result = {}
        for row in cur.fetchall():
            post_id = row['standalone_post_id']
            if post_id not in result:
                result[post_id] = {}
            # Store as platform -> scheduled_for dict for easy lookup
            result[post_id][row['platform']] = row['scheduled_for']
        return result

def get_pending_schedule_ids_for_standalone_posts(standalone_post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> dict:
    """Map standalone_post_id -> [ids of its pending scheduled_posts entries].

    The entries themselves, not just when they run, so a caller that deletes a
    saved post can take its queue entries with it: deleting the post alone
    leaves a pending entry pointing at nothing.
    """
    uid = _require_user(user_id)
    if not standalone_post_ids:
        return {}

    with sqlite3.connect(db_path) as conn:
        placeholders = ",".join("?" for _ in standalone_post_ids)
        cur = conn.execute(
            f"""
            SELECT standalone_post_id, id
            FROM scheduled_posts
            WHERE standalone_post_id IN ({placeholders})
            AND status = 'pending'
            AND user_id = ?
            """,
            [*standalone_post_ids, uid],
        )
        result = {}
        for post_id, scheduled_id in cur.fetchall():
            result.setdefault(post_id, []).append(scheduled_id)
        return result

def get_posted_info_for_standalone_posts(standalone_post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> dict:
    """Get posted info for a list of standalone post IDs.

    Returns a dict mapping standalone_post_id -> {platform: {url, posted_at}}
    Only includes posts that have been successfully posted.
    """
    uid = _require_user(user_id)
    if not standalone_post_ids:
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in standalone_post_ids)
        cur = conn.execute(
            f"""
            SELECT standalone_post_id, platform, linkedin_post_urn, posted_at
            FROM scheduled_posts
            WHERE standalone_post_id IN ({placeholders})
            AND status = 'posted'
            AND user_id = ?
            ORDER BY posted_at DESC
            """,
            [*standalone_post_ids, uid],
        )
        
        result = {}
        rows = cur.fetchall()
        for row in rows:
            post_id = row['standalone_post_id']
            if post_id not in result:
                result[post_id] = {}
            # Store the most recent posted info per platform
            if row['platform'] not in result[post_id]:
                url = row['linkedin_post_urn']
                if row['platform'] == 'linkedin' and url and not url.startswith('http'):
                    url = f"https://www.linkedin.com/feed/update/{url}"
                result[post_id][row['platform']] = {
                    'url': url,
                    'posted_at': row['posted_at'],
                }
        return result

def list_scheduled_posts(
    user_id: int,
    status: str | None = None,
    platform: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_order: str = 'asc',
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List a user's scheduled posts with optional filtering.

    Args:
        user_id: Owner of the queue entries
        status: Filter by post status (pending, posted, failed, cancelled)
        platform: Filter by platform (e.g. 'xiaohongshu', 'douyin')
        date_from: Filter posts scheduled on or after this date (YYYY-MM-DD)
        date_to: Filter posts scheduled on or before this date (YYYY-MM-DD)
        sort_order: Sort order for scheduled_for column ('asc' or 'desc')
        db_path: Database path
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT sp.*,
                   soc.content AS social_content, soc.platform AS social_platform,
                   soc.image_url AS social_image_url,
                   a.topic AS article_topic, a.content AS article_content,
                   st.content AS standalone_content, st.platform AS standalone_platform,
                   st.image_url AS standalone_image_url,
                   st.video_url AS standalone_video_url,
                   st.ig_post_type AS standalone_ig_post_type,
                   st.ig_collaborators AS standalone_ig_collaborators,
                   st.ig_reel_tags AS standalone_ig_reel_tags
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            LEFT JOIN articles a ON sp.article_id = a.id
            LEFT JOIN standalone_posts st ON sp.standalone_post_id = st.id
            WHERE 1=1
        """
        params = [uid]
        query += " AND sp.user_id = ?"

        if status:
            query += " AND sp.status = ?"
            params.append(status)
        if platform:
            query += " AND sp.platform = ?"
            params.append(platform)
        if date_from:
            query += " AND sp.scheduled_for >= ?"
            params.append(date_from)
        if date_to:
            # Include the entire end date by appending end-of-day time
            query += " AND sp.scheduled_for <= ?"
            params.append(date_to + "T23:59:59")

        # Dynamic sort order (validate to prevent SQL injection)
        order = "DESC" if sort_order.lower() == 'desc' else "ASC"
        # id breaks ties so entries sharing a moment (a post and its copies on
        # other platforms) always list in the same order.
        query += f" ORDER BY sp.scheduled_for {order}, sp.id ASC"
        cur = conn.execute(query, params)
        return cur.fetchall()

def _scheduled_field(row, name):
    """A column of a queue row, or None when the row does not carry it."""
    return row[name] if name in row.keys() else None

def scheduled_group_key(row):
    """What makes two queue entries one post going to more than one place.

    The queue holds one entry per platform and account, so a post sent to three
    places is three entries. They are the same post when they have the same
    copy, image and video, are in the same state and go out at the same moment;
    that is the key. An article is the same post by its article id, since the
    copy it publishes is built from the article. An entry with nothing to show
    (its post was deleted) is never merged with anything.
    """
    post_type = _scheduled_field(row, 'post_type')
    article_id = _scheduled_field(row, 'article_id')
    if post_type == 'article' and article_id:
        what = ('article', article_id)
    else:
        content = (_scheduled_field(row, 'social_content')
                   or _scheduled_field(row, 'standalone_content') or '')
        if not content:
            return ('entry', row['id'])
        image = (_scheduled_field(row, 'social_image_url')
                 or _scheduled_field(row, 'standalone_image_url') or '')
        what = ('copy', content, image, _scheduled_field(row, 'standalone_video_url') or '')
    return (post_type, what, row['status'], row['scheduled_for'])

def group_scheduled_rows(rows):
    """``rows`` (from list_scheduled_posts) as lists of entries that are one post.

    Groups come out in the order their first entry appears, and entries keep
    their order within a group. A group holds at most one entry per platform
    and account: a second entry for the same account is a deliberate repost,
    so it starts a group of its own rather than hiding in this one.
    """
    groups = []
    open_groups = {}
    for row in rows:
        slot = (_scheduled_field(row, 'platform'), _scheduled_field(row, 'account_id'))
        for group in open_groups.setdefault(scheduled_group_key(row), []):
            if slot not in group['slots']:
                group['slots'].add(slot)
                group['rows'].append(row)
                break
        else:
            group = {'slots': {slot}, 'rows': [row]}
            open_groups[scheduled_group_key(row)].append(group)
            groups.append(group)
    return [group['rows'] for group in groups]

def get_scheduled_group_ids(scheduled_id: int, user_id: int, db_path: str = DB_PATH) -> List[int]:
    """The ids of every queue entry shown in one row with ``scheduled_id``.

    A row that does not exist (or has no copies) is its own group.
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        found = conn.execute(
            "SELECT status FROM scheduled_posts WHERE id = ? AND user_id = ?",
            (scheduled_id, uid),
        ).fetchone()
    if not found:
        return [scheduled_id]
    for group in group_scheduled_rows(list_scheduled_posts(uid, status=found[0], db_path=db_path)):
        ids = [row['id'] for row in group]
        if scheduled_id in ids:
            return ids
    return [scheduled_id]

def get_pending_scheduled_posts(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get all pending scheduled posts that are due (scheduled_for <= now).

    Uses local time since time slots are configured in local time by users.

    Deliberately unscoped: this is the publisher's consumption queue — it reads
    every owner's due entries so they publish on their global time slots; each
    row's user_id, not a filter, is what ties a due entry back to its owner.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, 
                   soc.content AS social_content, soc.platform AS social_platform,
                   soc.image_url AS social_image_url,
                   a.topic AS article_topic, a.content AS article_content,
                   a.episode_id,
                   st.content AS standalone_content, st.platform AS standalone_platform,
                   st.image_url AS standalone_image_url,
                   st.video_url AS standalone_video_url,
                   st.ig_post_type AS standalone_ig_post_type,
                   st.ig_collaborators AS standalone_ig_collaborators,
                   st.ig_reel_tags AS standalone_ig_reel_tags
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            LEFT JOIN articles a ON sp.article_id = a.id
            LEFT JOIN standalone_posts st ON sp.standalone_post_id = st.id
            WHERE sp.status = 'pending' AND sp.scheduled_for <= ?
            ORDER BY sp.scheduled_for ASC
            """,
            (now,),
        )
        return cur.fetchall()

def update_scheduled_post_time(
    scheduled_id: int,
    user_id: int,
    scheduled_for: str,
    db_path: str = DB_PATH,
) -> bool:
    """Update the scheduled time for a pending post.

    Returns True if updated successfully, False if post not found or not pending.
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE scheduled_posts
            SET scheduled_for = ?
            WHERE id = ? AND status = 'pending' AND user_id = ?
            """,
            (scheduled_for, scheduled_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

def redistribute_scheduled_posts(platform: str, user_id: int, db_path: str = DB_PATH) -> int:
    """Redistribute all pending posts for a platform to use the earliest available slots.
    
    This should be called when:
    - A time slot is added, deleted, or toggled
    - Daily posting limits are changed
    
    The function clears all existing scheduled times for pending posts of the platform,
    then reassigns them in order using get_next_available_slot().
    
    Args:
        platform: The platform to redistribute
        user_id: Owner of the queue entries to redistribute
        db_path: Database path

    Returns:
        Number of posts redistributed
    """
    uid = _require_user(user_id)
    from datetime import datetime

    # Get all pending posts for this platform, ordered by their creation time
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id FROM scheduled_posts
            WHERE platform = ? AND status = 'pending' AND user_id = ?
            ORDER BY created_at ASC
            """,
            (platform, uid),
        )
        pending_posts = [row['id'] for row in cur.fetchall()]

    if not pending_posts:
        return 0

    # Clear all scheduled times first (set to far future temporarily)
    # This ensures get_next_available_slot doesn't see conflicts
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE scheduled_posts
            SET scheduled_for = '9999-12-31T23:59:59'
            WHERE platform = ? AND status = 'pending' AND user_id = ?
            """,
            (platform, uid),
        )
        conn.commit()

    # Reassign each post to the next available slot
    redistributed = 0
    for post_id in pending_posts:
        next_slot = get_next_available_slot(platform, db_path)
        if next_slot:
            update_scheduled_post_time(post_id, uid, next_slot, db_path=db_path)
            redistributed += 1
        else:
            # No more slots available, leave at far future (will need manual intervention)
            pass

    return redistributed

def _assign_group_times(conn, groups, times, user_id):
    """Give ``groups[i]`` the moment ``times[i]``, every entry of it at once."""
    for group, when in zip(groups, times):
        ids = [row['id'] for row in group]
        conn.execute(
            f"UPDATE scheduled_posts SET scheduled_for = ? "
            f"WHERE status = 'pending' AND user_id = ? AND id IN ({','.join('?' for _ in ids)})",
            [when, user_id, *ids],
        )

def reorder_scheduled_posts(post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> bool:
    """Reorder a user's pending scheduled posts by swapping their scheduled times.

    Takes a list of post IDs in the desired new order. The scheduled_for times
    are preserved but reassigned based on the new order.

    The queue page shows a post that goes to several platforms as one row, so
    an id stands for its whole group: its copies move with it and keep sharing
    one moment. Times are handed out per group, never per entry, which is what
    keeps a group of three from being split when it swaps places with a lone
    entry.

    Args:
        post_ids: List of scheduled post IDs in the desired order
        user_id: Owner of the queue entries
        db_path: Database path

    Returns:
        True if successful, False otherwise
    """
    uid = _require_user(user_id)
    if not post_ids or len(post_ids) < 2:
        return True  # Nothing to reorder

    by_id = {}
    for group in group_scheduled_rows(list_scheduled_posts(uid, status='pending', db_path=db_path)):
        for row in group:
            by_id[row['id']] = group

    ordered = []
    for post_id in post_ids:
        group = by_id.get(post_id)
        if group is not None and not any(group is seen for seen in ordered):
            ordered.append(group)
    if len(ordered) < 2:
        return True  # Not enough posts to reorder

    # The moments these posts already hold, earliest first; the first post in
    # the new order gets the earliest, and so on.
    times = sorted(group[0]['scheduled_for'] for group in ordered)
    with sqlite3.connect(db_path) as conn:
        _assign_group_times(conn, ordered, times, uid)
        conn.commit()

    return True

def move_posts_to_position(
    post_ids: List[int],
    user_id: int,
    position: str,
    db_path: str = DB_PATH,
) -> bool:
    """Move selected pending posts to the top or bottom of the queue.

    Moves whole groups (see reorder_scheduled_posts): selecting one entry of a
    post that goes to several platforms moves all of them.

    Args:
        post_ids: List of scheduled post IDs to move
        user_id: Owner of the queue entries
        position: 'top' to move to earliest times, 'bottom' to move to latest times
        db_path: Database path

    Returns:
        True if successful, False otherwise
    """
    uid = _require_user(user_id)
    if not post_ids:
        return True  # Nothing to move

    # Every pending post, earliest first, as groups; their moments are the
    # slots the new order is dealt into.
    groups = group_scheduled_rows(list_scheduled_posts(uid, status='pending', db_path=db_path))
    if len(groups) < 2:
        return True  # Not enough posts to reorder

    selected_ids = set(post_ids)
    selected = [g for g in groups if any(row['id'] in selected_ids for row in g)]
    others = [g for g in groups if not any(row['id'] in selected_ids for row in g)]
    if not selected:
        return True  # No selected posts found

    new_order = selected + others if position == 'top' else others + selected
    times = [group[0]['scheduled_for'] for group in groups]
    with sqlite3.connect(db_path) as conn:
        _assign_group_times(conn, new_order, times, uid)
        conn.commit()

    return True

def update_scheduled_post_status(
    scheduled_id: int,
    user_id: int,
    status: str,
    linkedin_post_urn: str | None = None,
    error_message: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update the status of a scheduled post."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        posted_at = None
        if status == "posted":
            posted_at = datetime.utcnow().isoformat(timespec="seconds")

        conn.execute(
            """
            UPDATE scheduled_posts SET
                status = ?,
                linkedin_post_urn = ?,
                error_message = ?,
                posted_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (status, linkedin_post_urn, error_message, posted_at, scheduled_id, uid),
        )
        conn.commit()

def increment_retry_count(scheduled_id: int, user_id: int, db_path: str = DB_PATH) -> int:
    """Increment the retry count for a scheduled post and return the new value."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE scheduled_posts SET retry_count = COALESCE(retry_count, 0) + 1 "
            "WHERE id = ? AND user_id = ?",
            (scheduled_id, uid),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT retry_count FROM scheduled_posts WHERE id = ? AND user_id = ?",
            (scheduled_id, uid),
        )
        row = cur.fetchone()
        return row[0] if row else 0

def cancel_scheduled_post(scheduled_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Cancel a pending scheduled post. Returns True if cancelled."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE scheduled_posts SET status = 'cancelled'
            WHERE id = ? AND status = 'pending' AND user_id = ?
            """,
            (scheduled_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

def cancel_scheduled_post_by_source(
    post_type: str,
    post_id: int,
    user_id: int,
    platform: str,
    db_path: str = DB_PATH,
) -> bool:
    """Cancel a pending scheduled post by its source post ID and platform.

    Args:
        post_type: 'social' or 'standalone'
        post_id: The social_post_id or standalone_post_id
        user_id: Owner of the queued entry
        platform: The platform of the queued entry

    Returns True if a post was cancelled.
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        if post_type == 'social':
            cur = conn.execute(
                """
                UPDATE scheduled_posts SET status = 'cancelled'
                WHERE social_post_id = ? AND platform = ? AND status = 'pending'
                AND user_id = ?
                """,
                (post_id, platform, uid),
            )
        elif post_type == 'standalone':
            cur = conn.execute(
                """
                UPDATE scheduled_posts SET status = 'cancelled'
                WHERE standalone_post_id = ? AND platform = ? AND status = 'pending'
                AND user_id = ?
                """,
                (post_id, platform, uid),
            )
        else:
            return False
        conn.commit()
        return cur.rowcount > 0

def delete_scheduled_post(scheduled_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a scheduled post."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM scheduled_posts WHERE id = ? AND user_id = ?",
            (scheduled_id, uid),
        )
        conn.commit()

def clear_pending_scheduled_posts(user_id: int, db_path: str = DB_PATH) -> int:
    """Clear a user's pending scheduled posts. Returns the count of deleted posts."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM scheduled_posts WHERE status = 'pending' AND user_id = ?",
            (uid,),
        )
        conn.commit()
        return cur.rowcount

def delete_scheduled_posts_bulk(post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple scheduled posts by their IDs. Returns count deleted."""
    uid = _require_user(user_id)
    if not post_ids:
        return 0
    with sqlite3.connect(db_path) as conn:
        placeholders = ",".join("?" for _ in post_ids)
        cur = conn.execute(
            f"DELETE FROM scheduled_posts WHERE id IN ({placeholders}) AND user_id = ?",
            [*post_ids, uid],
        )
        conn.commit()
        return cur.rowcount

def get_scheduled_posts_for_article(
    article_id: int,
    user_id: int,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Get all scheduled posts for a specific article."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, soc.content AS social_content
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            WHERE (sp.article_id = ? OR soc.article_id = ?) AND sp.user_id = ?
            ORDER BY sp.scheduled_for ASC
            """,
            (article_id, article_id, uid),
        )
        return cur.fetchall()

# --- Schedule Time Slots Functions ---

def get_slot_platforms(slot_id: int, db_path: str = DB_PATH) -> List[str]:
    """Get the list of platforms assigned to a time slot.
    
    Returns an empty list if no platforms are explicitly assigned,
    which means the slot applies to ALL platforms.
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT platform FROM time_slot_platforms WHERE slot_id = ? ORDER BY platform",
            (slot_id,),
        )
        return [row[0] for row in cur.fetchall()]

def set_slot_platforms(slot_id: int, platforms: List[str], db_path: str = DB_PATH) -> None:
    """Set the platforms for a time slot (replaces any existing assignments).
    
    Pass an empty list to make the slot apply to all platforms.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM time_slot_platforms WHERE slot_id = ?", (slot_id,))
        for platform in platforms:
            conn.execute(
                "INSERT INTO time_slot_platforms (slot_id, platform) VALUES (?, ?)",
                (slot_id, platform),
            )
        conn.commit()

def add_time_slot(
    day_of_week: int,
    time_slot: str,
    enabled: bool = True,
    platforms: List[str] | None = None,
    db_path: str = DB_PATH,
) -> int:
    """Add a new time slot for queue-based scheduling.
    
    Args:
        day_of_week: 0-6 (Monday-Sunday), or -1 for every day
        time_slot: Time in HH:MM format (24-hour)
        enabled: Whether the slot is active
        platforms: List of platform names this slot applies to.
                   None or empty list means all platforms.
    
    Returns:
        The ID of the created time slot
    """
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO schedule_time_slots (day_of_week, time_slot, enabled, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (day_of_week, time_slot, 1 if enabled else 0, created_at),
        )
        slot_id = cur.lastrowid
        # Store platform assignments if provided
        if platforms:
            for platform in platforms:
                conn.execute(
                    "INSERT INTO time_slot_platforms (slot_id, platform) VALUES (?, ?)",
                    (slot_id, platform),
                )
        conn.commit()
        return slot_id

def list_time_slots(db_path: str = DB_PATH) -> List[dict]:
    """Get all configured time slots ordered by day and time.
    
    Each returned dict includes a 'platforms' key with the list of
    platform names assigned to the slot (empty list = all platforms).
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM schedule_time_slots
            ORDER BY day_of_week ASC, time_slot ASC
            """
        )
        slots = []
        for row in cur.fetchall():
            slot = dict(row)
            slot['platforms'] = get_slot_platforms(slot['id'], db_path)
            slots.append(slot)
        return slots

def get_enabled_time_slots(platform: str | None = None, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get only enabled time slots, optionally filtered by platform.
    
    Args:
        platform: If provided, only return slots that are assigned to this
                  platform (or slots with no platform restriction, i.e. all-platform slots).
        db_path: Database path
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if platform:
            # Return slots that either:
            # 1. Have no platform assignments (applies to all), OR
            # 2. Are explicitly assigned to this platform
            cur = conn.execute(
                """
                SELECT s.* FROM schedule_time_slots s
                WHERE s.enabled = 1
                AND (
                    NOT EXISTS (
                        SELECT 1 FROM time_slot_platforms tp WHERE tp.slot_id = s.id
                    )
                    OR EXISTS (
                        SELECT 1 FROM time_slot_platforms tp
                        WHERE tp.slot_id = s.id AND tp.platform = ?
                    )
                )
                ORDER BY s.day_of_week ASC, s.time_slot ASC
                """,
                (platform,),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM schedule_time_slots
                WHERE enabled = 1
                ORDER BY day_of_week ASC, time_slot ASC
                """
            )
        return cur.fetchall()

def update_time_slot(
    slot_id: int,
    day_of_week: int | None = None,
    time_slot: str | None = None,
    enabled: bool | None = None,
    platforms: List[str] | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update a time slot's settings.
    
    Args:
        slot_id: ID of the slot to update
        day_of_week: New day of week (None to leave unchanged)
        time_slot: New time (None to leave unchanged)
        enabled: New enabled state (None to leave unchanged)
        platforms: New platform list (None to leave unchanged;
                   empty list to apply to all platforms)
    """
    with sqlite3.connect(db_path) as conn:
        if day_of_week is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET day_of_week = ? WHERE id = ?",
                (day_of_week, slot_id),
            )
        if time_slot is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET time_slot = ? WHERE id = ?",
                (time_slot, slot_id),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, slot_id),
            )
        if platforms is not None:
            conn.execute(
                "DELETE FROM time_slot_platforms WHERE slot_id = ?", (slot_id,)
            )
            for platform in platforms:
                conn.execute(
                    "INSERT INTO time_slot_platforms (slot_id, platform) VALUES (?, ?)",
                    (slot_id, platform),
                )
        conn.commit()

def delete_time_slot(slot_id: int, db_path: str = DB_PATH) -> None:
    """Delete a time slot and its platform assignments."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM time_slot_platforms WHERE slot_id = ?", (slot_id,))
        conn.execute("DELETE FROM schedule_time_slots WHERE id = ?", (slot_id,))
        conn.commit()

def get_next_available_slot(platform: str = "linkedin", db_path: str = DB_PATH) -> str | None:
    """Find the next available time slot for scheduling on a specific platform.
    
    Each platform has its own queue, so two posts for different platforms can
    share the same wall-clock slot without conflicting.
    
    Also respects daily posting limits - if a platform has a max posts per day
    limit set, days that have reached that limit will be skipped.
    
    Args:
        platform: The platform to check slots for
        db_path: Database path
    
    Returns the next datetime (ISO format) based on configured time slots
    that doesn't conflict with existing pending posts FOR THE SAME PLATFORM.
    
    Note: Uses LOCAL time for comparison since time slots are configured
    in local time by the user.
    
    Returns:
        ISO format datetime string, or None if no slots configured
    """
    from datetime import datetime, timedelta
    
    slots = get_enabled_time_slots(platform=platform, db_path=db_path)
    if not slots:
        return None
    
    # Get daily limit for this platform (0 = unlimited)
    daily_limit = get_daily_limit(platform, db_path)
    
    # Get existing pending posts for THIS PLATFORM ONLY to check for conflicts
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT scheduled_for FROM scheduled_posts
            WHERE status = 'pending' AND platform = ?
            """,
            (platform,),
        )
        existing_times = {row['scheduled_for'] for row in cur.fetchall()}
    
    # Use local time since time slots are configured in local time
    now = datetime.now()
    
    # Cache for daily post counts to avoid repeated DB queries
    daily_counts_cache = {}
    
    # Look up to 30 days ahead
    for day_offset in range(30):
        check_date = now + timedelta(days=day_offset)
        current_day_of_week = check_date.weekday()  # 0=Monday, 6=Sunday
        date_str = check_date.strftime('%Y-%m-%d')
        
        # Check daily limit if set
        if daily_limit > 0:
            if date_str not in daily_counts_cache:
                daily_counts_cache[date_str] = count_scheduled_posts_for_day(
                    platform, date_str, db_path
                )
            
            # Skip this day if limit reached
            if daily_counts_cache[date_str] >= daily_limit:
                continue
        
        for slot in slots:
            slot_day = slot['day_of_week']
            # -1 means every day
            if slot_day != -1 and slot_day != current_day_of_week:
                continue
            
            # Parse the time slot
            try:
                hour, minute = map(int, slot['time_slot'].split(':'))
            except (ValueError, AttributeError):
                continue
            
            # Create the candidate datetime
            candidate = check_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            
            # Skip if in the past
            if candidate <= now:
                continue
            
            # Check if this slot is already taken for THIS PLATFORM
            candidate_str = candidate.isoformat(timespec="seconds")
            if candidate_str not in existing_times:
                # Update cache to account for this new post if we were to add it
                if daily_limit > 0:
                    daily_counts_cache[date_str] = daily_counts_cache.get(date_str, 0) + 1
                return candidate_str
    
    return None

def initialize_default_time_slots(db_path: str = DB_PATH) -> None:
    """Create default time slots if none exist.
    
    Default slots: 9:00 AM, 12:00 PM, 5:00 PM every day
    """
    existing = list_time_slots(db_path)
    if existing:
        return  # Already have slots configured
    
    default_times = ["09:00", "12:00", "17:00"]
    for time in default_times:
        add_time_slot(
            day_of_week=-1,  # Every day
            time_slot=time,
            enabled=True,
            db_path=db_path,
        )

# =============================================================================
# Platform Daily Limits Functions
# =============================================================================

def get_daily_limit(platform: str, db_path: str = DB_PATH) -> int:
    """Get the max posts per day limit for a platform.
    
    Returns 0 if no limit is set (unlimited).
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT max_posts_per_day FROM platform_daily_limits WHERE platform = ?",
            (platform,),
        )
        row = cur.fetchone()
        return row[0] if row else 0

def set_daily_limit(platform: str, max_posts_per_day: int, db_path: str = DB_PATH) -> None:
    """Set the max posts per day limit for a platform.
    
    Set to 0 for unlimited posts.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO platform_daily_limits (platform, max_posts_per_day)
            VALUES (?, ?)
            ON CONFLICT(platform) DO UPDATE SET max_posts_per_day = excluded.max_posts_per_day
            """,
            (platform, max_posts_per_day),
        )
        conn.commit()

def get_all_daily_limits(db_path: str = DB_PATH) -> dict:
    """Get all platform daily limits as a dictionary.
    
    Returns: {platform: max_posts_per_day, ...}
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT platform, max_posts_per_day FROM platform_daily_limits")
        return {row['platform']: row['max_posts_per_day'] for row in cur.fetchall()}

def count_scheduled_posts_for_day(platform: str, date_str: str, db_path: str = DB_PATH) -> int:
    """Count pending scheduled posts for a platform on a specific date.
    
    Args:
        platform: The platform (e.g. 'xiaohongshu', 'douyin')
        date_str: Date in YYYY-MM-DD format
        
    Returns:
        Number of pending posts scheduled for that day
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM scheduled_posts
            WHERE platform = ?
            AND status = 'pending'
            AND date(scheduled_for) = ?
            """,
            (platform, date_str),
        )
        return cur.fetchone()[0]

# =============================================================================
# Standalone Posts Functions (Command Center)
# =============================================================================

def add_standalone_post(
    source_type: str,
    source_content: str,
    platform: str,
    content: str,
    user_id: int,
    image_url: Optional[str] = None,
    repost: bool = False,
    db_path: str = DB_PATH,
    brief_id: Optional[int] = None,
    brief_run_id: Optional[int] = None,
    account_id: Optional[int] = None,
    video_url: Optional[str] = None,
    ig_collaborators: Optional[list] = None,
    ig_reel_tags: Optional[list] = None,
) -> int:
    """Save a standalone post (not tied to an article) and return its id.

    Args:
        source_type: 'freeform', 'url', or 'text'
        source_content: The original prompt, URL, or text used to generate
        platform: Target platform (e.g. 'xiaohongshu', 'douyin')
        content: The generated post content
        user_id: Owner of the post
        image_url: Optional URL of an image to attach to the post
        video_url: Optional URL of a video to attach. A post with a video
            publishes the video and ignores the image.
        repost: If True, marks this as an intentional duplicate that bypassed
            the import-time duplicate check so the same content can be posted
            again.
        account_id: Which of the platform's connected accounts this row posts
            as. Left out, it resolves to that platform's default account, so a
            card ticked for "小红书" still lands somewhere concrete while a card
            ticked for a named second account keeps its own row.

    Returns:
        The ID of the newly created post
    """
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    if account_id is None:
        account_id = resolve_account_id(platform, db_path=db_path, user_id=uid)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO standalone_posts (source_type, source_content, platform, content, user_id, image_url, created_at, used, repost, brief_id, brief_run_id, account_id, video_url, ig_collaborators, ig_reel_tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_type, source_content, platform, content, uid, image_url, created_at, 1 if repost else 0, brief_id, brief_run_id, account_id, video_url,
             json.dumps(ig_collaborators) if ig_collaborators else None,
             json.dumps(ig_reel_tags) if ig_reel_tags else None),
        )
        conn.commit()
        return cur.lastrowid

def get_existing_standalone_content(user_id: int, db_path: str = DB_PATH) -> dict:
    """Return a dict mapping (platform, content) -> post id for a user's standalone posts.

    Used by the import route to skip duplicates (per user).
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT id, platform, content FROM standalone_posts WHERE user_id = ?",
            (uid,),
        )
        result = {}
        for row in cur.fetchall():
            key = (row[1], row[2])
            if key not in result:
                result[key] = row[0]
        return result

def list_standalone_posts(
    user_id: int,
    source_type: Optional[str] = None,
    platform: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List a user's standalone posts, optionally filtered by source type and/or platform.

    Args:
        user_id: Owner of the posts
        source_type: Optional filter by source type ('freeform', 'url', 'text')
        platform: Optional filter by platform
        limit: Optional max rows to return (for pagination)
        offset: Optional number of rows to skip (for pagination)

    Returns:
        List of standalone post rows, newest first
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        conditions = ["user_id = ?"]
        params = [uid]

        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)
        if platform:
            conditions.append("platform = ?")
            params.append(platform)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ? OFFSET ?"
            params.append(int(limit))
            params.append(int(offset or 0))

        cur = conn.execute(
            f"""
            SELECT * FROM standalone_posts
            {where_clause}
            ORDER BY created_at DESC
            {limit_clause}
            """,
            params,
        )
        return cur.fetchall()

def get_standalone_post(post_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a single standalone post by its id, scoped to its owner.

    Args:
        post_id: The post ID
        user_id: Owner of the post

    Returns:
        The post row or None if not found (or owned by another user)
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM standalone_posts WHERE id = ? AND user_id = ?",
            (post_id, uid),
        )
        return cur.fetchone()

def list_standalone_posts_by_source_url(
    url: str,
    user_id: int,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List a user's standalone posts generated from a given saved source URL.

    Posts generated from saved sources store the originating URL (truncated to
    1000 chars) in ``source_content`` with a ``source_type`` of ``'saved_source'``
    (Generate from Saved Source), ``'url'`` (the URL tab on the Command Center),
    or ``'agent'`` (the content agent). There is no foreign key, so linkage is by
    URL string.

    Args:
        url: The source URL to match against.
        user_id: Owner of the posts.

    Returns:
        List of standalone post rows, most recent first.
    """
    if not url:
        return []
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM standalone_posts
            WHERE user_id = ?
              AND source_type IN ('saved_source', 'url', 'agent')
              AND source_content = ?
            ORDER BY created_at DESC
            """,
            (uid, url[:1000]),
        )
        return cur.fetchall()

def count_standalone_posts_by_source_urls(
    urls: List[str],
    user_id: int,
    db_path: str = DB_PATH,
) -> dict:
    """Count a user's standalone posts grouped by their source URL.

    Returns a dict mapping each provided URL (original, untruncated) to the
    number of that user's standalone posts whose ``source_content`` matches it.
    URLs are matched against the stored (truncated to 1000 chars) value.

    Args:
        urls: List of source URLs to count posts for.
        user_id: Owner of the posts.

    Returns:
        Dict of ``{url: count}`` (URLs with no posts map to 0).
    """
    uid = _require_user(user_id)
    counts = {u: 0 for u in urls}
    if not urls:
        return counts

    # Map truncated value -> original url(s) so we can report against the
    # caller's original URL strings.
    truncated_to_originals: dict = {}
    for u in urls:
        truncated_to_originals.setdefault(u[:1000], []).append(u)

    truncated_keys = list(truncated_to_originals.keys())
    placeholders = ",".join("?" for _ in truncated_keys)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT source_content, COUNT(*) AS cnt
            FROM standalone_posts
            WHERE user_id = ?
              AND source_type IN ('saved_source', 'url', 'agent')
              AND source_content IN ({placeholders})
            GROUP BY source_content
            """,
            (uid, *truncated_keys),
        )
        for row in cur.fetchall():
            for original in truncated_to_originals.get(row[0], []):
                counts[original] = row[1]
    return counts

def update_standalone_post(
    post_id: int,
    user_id: int,
    content: str,
    image_url: Optional[str] = None,
    clear_image: bool = False,
    db_path: str = DB_PATH,
) -> None:
    """Update the content and optionally the image of a standalone post.

    Args:
        post_id: The post ID
        user_id: Owner of the post
        content: New content for the post
        image_url: Optional new image URL (only updated if provided or clear_image is True)
        clear_image: If True, remove the image (set to NULL)
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        if clear_image:
            conn.execute(
                "UPDATE standalone_posts SET content = ?, image_url = NULL WHERE id = ? AND user_id = ?",
                (content, post_id, uid),
            )
        elif image_url is not None:
            conn.execute(
                "UPDATE standalone_posts SET content = ?, image_url = ? WHERE id = ? AND user_id = ?",
                (content, image_url, post_id, uid),
            )
        else:
            conn.execute(
                "UPDATE standalone_posts SET content = ? WHERE id = ? AND user_id = ?",
                (content, post_id, uid),
            )
        conn.commit()

def update_standalone_post_image(
    post_id: int,
    image_url: Optional[str],
    user_id: int,
    db_path: str = DB_PATH,
) -> None:
    """Update only the image URL of a standalone post, scoped to its owner.

    Args:
        post_id: The post ID
        image_url: New image URL (or None to remove image)
        user_id: Owner of the post
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE standalone_posts SET image_url = ? WHERE id = ? AND user_id = ?",
            (image_url, post_id, uid),
        )
        conn.commit()

def update_standalone_post_video(
    post_id: int,
    video_url: Optional[str],
    user_id: int,
    db_path: str = DB_PATH,
) -> None:
    """Set (or with None, clear) the video of a standalone post, scoped to its owner.

    Args:
        post_id: The post ID
        video_url: Public URL of the video, or None to remove it
        user_id: Owner of the post
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE standalone_posts SET video_url = ? WHERE id = ? AND user_id = ?",
            (video_url, post_id, uid),
        )
        conn.commit()


def update_social_post_image(
    post_id: int,
    image_url: Optional[str],
    user_id: int,
    db_path: str = DB_PATH,
) -> None:
    """Update only the image URL of a social post, scoped to its owner.

    Args:
        post_id: The post ID
        image_url: New image URL (or None to remove image)
        user_id: Owner of the post
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE social_posts SET image_url = ? WHERE id = ? AND user_id = ?",
            (image_url, post_id, uid),
        )
        conn.commit()

def delete_standalone_post(post_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a standalone post by its id, scoped to its owner.

    Args:
        post_id: The post ID to delete
        user_id: Owner of the post
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM standalone_posts WHERE id = ? AND user_id = ?", (post_id, uid))
        conn.commit()

def delete_standalone_posts_bulk(post_ids: List[int], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple of a user's standalone posts. Returns count deleted.

    Args:
        post_ids: List of post IDs to delete
        user_id: Owner of the posts

    Returns:
        Number of posts deleted
    """
    uid = _require_user(user_id)
    if not post_ids:
        return 0
    placeholders = ",".join("?" * len(post_ids))
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM standalone_posts WHERE id IN ({placeholders}) AND user_id = ?",
            (*post_ids, uid),
        )
        conn.commit()
        return cur.rowcount

def mark_standalone_post_used(post_id: int, used: bool = True, user_id: Optional[int] = None, db_path: str = DB_PATH) -> None:
    """Mark a standalone post as used or unused, scoped to its owner.

    Args:
        post_id: The post ID
        used: True to mark as used, False to mark as unused
        user_id: Owner of the post (required; Optional only because ``used``
            precedes it in the signature)
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE standalone_posts SET used = ? WHERE id = ? AND user_id = ?",
            (1 if used else 0, post_id, uid),
        )
        conn.commit()

# =============================================================================
# URL Sources CRUD Functions
# =============================================================================

def add_url_source(
    url: str,
    title: str,
    description: str,
    content: str,
    user_id: int,
    og_image: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Save extracted URL content for future reuse.

    If the URL already exists for this user, updates the existing record.
    The composite (url, user_id) unique index lets another user keep their
    own row for the same URL.

    Args:
        url: The source URL
        title: Page title
        description: Meta description or og:description
        content: Extracted body text content
        user_id: Owner of the source
        og_image: Open Graph image URL (optional)

    Returns:
        The id of the inserted or updated record
    """
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        # Check if URL already exists for this user
        cur = conn.execute(
            "SELECT id FROM url_sources WHERE url = ? AND user_id = ?", (url, uid)
        )
        existing = cur.fetchone()

        if existing:
            # Update existing record
            conn.execute(
                """
                UPDATE url_sources
                SET title = ?, description = ?, content = ?, og_image = ?, last_used_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (title, description, content, og_image, created_at, existing[0], uid),
            )
            conn.commit()
            return existing[0]
        else:
            # Insert new record
            cur = conn.execute(
                """
                INSERT INTO url_sources (url, title, description, content, og_image, created_at, last_used_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (url, title, description, content, og_image, created_at, created_at, uid),
            )
            conn.commit()
            return cur.lastrowid

def list_url_sources(user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List a user's saved URL sources, ordered by last used date.

    Args:
        user_id: Owner of the sources

    Returns:
        List of url_sources rows
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM url_sources
            WHERE user_id = ?
            ORDER BY last_used_at DESC
            """,
            (uid,),
        )
        return cur.fetchall()

def get_url_source(source_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get a single URL source by ID.

    Args:
        source_id: The source ID
        user_id: Owner of the source

    Returns:
        The url_sources row or None if not found
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM url_sources WHERE id = ? AND user_id = ?",
            (source_id, uid),
        )
        return cur.fetchone()

def get_url_source_by_url(url: str, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get a user's URL source by its URL.

    Args:
        url: The source URL
        user_id: Owner of the source

    Returns:
        The url_sources row or None if not found
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM url_sources WHERE url = ? AND user_id = ?",
            (url, uid),
        )
        return cur.fetchone()

def delete_url_source(source_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Delete a user's URL source by ID.

    Args:
        source_id: The source ID to delete
        user_id: Owner of the source

    Returns:
        True if a row was deleted, False otherwise
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM url_sources WHERE id = ? AND user_id = ?",
            (source_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

def update_url_source_last_used(source_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Update the last_used_at timestamp for a user's URL source.

    Args:
        source_id: The source ID
        user_id: Owner of the source
    """
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE url_sources SET last_used_at = ? WHERE id = ? AND user_id = ?",
            (now, source_id, uid),
        )
        conn.commit()

def update_url_source_content(
    source_id: int,
    user_id: int,
    title: str,
    description: str,
    content: str,
    og_image: Optional[str] = None,
    db_path: str = DB_PATH
) -> bool:
    """Update the content of a user's URL source (for re-extraction).

    Args:
        source_id: The source ID to update
        user_id: Owner of the source
        title: New title
        description: New description
        content: New extracted content
        og_image: New OG image URL (optional)

    Returns:
        True if updated successfully
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE url_sources
            SET title = ?, description = ?, content = ?, og_image = ?
            WHERE id = ? AND user_id = ?
            """,
            (title, description, content, og_image, source_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

# =============================================================================
# Uploaded Images Library Functions
# =============================================================================

def add_uploaded_image(
    filename: str,
    url: str,
    storage: str,
    user_id: int,
    size: int = 0,
    media_type: str = 'image',
    db_path: str = DB_PATH,
) -> int:
    """Save an uploaded image or video to the library.

    Args:
        filename: Original or generated filename
        url: The URL to access the media (local path or Cloudinary URL)
        storage: 'local' or 'cloudinary'
        user_id: Owner of the media
        size: File size in bytes
        media_type: 'image' or 'video'

    Returns:
        The id of the inserted record
    """
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        # Check if URL already exists for this user (avoid duplicates); the
        # same link stays independent per user, so dedupe is on (url, user_id)
        cur = conn.execute(
            "SELECT id FROM uploaded_images WHERE url = ? AND user_id = ?", (url, uid)
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        cur = conn.execute(
            """
            INSERT INTO uploaded_images (filename, url, storage, size, created_at, media_type, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, url, storage, size, created_at, media_type, uid),
        )
        conn.commit()
        return cur.lastrowid

def list_uploaded_images(user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List a user's uploaded images, ordered by most recent first.

    Args:
        user_id: Owner of the media

    Returns:
        List of uploaded_images rows
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM uploaded_images
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (uid,),
        )
        return cur.fetchall()

def delete_uploaded_image(image_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Delete a user's uploaded image record by ID.

    Args:
        image_id: The image ID to delete
        user_id: Owner of the media

    Returns:
        True if a row was deleted, False otherwise
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM uploaded_images WHERE id = ? AND user_id = ?",
            (image_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

def get_uploaded_image(image_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get a user's uploaded image by ID.

    Args:
        image_id: The image ID
        user_id: Owner of the media

    Returns:
        The uploaded_images row or None if not found
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM uploaded_images WHERE id = ? AND user_id = ?",
            (image_id, uid),
        )
        return cur.fetchone()

# =============================================================================
# Recent Prompts Functions
# =============================================================================

def list_recent_prompts(user_id: int, limit: int = 20, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get a user's unique recent freeform prompts from standalone_posts.

    Returns distinct prompts ordered by most recently used, so the user can
    easily reuse previous prompts in the Command Center.

    Args:
        user_id: Owner of the posts
        limit: Maximum number of prompts to return (default 20)

    Returns:
        List of rows with 'source_content' and 'created_at' fields
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT source_content, MAX(created_at) as created_at
            FROM standalone_posts
            WHERE user_id = ?
            AND source_type = 'freeform'
            AND source_content IS NOT NULL
            AND source_content != ''
            GROUP BY source_content
            ORDER BY MAX(created_at) DESC
            LIMIT ?
            """,
            (uid, limit),
        )
        return cur.fetchall()

def list_recent_image_prompts(user_id: int, limit: int = 10, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get a user's recent image generation sessions (prompt + image URL).

    Returns distinct ``(source_content, image_url, created_at)`` combos
    for ``source_type='image'``, ordered by most recently used.
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT source_content, image_url, MAX(created_at) as created_at
            FROM standalone_posts
            WHERE user_id = ?
            AND source_type = 'image'
            AND image_url IS NOT NULL
            AND image_url != ''
            GROUP BY source_content, image_url
            ORDER BY MAX(created_at) DESC
            LIMIT ?
            """,
            (uid, limit),
        )
        return cur.fetchall()

def clear_recent_prompts(user_id: int, db_path: str = DB_PATH) -> int:
    """Clear a user's prompt history by setting source_content to empty for their freeform posts.

    This preserves the generated posts but removes them from the recent prompts list.

    Args:
        user_id: Owner of the posts

    Returns:
        Number of posts affected
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE standalone_posts
            SET source_content = ''
            WHERE user_id = ?
            AND source_type = 'freeform'
            AND source_content IS NOT NULL
            AND source_content != ''
            """,
            (uid,),
        )
        conn.commit()
        return cur.rowcount

def delete_prompt_by_content(prompt_content: str, user_id: int, db_path: str = DB_PATH) -> int:
    """Delete a specific prompt from a user's history by clearing its source_content.

    This preserves the generated posts but removes the prompt from the history.

    Args:
        prompt_content: The exact prompt text to remove
        user_id: Owner of the posts

    Returns:
        Number of posts affected
    """
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE standalone_posts
            SET source_content = ''
            WHERE user_id = ?
            AND source_type = 'freeform'
            AND source_content = ?
            """,
            (uid, prompt_content),
        )
        conn.commit()
        return cur.rowcount

def delete_prompts_bulk(prompt_contents: List[str], user_id: int, db_path: str = DB_PATH) -> int:
    """Delete multiple prompts from a user's history by clearing their source_content.

    Args:
        prompt_contents: List of prompt texts to remove
        user_id: Owner of the posts

    Returns:
        Number of posts affected
    """
    uid = _require_user(user_id)
    if not prompt_contents:
        return 0

    with sqlite3.connect(db_path) as conn:
        placeholders = ",".join("?" for _ in prompt_contents)
        cur = conn.execute(
            f"""
            UPDATE standalone_posts
            SET source_content = ''
            WHERE user_id = ?
            AND source_type = 'freeform'
            AND source_content IN ({placeholders})
            """,
            (uid, *prompt_contents),
        )
        conn.commit()
        return cur.rowcount

# =============================================================================
# Prompt Library Functions
# =============================================================================

def add_library_prompt(title: str, content: str, user_id: int, db_path: str = DB_PATH) -> int:
    """Save a named, reusable prompt to the library and return its id."""
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO prompt_library (title, content, created_at, updated_at, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title.strip(), content, now, now, uid),
        )
        conn.commit()
        return cur.lastrowid

def list_library_prompts(user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return a user's saved library prompts, most recently updated first."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM prompt_library WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (uid,),
        )
        return cur.fetchall()

def get_library_prompt(prompt_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a user's single library prompt by id, or ``None`` if it does not exist."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM prompt_library WHERE id = ? AND user_id = ?", (prompt_id, uid)
        )
        return cur.fetchone()

def update_library_prompt(
    prompt_id: int,
    user_id: int,
    title: str,
    content: str,
    db_path: str = DB_PATH,
) -> int:
    """Update a user's library prompt title and content. Returns rows affected."""
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE prompt_library
            SET title = ?, content = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (title.strip(), content, now, prompt_id, uid),
        )
        conn.commit()
        return cur.rowcount

def delete_library_prompt(prompt_id: int, user_id: int, db_path: str = DB_PATH) -> int:
    """Delete a user's library prompt by id. Returns rows affected."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM prompt_library WHERE id = ? AND user_id = ?", (prompt_id, uid)
        )
        conn.commit()
        return cur.rowcount

# =============================================================================
# Generated Thumbnails Functions
# =============================================================================

def add_generated_thumbnail(
    youtube_url: str,
    video_id: str | None,
    title: str,
    channel: str,
    aspect: str,
    style: str,
    prompt: str,
    image_relpath: str,
    user_id: int,
    db_path: str = DB_PATH,
) -> int:
    """Insert a user's generated thumbnail record and return its ``id``."""
    uid = _require_user(user_id)
    created_at = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO generated_thumbnails
                (youtube_url, video_id, title, channel, aspect, style, prompt, image_relpath, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (youtube_url, video_id, title, channel, aspect, style, prompt, image_relpath, created_at, uid),
        )
        conn.commit()
        return cur.lastrowid

def list_generated_thumbnails(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Return a user's saved thumbnails, newest first."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM generated_thumbnails WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (uid, limit, offset),
        )
        return cur.fetchall()

def get_generated_thumbnail(
    thumbnail_id: int,
    user_id: int,
    db_path: str = DB_PATH,
) -> Optional[sqlite3.Row]:
    """Return a user's single generated thumbnail by ``id``, or ``None``."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM generated_thumbnails WHERE id = ? AND user_id = ?",
            (thumbnail_id, uid),
        )
        return cur.fetchone()

def delete_generated_thumbnail(
    thumbnail_id: int,
    user_id: int,
    db_path: str = DB_PATH,
) -> bool:
    """Delete a user's generated thumbnail row. Returns ``True`` if a row was removed."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM generated_thumbnails WHERE id = ? AND user_id = ?",
            (thumbnail_id, uid),
        )
        conn.commit()
        return cur.rowcount > 0

# ---------------------------------------------------------------------------
# Users (authentication)
# ---------------------------------------------------------------------------

def create_user(
    username: str,
    password_hash: str,
    db_path: str = DB_PATH,
) -> int:
    """Insert a new user and return the new row id.

    The first account created on an instance becomes its admin — it is the
    only role allowed to edit /settings. Raises ``sqlite3.IntegrityError``
    if the username is already taken.
    """
    with sqlite3.connect(db_path) as conn:
        first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, password_hash, 1 if first else 0),
        )
        conn.commit()
        return int(cur.lastrowid)

def get_user_by_username(
    username: str,
    db_path: str = DB_PATH,
) -> Optional[sqlite3.Row]:
    """Return the user row for ``username`` (case-insensitive) or ``None``."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        return cur.fetchone()

def get_user_by_id(
    user_id: int,
    db_path: str = DB_PATH,
) -> Optional[sqlite3.Row]:
    """Return the user row for ``user_id`` or ``None``."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()

def count_users(db_path: str = DB_PATH) -> int:
    """Return the total number of registered users."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM users")
        return int(cur.fetchone()[0])

def list_users(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return all users ordered by username."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, username, is_admin, created_at, last_login_at FROM users ORDER BY username ASC"
        )
        return cur.fetchall()

def update_last_login(user_id: int, db_path: str = DB_PATH) -> None:
    """Stamp ``last_login_at`` on the given user."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        conn.commit()

# 删除级联的 15 表清单：init_db 里的 _CONTENT_TABLES（见上方迁移段）11 表
# + content_briefs + multi-user-publishing-channels 的通道 3 表。两张清单
# 必须同步漂移：往 _CONTENT_TABLES 加表时这里也要加。
_DELETE_USER_TABLES = (
    "feeds", "episodes", "articles", "social_posts", "standalone_posts",
    "url_sources", "uploaded_images", "generated_thumbnails", "prompt_library",
    "content_brief_runs", "scheduled_posts",
    "content_briefs", "social_accounts", "agent_jobs", "agent_tokens",
)

def update_user_password(user_id: int, password_hash: str, *,
                         db_path: str = DB_PATH) -> bool:
    """Overwrite a user's password hash. True if the row existed."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )
        conn.commit()
        return cur.rowcount == 1

def is_last_admin(user_id: int, *, db_path: str = DB_PATH) -> bool:
    """True only when ``user_id`` is an admin and the instance has no other."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not row[0]:
            return False
        admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        return admins == 1

def count_user_rows(user_id: int, *, db_path: str = DB_PATH) -> Dict[str, int]:
    """Per-table row counts the delete confirmation dialog previews."""
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?",
                (user_id,)).fetchone()[0]
            for table in _DELETE_USER_TABLES
        }

def delete_user(user_id: int, *, db_path: str = DB_PATH,
                files_root: Optional[str] = None) -> Dict[str, int]:
    """Delete a user and every row they own, in one transaction.

    FKs are never enforced in this project (PRAGMA foreign_keys has never
    been on), so the cascade is explicit, table by table. activity_log and
    usage_events rows survive but lose their identity (user_id and username
    both set to NULL). Local files behind uploaded_images(storage='local')
    and generated_thumbnails rows are unlinked AFTER the commit; a missing
    or unremovable file is logged and skipped — the database is already
    committed and must not roll back for a filesystem side-effect.
    """
    if files_root is None:
        files_root = os.path.dirname(os.path.abspath(__file__))
    counts: Dict[str, int] = {}
    pending_files: List[str] = []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT url FROM uploaded_images "
            "WHERE user_id = ? AND storage = 'local' AND url IS NOT NULL",
            (user_id,)).fetchall()
        for (url,) in rows:
            # basename() pins the path under static/uploads whatever the row says.
            pending_files.append(os.path.join(
                files_root, "static", "uploads", os.path.basename(url)))
        rows = conn.execute(
            "SELECT image_relpath FROM generated_thumbnails "
            "WHERE user_id = ? AND image_relpath IS NOT NULL AND image_relpath != ''",
            (user_id,)).fetchall()
        for (relpath,) in rows:
            path = os.path.normpath(os.path.join(files_root, relpath))
            if path.startswith(os.path.normpath(files_root)):
                pending_files.append(path)
        for table in _DELETE_USER_TABLES:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            counts[table] = cur.rowcount
        for table in ("activity_log", "usage_events"):
            conn.execute(
                f"UPDATE {table} SET user_id = NULL, username = NULL "
                "WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        counts["users"] = cur.rowcount
        conn.commit()
    for path in pending_files:
        try:
            os.remove(path)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "delete_user: could not remove %s: %s", path, exc)
    return counts

# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_activity(
    action: str,
    *,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    endpoint: Optional[str] = None,
    target: Optional[str] = None,
    status_code: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    duration_ms: Optional[int] = None,
    details: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Append one row to ``activity_log`` and return its id."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO activity_log (
                user_id, username, action, method, path, endpoint,
                target, status_code, ip, user_agent, duration_ms, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                action,
                method,
                path,
                endpoint,
                target,
                status_code,
                ip,
                user_agent,
                duration_ms,
                details,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

def _activity_filter_clause(
    user_id: Optional[int],
    action: Optional[str],
    start_ts: Optional[str],
    end_ts: Optional[str],
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if action:
        clauses.append("action LIKE ?")
        params.append(f"%{action}%")
    if start_ts:
        clauses.append("ts >= ?")
        params.append(start_ts)
    if end_ts:
        clauses.append("ts <= ?")
        params.append(end_ts)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params

def list_activity(
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Return a page of activity rows matching the filters, newest first."""
    where, params = _activity_filter_clause(user_id, action, start_ts, end_ts)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT * FROM activity_log{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        )
        return cur.fetchall()

def count_activity(
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Return total number of activity rows matching the filters."""
    where, params = _activity_filter_clause(user_id, action, start_ts, end_ts)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(f"SELECT COUNT(*) FROM activity_log{where}", params)
        return int(cur.fetchone()[0])

def iter_activity_for_export(
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    db_path: str = DB_PATH,
) -> Iterable[sqlite3.Row]:
    """Yield activity rows matching the filters for CSV streaming."""
    where, params = _activity_filter_clause(user_id, action, start_ts, end_ts)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT * FROM activity_log{where} ORDER BY ts DESC, id DESC",
            params,
        )
        for row in cur:
            yield row
    finally:
        conn.close()

def distinct_activity_actions(db_path: str = DB_PATH) -> List[str]:
    """Return distinct action names in the activity log, alphabetical."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT DISTINCT action FROM activity_log ORDER BY action ASC"
        )
        return [row[0] for row in cur.fetchall() if row[0]]

# ---------------------------------------------------------------------------
# AI usage metering
# ---------------------------------------------------------------------------

def log_usage(
    *,
    mode: str,
    category: str,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
    cost_cny: float = 0.0,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    details: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Append one row to ``usage_events`` and return its id."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO usage_events (
                mode, category, provider, model,
                prompt_tokens, completion_tokens, total_tokens,
                audio_seconds, images, cost_cny,
                user_id, username, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                category,
                provider,
                model,
                int(prompt_tokens),
                int(completion_tokens),
                int(total_tokens),
                float(audio_seconds),
                int(images),
                float(cost_cny),
                user_id,
                username,
                details,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

def _usage_filter_clause(
    mode: Optional[str],
    category: Optional[str],
    start_ts: Optional[str],
    end_ts: Optional[str],
    user_id: Optional[int] = None,
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if start_ts:
        clauses.append("ts >= ?")
        params.append(start_ts)
    if end_ts:
        clauses.append("ts <= ?")
        params.append(end_ts)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params

def usage_totals(
    mode: Optional[str] = None,
    category: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> dict:
    """Return aggregate totals (cost, tokens, counts) for the given filters."""
    where, params = _usage_filter_clause(mode, category, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(cost_cny), 0),
                COALESCE(SUM(total_tokens), 0),
                COALESCE(SUM(prompt_tokens), 0),
                COALESCE(SUM(completion_tokens), 0),
                COALESCE(SUM(audio_seconds), 0),
                COALESCE(SUM(images), 0)
            FROM usage_events{where}
            """,
            params,
        )
        row = cur.fetchone()
        return {
            "events": int(row[0]),
            "cost_cny": float(row[1]),
            "total_tokens": int(row[2]),
            "prompt_tokens": int(row[3]),
            "completion_tokens": int(row[4]),
            "audio_seconds": float(row[5]),
            "images": int(row[6]),
        }

def usage_by_mode(
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> dict:
    """Return ``{mode: {events, cost_cny, total_tokens}}`` for the range."""
    where, params = _usage_filter_clause(None, None, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT mode, COUNT(*), COALESCE(SUM(cost_cny), 0), COALESCE(SUM(total_tokens), 0)
            FROM usage_events{where} GROUP BY mode
            """,
            params,
        )
        return {
            r[0]: {"events": int(r[1]), "cost_cny": float(r[2]), "total_tokens": int(r[3])}
            for r in cur.fetchall()
        }

def usage_by_category(
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[dict]:
    """Return per-category cost split into proactive / reactive / total, plus tokens."""
    where, params = _usage_filter_clause(None, None, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT category,
                   COALESCE(SUM(CASE WHEN mode='proactive' THEN cost_cny ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN mode='reactive'  THEN cost_cny ELSE 0 END), 0),
                   COALESCE(SUM(cost_cny), 0),
                   COALESCE(SUM(total_tokens), 0),
                   COUNT(*)
            FROM usage_events{where}
            GROUP BY category
            ORDER BY 4 DESC
            """,
            params,
        )
        return [
            {
                "category": r[0],
                "proactive_cost": float(r[1]),
                "reactive_cost": float(r[2]),
                "cost_cny": float(r[3]),
                "total_tokens": int(r[4]),
                "events": int(r[5]),
            }
            for r in cur.fetchall()
        ]

def usage_by_model(
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[dict]:
    """Return per-model / per-provider cost and token totals, biggest first."""
    where, params = _usage_filter_clause(None, None, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT model, provider, COUNT(*), COALESCE(SUM(cost_cny), 0),
                   COALESCE(SUM(total_tokens), 0)
            FROM usage_events{where}
            GROUP BY model, provider
            ORDER BY 4 DESC
            """,
            params,
        )
        return [
            {
                "model": r[0],
                "provider": r[1],
                "events": int(r[2]),
                "cost_cny": float(r[3]),
                "total_tokens": int(r[4]),
            }
            for r in cur.fetchall()
        ]

def usage_daily(
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[dict]:
    """Return per-day cost split into proactive / reactive / total, newest first."""
    where, params = _usage_filter_clause(None, None, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT date(ts) AS day,
                   COALESCE(SUM(CASE WHEN mode='proactive' THEN cost_cny ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN mode='reactive'  THEN cost_cny ELSE 0 END), 0),
                   COALESCE(SUM(cost_cny), 0),
                   COALESCE(SUM(total_tokens), 0),
                   COUNT(*)
            FROM usage_events{where}
            GROUP BY day
            ORDER BY day DESC
            """,
            params,
        )
        return [
            {
                "day": r[0],
                "proactive_cost": float(r[1]),
                "reactive_cost": float(r[2]),
                "cost_cny": float(r[3]),
                "total_tokens": int(r[4]),
                "events": int(r[5]),
            }
            for r in cur.fetchall()
        ]

def list_usage(
    mode: Optional[str] = None,
    category: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Return a page of usage-event rows matching the filters, newest first."""
    where, params = _usage_filter_clause(mode, category, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT * FROM usage_events{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        )
        return cur.fetchall()

def count_usage(
    mode: Optional[str] = None,
    category: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> int:
    """Return total number of usage-event rows matching the filters."""
    where, params = _usage_filter_clause(mode, category, start_ts, end_ts, user_id)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(f"SELECT COUNT(*) FROM usage_events{where}", params)
        return int(cur.fetchone()[0])

def iter_usage_for_export(
    mode: Optional[str] = None,
    category: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> Iterable[sqlite3.Row]:
    """Yield usage-event rows matching the filters for CSV streaming."""
    where, params = _usage_filter_clause(mode, category, start_ts, end_ts, user_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT * FROM usage_events{where} ORDER BY ts DESC, id DESC",
            params,
        )
        for row in cur:
            yield row
    finally:
        conn.close()

def distinct_usage_categories(user_id: Optional[int] = None,
                              db_path: str = DB_PATH) -> List[str]:
    """Return distinct content categories present in the usage log, alphabetical."""
    with sqlite3.connect(db_path) as conn:
        if user_id is not None:
            cur = conn.execute(
                "SELECT DISTINCT category FROM usage_events WHERE user_id = ? "
                "ORDER BY category ASC",
                (user_id,),
            )
        else:
            cur = conn.execute(
                "SELECT DISTINCT category FROM usage_events ORDER BY category ASC"
            )
        return [row[0] for row in cur.fetchall() if row[0]]

# --- Content Brief Functions (agentic content preparation) ---

# List-valued columns are stored as JSON strings; callers pass Python lists.
_BRIEF_LIST_FIELDS = ("platforms", "focus_sources", "must_include_keywords", "run_days")

# Columns that update_content_brief is allowed to write (guards the dynamic SQL).
_BRIEF_UPDATABLE = {
    "name", "instructions", "content_type", "platforms", "tone",
    "posts_per_platform", "article_count", "article_style", "focus_sources",
    "must_include_keywords", "audience_persona", "use_web_search",
    "use_saved_sources", "cadence", "run_time", "run_days", "next_run_at",
    "enabled", "auto_queue", "review_window_hours", "max_sources_per_run",
    "max_cost_cny", "max_drafts_per_run", "last_run_at", "last_run_status",
}

def _encode_brief_value(key: str, value):
    """Serialize a brief field for storage: JSON for list fields, 0/1 for bools."""
    if key in _BRIEF_LIST_FIELDS and isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    if isinstance(value, bool):
        return 1 if value else 0
    return value

def create_content_brief(
    name: str,
    instructions: str,
    content_type: str = "posts",
    platforms=None,
    tone: str = "professional",
    posts_per_platform: int = 3,
    article_count: int = 0,
    article_style: str = "blog",
    focus_sources=None,
    must_include_keywords=None,
    audience_persona: Optional[str] = None,
    use_web_search: bool = True,
    use_saved_sources: bool = True,
    cadence: str = "manual",
    run_time: Optional[str] = None,
    run_days=None,
    next_run_at: Optional[str] = None,
    enabled: bool = True,
    auto_queue: bool = False,
    review_window_hours: int = 24,
    max_sources_per_run: int = 5,
    max_cost_cny: float = 0.5,
    max_drafts_per_run: int = 30,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> int:
    """Create a content brief and return its id.

    List-valued arguments (``platforms``, ``focus_sources``,
    ``must_include_keywords``, ``run_days``) accept Python lists and are stored
    as JSON strings. ``user_id`` is required (the ``None`` default exists only
    because keyword-only-free syntax puts it after defaulted parameters).
    """
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO content_briefs (
                name, instructions, content_type, platforms, tone,
                posts_per_platform, article_count, article_style, focus_sources,
                must_include_keywords, audience_persona, use_web_search,
                use_saved_sources, cadence, run_time, run_days, next_run_at,
                enabled, auto_queue, review_window_hours, max_sources_per_run,
                max_cost_cny, max_drafts_per_run, created_at, updated_at, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, instructions, content_type,
                json.dumps(list(platforms or [])), tone,
                posts_per_platform, article_count, article_style,
                json.dumps(list(focus_sources or [])),
                json.dumps(list(must_include_keywords or [])),
                audience_persona, 1 if use_web_search else 0,
                1 if use_saved_sources else 0, cadence, run_time,
                json.dumps(list(run_days or [])), next_run_at,
                1 if enabled else 0, 1 if auto_queue else 0, review_window_hours,
                max_sources_per_run, max_cost_cny, max_drafts_per_run,
                now, now, uid,
            ),
        )
        conn.commit()
        return cur.lastrowid

def update_content_brief(brief_id: int, user_id: int, db_path: str = DB_PATH, **fields) -> None:
    """Update the given columns of a brief the user owns. Unknown keys are ignored.

    List-valued fields accept Python lists (JSON-encoded automatically).
    Ownership is not updatable: ``user_id`` is the scope, not a field.
    """
    uid = _require_user(user_id)
    updates = {k: v for k, v in fields.items() if k in _BRIEF_UPDATABLE}
    if not updates:
        return
    cols, params = [], []
    for key, value in updates.items():
        cols.append(f"{key} = ?")
        params.append(_encode_brief_value(key, value))
    cols.append("updated_at = ?")
    params.append(datetime.utcnow().isoformat(timespec="seconds"))
    params.extend([brief_id, uid])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE content_briefs SET {', '.join(cols)} WHERE id = ? AND user_id = ?",
            params,
        )
        conn.commit()

def get_content_brief(brief_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a single content brief row by id, if the user owns it."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM content_briefs WHERE id = ? AND user_id = ?",
            (brief_id, uid),
        )
        return cur.fetchone()

def get_content_brief_unscoped(brief_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a brief row by id with no owner filter.

    Worker entry point only (``content_agent.run_brief``): the due-briefs queue
    hands the worker an id before anyone knows the owner, and the row itself
    carries the ``user_id`` every later step scopes by. Anything user-facing
    goes through ``get_content_brief`` instead.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM content_briefs WHERE id = ?", (brief_id,))
        return cur.fetchone()

def list_content_briefs(user_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return the user's content briefs, most recently created first."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM content_briefs WHERE user_id = ? ORDER BY created_at DESC",
            (uid,),
        )
        return cur.fetchall()

def delete_content_brief(brief_id: int, user_id: int, db_path: str = DB_PATH) -> None:
    """Delete a brief the user owns, and all of its run history with it."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM content_brief_runs WHERE brief_id = ? AND user_id = ?",
            (brief_id, uid),
        )
        conn.execute(
            "DELETE FROM content_briefs WHERE id = ? AND user_id = ?",
            (brief_id, uid),
        )
        conn.commit()

def set_content_brief_enabled(brief_id: int, user_id: int, enabled: bool, db_path: str = DB_PATH) -> None:
    """Enable or disable a brief's recurring schedule."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE content_briefs SET enabled = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (1 if enabled else 0, datetime.utcnow().isoformat(timespec="seconds"), brief_id, uid),
        )
        conn.commit()

def set_content_brief_schedule(
    brief_id: int,
    user_id: int,
    next_run_at: Optional[str] = None,
    last_run_at: Optional[str] = None,
    last_run_status: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Update a brief's schedule bookkeeping after a run (or when (re)scheduling).

    ``next_run_at`` is always written (pass ``None`` to clear it, e.g. for a
    paused/manual brief). ``last_run_at``/``last_run_status`` are written only
    when provided.
    """
    uid = _require_user(user_id)
    sets, params = ["next_run_at = ?"], [next_run_at]
    if last_run_at is not None:
        sets.append("last_run_at = ?")
        params.append(last_run_at)
    if last_run_status is not None:
        sets.append("last_run_status = ?")
        params.append(last_run_status)
    params.extend([brief_id, uid])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE content_briefs SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            params,
        )
        conn.commit()

def get_due_content_briefs(now_iso: str, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return enabled, recurring briefs whose ``next_run_at`` is due (<= now).

    Deliberately unscoped: this is the worker's queue across all owners; each
    row's user_id decides whose sources and drafts a run acts for.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM content_briefs
            WHERE enabled = 1 AND cadence != 'manual'
              AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now_iso,),
        )
        return cur.fetchall()

def create_brief_run(brief_id: int, user_id: int, trigger: str = "manual", db_path: str = DB_PATH) -> int:
    """Insert a 'running' run row for a brief and return its id."""
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO content_brief_runs (brief_id, user_id, trigger, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (brief_id, uid, trigger, now),
        )
        conn.commit()
        return cur.lastrowid

def finalize_brief_run(
    run_id: int,
    user_id: int,
    status: str,
    sources_found: int = 0,
    sources_used: int = 0,
    posts_created: int = 0,
    articles_created: int = 0,
    cost_cny: float = 0.0,
    error_message: Optional[str] = None,
    log=None,
    db_path: str = DB_PATH,
) -> None:
    """Mark a run finished and record its outcome counts. ``log`` may be a list/dict."""
    uid = _require_user(user_id)
    now = datetime.utcnow().isoformat(timespec="seconds")
    log_str = json.dumps(log) if isinstance(log, (list, dict)) else log
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE content_brief_runs
            SET status = ?, finished_at = ?, sources_found = ?, sources_used = ?,
                posts_created = ?, articles_created = ?, cost_cny = ?,
                error_message = ?, log = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                status, now, sources_found, sources_used, posts_created,
                articles_created, cost_cny, error_message, log_str, run_id, uid,
            ),
        )
        conn.commit()

def get_brief_run(run_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a single run row by id, if the user owns it."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM content_brief_runs WHERE id = ? AND user_id = ?",
            (run_id, uid),
        )
        return cur.fetchone()

def list_brief_runs(brief_id: int, user_id: int, limit: int = 20, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return the user's recent runs for a brief, newest first."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM content_brief_runs WHERE brief_id = ? AND user_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (brief_id, uid, limit),
        )
        return cur.fetchall()

def get_active_brief_run(brief_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return the brief's currently-running run, if any (concurrency guard)."""
    uid = _require_user(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM content_brief_runs
            WHERE brief_id = ? AND user_id = ? AND status = 'running'
            ORDER BY started_at DESC LIMIT 1
            """,
            (brief_id, uid),
        )
        return cur.fetchone()

# ── Content Library ─────────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# Connected accounts
#
# One row per login. Everything that publishes names an account, so a platform
# can hold several logins and a single piece of copy can be aimed at any mix of
# them across any mix of platforms.
# ---------------------------------------------------------------------------

def list_social_accounts(
    platform: str | None = None,
    include_removed: bool = False,
    *,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Connected accounts, defaults first, then oldest first within a platform.

    ``user_id`` scopes the list to one user's logins; a NULL-owned row (there
    should be none after the upgrade backfill) is invisible to every user.
    """
    sql = "SELECT * FROM social_accounts"
    clauses, params = [], []
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if not include_removed:
        clauses.append("status != 'removed'")
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY platform, is_default DESC, id"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()

def get_social_account(account_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """One account by id, or None."""
    if not account_id:
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM social_accounts WHERE id = ?", (account_id,)
        ).fetchone()

def find_social_account(
    platform: str, external_id: str, db_path: str = DB_PATH
) -> Optional[sqlite3.Row]:
    """The account for a platform's own id, or None if it was never connected."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM social_accounts WHERE platform = ? AND external_id = ?",
            (platform, str(external_id)),
        ).fetchone()

def get_default_social_account(
    platform: str, user_id: Optional[int] = None, db_path: str = DB_PATH
) -> Optional[sqlite3.Row]:
    """The account a bare platform name means, or None if none are connected.

    ``user_id`` scopes the pool to that user's logins; None reads the global
    pool, which only the pre-user gates and internal tooling still do.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if user_id is None:
            return conn.execute(
                "SELECT * FROM social_accounts WHERE platform = ? AND status != 'removed' "
                "ORDER BY is_default DESC, id LIMIT 1",
                (platform,),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM social_accounts WHERE platform = ? AND status != 'removed' "
            "AND user_id = ? ORDER BY is_default DESC, id LIMIT 1",
            (platform, user_id),
        ).fetchone()

def upsert_social_account(
    platform: str,
    external_id: str,
    display_name: str | None = None,
    handle: str | None = None,
    avatar_url: str | None = None,
    label: str | None = None,
    *,
    user_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> int:
    """Record a connected login, returning its account id.

    Called from every OAuth callback. Re-authorising an account already
    connected refreshes it in place; authorising a different one adds a second
    account rather than replacing the first, which is the whole point.
    ``user_id`` owns the row; None stores NULL, which no user-scoped read
    ever sees, so an unowned account isolates itself.
    """
    with sqlite3.connect(db_path) as conn:
        account_id = _upsert_account_row(
            conn,
            platform=platform,
            external_id=str(external_id),
            display_name=display_name,
            handle=handle,
            avatar_url=avatar_url,
            label=label,
            user_id=user_id,
        )
        conn.commit()
        return account_id

def set_default_social_account(account_id: int, db_path: str = DB_PATH) -> bool:
    """Make one account the platform default. False if the id is unknown."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT platform FROM social_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE social_accounts SET is_default = 0 WHERE platform = ?", (row[0],)
        )
        conn.execute(
            "UPDATE social_accounts SET is_default = 1, updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(timespec="seconds"), account_id),
        )
        conn.commit()
        return True

def set_social_account_label(
    account_id: int, label: str | None, db_path: str = DB_PATH
) -> bool:
    """Name an account ("Work", "Brand"). Blank clears back to the profile name."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE social_accounts SET label = ?, updated_at = ? WHERE id = ?",
            ((label or "").strip() or None,
             datetime.utcnow().isoformat(timespec="seconds"), account_id),
        )
        conn.commit()
        return cur.rowcount > 0

def set_social_account_status(
    account_id: int, status: str, db_path: str = DB_PATH
) -> bool:
    """Flag an account 'active' or 'expired' so the UI can show it needs a reconnect."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE social_accounts SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(timespec="seconds"), account_id),
        )
        conn.commit()
        return cur.rowcount > 0

def delete_social_account(account_id: int, db_path: str = DB_PATH) -> bool:
    """Disconnect one account: drop its token and hand the default on.

    Only this account's token goes; the platform's other logins keep working.
    Posts and queue entries that named it fall back to NULL, which resolves to
    whichever account is default when they publish, so nothing is orphaned.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT platform, is_default FROM social_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not row:
            return False
        platform, was_default = row[0], row[1]
        for table in ("standalone_posts", "social_posts", "scheduled_posts"):
            conn.execute(
                f"UPDATE {table} SET account_id = NULL WHERE account_id = ?",
                (account_id,),
            )
        conn.execute("DELETE FROM social_accounts WHERE id = ?", (account_id,))
        if was_default:
            heir = _default_account_id(conn, platform)
            if heir:
                conn.execute(
                    "UPDATE social_accounts SET is_default = 1 WHERE id = ?", (heir,)
                )
        conn.commit()
        return True

def resolve_account_id(
    platform: str, account_id: int | None = None, db_path: str = DB_PATH,
    *, user_id: Optional[int] = None,
) -> Optional[int]:
    """The account a publish should use, given an explicit id or none.

    An id that belongs to another platform (a stale pick from the browser) is
    refused rather than quietly redirected, because posting a card to the wrong
    account is worse than not posting it. ``user_id`` adds the same refusal for
    another user's account — an id that exists but is not this user's reads as
    the same None as one that does not exist, so a foreign id from a hand-built
    form is indistinguishable from a stale one. The default branch reads only
    that user's logins; without ``user_id`` the global pool stands, which is the
    pre-user view the gates and tooling still rely on.
    """
    if account_id:
        account = get_social_account(int(account_id), db_path=db_path)
        if not account or account["platform"] != platform:
            return None
        if user_id is not None and account["user_id"] != user_id:
            return None
        return account["id"]
    default = get_default_social_account(platform, user_id=user_id, db_path=db_path)
    return default["id"] if default else None

def count_social_accounts(platform: str | None = None, db_path: str = DB_PATH) -> int:
    """How many accounts are connected, for a platform or in total."""
    with sqlite3.connect(db_path) as conn:
        if platform:
            cur = conn.execute(
                "SELECT COUNT(*) FROM social_accounts "
                "WHERE platform = ? AND status != 'removed'",
                (platform,),
            )
        else:
            cur = conn.execute(
                "SELECT COUNT(*) FROM social_accounts WHERE status != 'removed'"
            )
        return cur.fetchone()[0]

# ---------------------------------------------------------------------------
# Agent jobs: the queue the local publishing agent drains over HTTPS.
# ---------------------------------------------------------------------------

_BACKOFF_SECONDS = (60, 300, 900, 3600)

def enqueue_agent_job(kind, payload, *, run_after=None, max_attempts=5,
                      article_id=None, created_by=None, user_id=None,
                      db_path=DB_PATH):
    """Queue one job for the local agent; ``payload`` is a JSON string.

    ``user_id`` is the job's owner, the same write-inlet convention as the
    content tables. It is required: a NULL owner is a job no token can claim
    and no page can read, which would sit pending forever — failing loudly at
    the inlet beats a silent stall in the queue.
    """
    if user_id is None:
        raise ValueError("enqueue_agent_job requires user_id")
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_jobs (kind, payload, status, run_after,
                                    max_attempts, article_id, created_by,
                                    user_id, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, payload, run_after, max_attempts, article_id, created_by,
             user_id, now, now),
        )
    return cur.lastrowid

def _job_matches_executor(payload_text, executor, platforms):
    """Whether one queued job may go to this claimant.

    ``executor`` defaults to the old agent lane, so an unmarked request body
    and an unstamped payload meet on "agent" without either side changing.
    A payload that does not parse is nobody's job: skip it rather than let
    one broken row wedge the claim transaction.
    """
    try:
        payload = json.loads(payload_text or "{}")
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    if (payload.get("executor") or "agent") != (executor or "agent"):
        return False
    if platforms is None:
        return True
    targets = [i.get("target") for i in payload.get("items") or []
               if isinstance(i, dict)]
    return bool(targets) and all(t in platforms for t in targets)

def claim_agent_job(*, user_id, kinds=("article_sync",), agent="",
                    stale_minutes=15, executor=None, platforms=None,
                    db_path=DB_PATH):
    """Hand the oldest due job belonging to ``user_id`` to one agent.

    A claim older than ``stale_minutes`` is recycled first, so a killed agent
    cannot pin a job forever. The same rule covers ``running`` rows:
    ``claimed_at`` survives the started event, and a run past the deadline is
    a dead agent too — the window (15 minutes) is comfortably longer than the
    longest a live agent can hold a job (~330s of wechatsync sync). BEGIN
    IMMEDIATE keeps two pollers from taking the same row even on SQLite
    builds without RETURNING.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    marks = ",".join("?" for _ in kinds)
    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE agent_jobs SET status = 'pending', claimed_by = NULL,
                       claimed_at = NULL, updated_at = ?
                WHERE status IN ('claimed', 'running')
                  AND claimed_at IS NOT NULL
                  AND claimed_at < ?
                """,
                (now,
                 (datetime.utcnow()
                  - timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")),
            )
            # Page through every pending candidate inside the transaction:
            # stopping at the first batch would let a run of the other lane's
            # jobs starve a matching one behind them.
            chosen = None
            last_id = 0
            while chosen is None:
                rows = conn.execute(
                    f"""
                    SELECT id, payload FROM agent_jobs
                    WHERE status = 'pending' AND kind IN ({marks})
                      AND (run_after IS NULL OR run_after <= ?)
                      AND user_id = ?
                      AND id > ?
                    ORDER BY id LIMIT 100
                    """,
                    (*kinds, now, user_id, last_id),
                ).fetchall()
                if not rows:
                    break
                chosen = next((rid for rid, text in rows
                               if _job_matches_executor(text, executor, platforms)),
                              None)
                last_id = rows[-1][0]
            if chosen is None:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE agent_jobs SET status = 'claimed', claimed_by = ?,
                       claimed_at = ?, updated_at = ? WHERE id = ?
                """,
                (agent, now, now, chosen),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_agent_job(chosen, db_path=db_path)

def record_agent_job_event(job_id, event, *, db_path=DB_PATH):
    """Apply one agent event (started/done/failed) to a job; returns the row.

    ``done`` carries per-platform ``results``; all-success is succeeded, any
    success with any failure is partial. ``failed`` requeues only when the
    event says ``retryable`` and attempts remain.

    An event of an unknown type is a protocol error and is refused wherever
    the job stands. But a job already in a terminal state (``succeeded``,
    ``partial``, ``failed``) answers every well-formed late event with the
    row unchanged: after a reclaim the old and the new agent may both report,
    and double-reporting is not an error a reporter can fix.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    etype = (event or {}).get("type")
    if etype not in ("started", "done", "failed"):
        raise ValueError(f"unknown agent job event type: {etype!r}")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"agent job {job_id} does not exist")
        if row["status"] in ("succeeded", "partial", "failed"):
            return get_agent_job(job_id, db_path=db_path)
        attempts = row["attempts"] or 0
        max_attempts = row["max_attempts"] or 5

        if etype == "started":
            status, run_after, result = "running", row["run_after"], row["result"]
        elif etype == "done":
            results = event.get("results") or []
            ok = [r for r in results if r.get("success")]
            if results and len(ok) == len(results):
                status = "succeeded"
            elif ok:
                status = "partial"
            else:
                status = "failed"
            run_after = row["run_after"]
            result = json.dumps(results, ensure_ascii=False)
        elif etype == "failed":
            result = json.dumps(
                [{"error": event.get("error", "unknown"),
                  "code": event.get("code"), "retryable": bool(event.get("retryable"))}],
                ensure_ascii=False)
            # Every retryable failure burns an attempt; the job goes back in the
            # queue only while attempts remain, otherwise it lands as terminal.
            if event.get("retryable"):
                attempts += 1
            if event.get("retryable") and attempts < max_attempts:
                status = "pending"
                delay = _BACKOFF_SECONDS[min(attempts - 1, len(_BACKOFF_SECONDS) - 1)]
                run_after = (datetime.utcnow()
                             + timedelta(seconds=delay)).isoformat(timespec="seconds")
            else:
                status, run_after = "failed", row["run_after"]

        conn.execute(
            """
            UPDATE agent_jobs SET status = ?, attempts = ?, run_after = ?,
                   result = COALESCE(?, result), updated_at = ? WHERE id = ?
            """,
            (status, attempts, run_after, result, now, job_id),
        )
    return get_agent_job(job_id, db_path=db_path)

def get_agent_job(job_id, *, db_path=DB_PATH):
    """One agent job row as a dict, or None."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None

def list_agent_jobs(*, limit=100, user_id=None, db_path=DB_PATH):
    """Agent jobs, newest first.

    ``user_id`` scopes the listing to one owner; None means every owner and
    is for the gates and internal tooling only — page routes must pass the
    viewer's own id.
    """
    sql = "SELECT * FROM agent_jobs"
    params: list = []
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params.append(user_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def hash_agent_token(token: str) -> str:
    """sha256 hexdigest of a plaintext agent token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_agent_token(user_id: int, token: str, label: str = "",
                       db_path: str = DB_PATH) -> int:
    """Store the hash of ``token`` for ``user_id`` and return the new row id.

    The plaintext ``token`` is never persisted; callers should hand it to the
    user exactly once, usually in the issuance response.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_tokens (token_hash, user_id, label, created_at, revoked)
            VALUES (?, ?, ?, ?, 0)
            """,
            (hash_agent_token(token), user_id, label, now),
        )
        conn.commit()
        return cur.lastrowid


def find_agent_user_by_token(token: str, db_path: str = DB_PATH) -> Optional[int]:
    """Return the user_id for an active (not revoked) token, or None."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT user_id FROM agent_tokens
            WHERE token_hash = ? AND revoked = 0
            """,
            (hash_agent_token(token),),
        ).fetchone()
    return row[0] if row else None


def touch_agent_token(token: str, db_path: str = DB_PATH) -> None:
    """Update ``last_seen`` for the active token matching ``token``."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE agent_tokens SET last_seen = ?
            WHERE token_hash = ? AND revoked = 0
            """,
            (now, hash_agent_token(token)),
        )
        conn.commit()


def list_agent_tokens(user_id: int, db_path: str = DB_PATH) -> List[dict]:
    """Return a user's tokens as dicts with id/label/created_at/last_seen/revoked.

    Plaintext tokens and their hashes are never included.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, label, created_at, last_seen, revoked
            FROM agent_tokens
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_agent_token(token_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Revoke one of ``user_id``'s tokens. Returns True if a row was updated."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE agent_tokens SET revoked = 1
            WHERE id = ? AND user_id = ?
            """,
            (token_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
