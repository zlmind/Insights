"""Queue a compose card for the local agent's article pipeline.

The compose page saves one row per (card, platform). For the domestic article
platforms a row stops being "copy it by hand" the moment the local agent is
online: pushing the row enqueues an ``article_sync`` job whose payload is the
row's own copy, verbatim, one ``{target, content}`` item. The agent drives
wechatsync per item and reports per-platform results, so a partial success is
recorded where it happened, not flattened.

Enqueueing is idempotent while a job is still in flight (pending, claimed or
running): pushing twice queues once. Once a job is finished, the same card can
be pushed again — re-pushing after a fix is a normal thing to want.
"""

from __future__ import annotations

import json

import database

DOMESTIC_ARTICLE_PLATFORMS = ("wechat", "zhihu", "toutiao")

DISPLAY_NAMES = {
    "wechat": "公众号",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "zhihu": "知乎",
    "toutiao": "头条号",
}


def display_name(platform: str) -> str:
    return DISPLAY_NAMES.get(platform, (platform or "").capitalize())


def enabled_platforms(db_path=None) -> list[str]:
    """The platforms the admin switched on, in registry order.

    Until the admin saves the setting, every article platform is on — the
    same default the settings page offers. A fresh install must not have to
    visit /settings before its first push works.
    """
    raw = database.get_setting("WECHATSYNC_ENABLED_PLATFORMS", "") \
        if db_path is None else database.get_setting(
            "WECHATSYNC_ENABLED_PLATFORMS", "", db_path=db_path)
    picked = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not picked:
        picked = list(DOMESTIC_ARTICLE_PLATFORMS)
    return [p for p in DOMESTIC_ARTICLE_PLATFORMS if p in picked]


def _payload_for(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else ()
    title = row["title"] if "title" in keys else None
    return {
        "title": title or display_name(row["platform"]) + "草稿",
        "items": [{"target": row["platform"], "content": row["content"] or ""}],
        "post_id": row["id"],
    }


def enqueue_card_push(row, *, run_after=None, created_by=None, db_path=None):
    """Queue one card row for the agent; idempotent while a job is in flight.

    Idempotence is per lane class: an immediate push and a scheduled push of
    the same card are different requests, so a 9:30 schedule never satisfies
    a "push it now" click (and the other way round). Two clicks on the same
    class still queue once. The class is a NULL test, not a string compare:
    a server retry rewrites a stored run_after in its own format, and an
    exact match would then split one lane in two.
    """
    kwargs = {} if db_path is None else {"db_path": db_path}
    payload = json.dumps(_payload_for(row), ensure_ascii=False)
    with database.sqlite3.connect(kwargs.get("db_path", database.DB_PATH)) as conn:
        existing = conn.execute(
            """
            SELECT id FROM agent_jobs
            WHERE kind = 'article_sync' AND payload = ?
              AND status IN ('pending', 'claimed', 'running')
              AND (run_after IS NULL) = (? IS NULL)  -- same lane class
            ORDER BY id DESC LIMIT 1
            """,
            (payload, run_after),
        ).fetchone()
    if existing:
        return {"job_id": existing[0], "already": True}
    job_id = database.enqueue_agent_job(
        "article_sync", payload, run_after=run_after,
        article_id=row["article_id"] if "article_id" in row.keys() else None,
        created_by=created_by, **kwargs,
    )
    return {"job_id": job_id, "already": False}
