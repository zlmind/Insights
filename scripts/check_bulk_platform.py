"""Gates for bulk "Add platform" on the Compose page.

One script, one section per claim, picked by argument so each gate reports its
own token only after proving its claim against the real routes and the real
schema:

    python scripts/check_bulk_platform.py guard    SINGLE_CARD_GUARD_OK
    python scripts/check_bulk_platform.py add      BULK_ADD_OK
    python scripts/check_bulk_platform.py queue    BULK_QUEUE_OK
    python scripts/check_bulk_platform.py select   BULK_SELECT_OK
    python scripts/check_bulk_platform.py remove       BULK_REMOVE_OK
    python scripts/check_bulk_platform.py removeselect BULK_REMOVE_SELECT_OK

The app is Chinese-first: the offered platforms are xiaohongshu, douyin and
wechat, and all three are draft-only — there is no posting API, so a queued
entry could never go out. Adding a trio target therefore only ever adds rows;
with ``queue`` set, every trio row is skipped with the reason that says to copy
the post and publish it manually.

The Western platforms (linkedin, threads, twitter, facebook, instagram) are
hidden, not removed. Every route that creates or moves a target validates
against the offered trio and refuses a Western one, so no new Western row can
come into being through the app. What still works for them is the machinery on
rows that already exist: the single-card queue, the whole-card queue and the
publish fan-out. This gate seeds such rows straight into the database
(``Rig.card``), which is exactly what a legacy row is, and uses them to pin the
queueing behaviour the trio can never reach — including the fact that a legacy
row can be queued but no longer unticked, since the tick route refuses its
platform before it looks at the action.

``guard`` pins the behaviour of the single-card endpoints (tick a platform,
queue a card) that the bulk endpoint shares code with.

Every section runs on a throwaway database with fake platform clients, so it
makes no network calls and cannot touch a real insights.db or a real account.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import (  # noqa: E402
    isolated_app, install_fake_clients, connect, check,
)

BULK = "/compose/posts/bulk-add-platform"
BULK_REMOVE = "/compose/posts/bulk-remove-platform"
IMAGE = "https://example.test/pic.jpg"

# What the app offers, and what a draft-only skip says, in the server's words.
OFFERED = ("xiaohongshu", "douyin", "wechat")
DRAFT_ONLY_SKIP = "{0} is draft-only: copy the post and publish it manually."


class Rig:
    """An isolated app with the legacy Western platforms connected and slots to
    queue into.

    The offered trio has no OAuth, so it has no accounts to connect: a trio row
    always aims at no account at all. The Western platforms stay connected here
    because their queue machinery is still live — but only for rows seeded
    directly, which is to say legacy rows.
    """

    def __init__(self, platforms=("linkedin", "threads", "twitter", "facebook", "instagram"),
                 slot_platforms=None):
        self.directory, self.database, self.web, self.publisher, self.client = isolated_app()
        self.P = self.database.DB_PATH
        install_fake_clients(self.publisher, self.web)

        # Hermetic: the repo's real .env leaks in through load_dotenv(), so the
        # link-image worker and the stock-image lookup would otherwise reach the
        # network with the user's real keys.
        self.web._maybe_attach_link_image = lambda *a, **k: None
        self.stock_calls = []
        self.stock_url = None
        self.web.get_image_for_post = self._stock

        self.accounts = {}
        ids = {"linkedin": "li-work", "threads": "th-1", "twitter": "tw-1",
               "facebook": "fb-1", "instagram": "ig-1"}
        labels = {"linkedin": "Work", "threads": "brandco", "twitter": "brandco",
                  "facebook": "Brand Page", "instagram": "brandco"}
        for platform in platforms:
            self.accounts[platform] = connect(
                self.database, platform, ids[platform], labels[platform])

        # Two slots a day, every day, so a batch has room to spread out.
        for hhmm in ("09:00", "18:00"):
            self.database.add_time_slot(
                -1, hhmm, True, list(slot_platforms) if slot_platforms else None,
                db_path=self.P)

    def _stock(self, content):
        self.stock_calls.append(content)
        return self.stock_url

    # --- seeding -----------------------------------------------------------
    def card(self, content, targets, image_url=IMAGE, brief_id=None):
        """One saved row per target, sharing copy and image: one Compose card.

        ``targets`` is a list of ``platform`` or ``(platform, account_id)``.
        A trio target takes no account — there is nothing to connect — so it
        seeds the account-less row the routes would write. Seeding a Western
        target here makes a legacy row: no route creates those any more.
        Returns ``{platform_or_key: row_id}`` in the order given.
        """
        ids = {}
        for target in targets:
            platform, account_id = target if isinstance(target, tuple) else (target, None)
            row_id = self.database.add_standalone_post(
                source_type="manual", source_content="gate", platform=platform,
                content=content, image_url=image_url, brief_id=brief_id,
                account_id=account_id, db_path=self.P)
            ids[platform if account_id is None else f"{platform}:{account_id}"] = row_id
        return ids

    # --- reading -----------------------------------------------------------
    def rows(self):
        return [dict(r) for r in self.database.list_standalone_posts(db_path=self.P)]

    def cards(self):
        return self.web._group_standalone_posts(
            self.database.list_standalone_posts(db_path=self.P))

    def rows_for(self, content):
        return [r for r in self.rows() if r["content"] == content]

    def targets_of(self, content):
        return sorted((r["platform"], r["account_id"]) for r in self.rows_for(content))

    def pending(self):
        return [dict(s) for s in self.database.list_scheduled_posts(status="pending", db_path=self.P)]

    def pending_for(self, row_id):
        return [s for s in self.pending() if s["standalone_post_id"] == row_id]

    def post(self, payload, url=BULK):
        response = self.client.post(url, json=payload)
        return response.status_code, (response.get_json(silent=True) or {})

    def queue_one(self, row_id, platform):
        """The single-card queue route: the road a legacy Western row still takes."""
        return self.client.post(f"/compose/post/{row_id}/queue", data={"platform": platform})


def trim(body):
    """A reply for a failure message, without the card HTML that would bury it."""
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if k not in ("html", "results")}
    return body


def by_target(res):
    """The reply's per-target breakdown keyed by (platform, account id)."""
    return {(t["platform"], t["account_id"]): t for t in res["by_target"]}


# ===========================================================================
# guard: the single-card endpoints behave the way the bulk endpoint assumes
# ===========================================================================
def section_guard():
    rig = Rig()
    db, P = rig.database, rig.P
    linkedin_default = rig.accounts["linkedin"]

    brief = db.create_content_brief("Gate brief", "make posts", db_path=P)
    ids = rig.card("Guarded card", ["linkedin", "threads"], brief_id=brief)
    li, th = ids["linkedin"], ids["threads"]
    card_ids = ",".join(str(i) for i in ids.values())

    # -- add a trio platform: copy, image and brief travel; the card stays one card
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "wechat", "action": "add", "post_ids": card_ids}).get_json()
    check(res and res["success"] and res["action"] == "add", f"tick wechat failed: {trim(res)}")
    wc = res["post_id"]
    row = dict(db.get_standalone_post(wc, db_path=P))
    check(row["content"] == "Guarded card" and row["image_url"] == IMAGE,
          f"ticked row did not copy the card's copy/image: {row}")
    check(row["brief_id"] == brief, "ticked row lost the card's brief")
    check(row["platform"] == "wechat" and row["account_id"] is None,
          f"a trio row must aim at no account (the trio has none to connect): {row}")
    check(sorted(res["post_ids"]) == sorted([li, th, wc]),
          f"reply did not list the card's rows: {res['post_ids']}")
    check('data-platform="wechat"' in res["html"], "reply did not re-render the card")
    check(len(rig.cards()) == 1, "ticking a platform split the card")

    # -- ticking it again is refused, writing nothing. A trio row carries no
    #    account at all — that is the only kind of trio row there is — so this is
    #    also the old "a legacy account-less row still counts as its platform's
    #    tick" claim, which used to need a hand-nulled LinkedIn row.
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "wechat", "action": "add", "post_ids": card_ids + f",{wc}"})
    check(res.status_code == 400 and "already goes to" in res.get_json()["error"],
          f"a repeat tick was not refused: {res.status_code} {trim(res.get_json())}")
    check(len(rig.rows_for("Guarded card")) == 3, "a refused tick still wrote a row")

    # -- a hidden Western platform is refused, adding and removing alike. The
    #    offered trio is the whole gate, so a legacy row can be queued but never
    #    unticked: the route refuses the platform before it reads the action.
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "linkedin", "action": "add", "post_ids": card_ids})
    check(res.status_code == 400
          and res.get_json()["error"] == "Invalid platform. Must be one of: xiaohongshu, douyin, wechat",
          f"a hidden platform was not refused in the server's words: {res.status_code} {trim(res.get_json())}")
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "twitter", "action": "remove", "post_ids": card_ids})
    check(res.status_code == 400 and "Invalid platform" in res.get_json()["error"],
          f"unticking a hidden platform was not refused: {res.status_code} {trim(res.get_json())}")

    # -- an unknown platform, and an account id on a platform with no accounts,
    #    are both refused
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "myspace", "action": "add"})
    check(res.status_code == 400, f"unknown platform was not refused: {res.status_code}")
    res = rig.client.post(f"/compose/post/{li}/platform", data={
        "platform": "wechat", "account_id": str(rig.accounts["threads"]), "action": "add"})
    check(res.status_code == 400 and "not connected" in res.get_json()["error"],
          f"an account the platform does not own was not refused: {res.status_code} {trim(res.get_json())}")

    # -- a legacy Western row still queues through the single-card route, and
    #    the queue entry carries the row's account
    legacy = rig.card("Guarded legacy row", ["twitter"])
    tw = legacy["twitter"]
    q = rig.queue_one(tw, "twitter").get_json()
    check(q and q["success"], f"queueing a legacy Western row failed: {q}")
    entries = rig.pending_for(tw)
    check(len(entries) == 1 and entries[0]["platform"] == "twitter"
          and entries[0]["account_id"] == rig.accounts["twitter"],
          f"the queue entry does not carry the legacy row's platform and account: {entries}")

    # -- unticking that queued row is refused rather than prompted: the 409
    #    ask-first flow is behind the platform check now. Nothing may be lost.
    res = rig.client.post(f"/compose/post/{tw}/platform", data={
        "platform": "twitter", "action": "remove", "post_ids": str(tw)})
    body = res.get_json()
    check(res.status_code == 400 and "Invalid platform" in body.get("error", ""),
          f"a queued legacy row was not protected from an untick: {res.status_code} {body}")
    check(db.get_standalone_post(tw, db_path=P) is not None and len(rig.pending_for(tw)) == 1,
          "the refused untick still deleted the row or its queue entry")

    # -- queueing a draft-only row is refused, in the same words everywhere: it
    #    could never go out
    res = rig.queue_one(wc, "wechat")
    body = res.get_json()
    check(res.status_code == 400 and body.get("error") == DRAFT_ONLY_SKIP.format("wechat"),
          f"a draft-only row was queued or refused without the way out: {res.status_code} {body}")
    check(not rig.pending_for(wc), "a draft-only row entered the queue")

    # -- queue the whole legacy card: one slot per row, then a repeat reports "already"
    ids2 = rig.card("Whole card", ["linkedin", "threads"])
    all2 = ",".join(str(i) for i in ids2.values())
    res = rig.client.post(f"/compose/post/{ids2['linkedin']}/queue",
                          data={"all": "1", "post_ids": all2})
    body = res.get_json()
    check(res.status_code == 200 and body["success"] and len(body["queued"]) == 2
          and not body["skipped"], f"queue-whole-card failed: {res.status_code} {body}")
    check(len(rig.pending_for(ids2["linkedin"])) == 1 and len(rig.pending_for(ids2["threads"])) == 1,
          "queue-whole-card did not queue each row exactly once")
    li_entry = rig.pending_for(ids2["linkedin"])[0]
    check(li_entry["account_id"] == linkedin_default and li_entry["platform"] == "linkedin",
          f"queue entry does not carry the row's account: {li_entry}")

    res = rig.client.post(f"/compose/post/{ids2['linkedin']}/queue",
                          data={"all": "1", "post_ids": all2})
    body = res.get_json()
    check(res.status_code == 400 and not body["success"] and len(body["skipped"]) == 2
          and all("Already queued" in s["error"] for s in body["skipped"]),
          f"re-queueing a queued card was not reported as already queued: {res.status_code} {body}")
    check(len(rig.pending_for(ids2["linkedin"])) == 1, "a repeat queue double-booked a row")

    # -- a draft-only row on a mixed card is skipped by the whole-card queue,
    #    with the same words the other queue paths use, while the legacy row
    #    beside it still queues
    ids3 = rig.card("Mixed card", ["linkedin", "wechat"])
    all3 = ",".join(str(i) for i in ids3.values())
    res = rig.client.post(f"/compose/post/{ids3['linkedin']}/queue",
                          data={"all": "1", "post_ids": all3})
    body = res.get_json()
    check(res.status_code == 200 and body["partial"] and len(body["queued"]) == 1
          and body["queued"][0]["platform"] == "linkedin"
          and len(body["skipped"]) == 1 and body["skipped"][0]["platform"] == "wechat"
          and body["skipped"][0]["error"] == DRAFT_ONLY_SKIP.format("wechat"),
          f"a mixed card did not queue around its draft-only row: {res.status_code} {body}")
    check(not rig.pending_for(ids3["wechat"]), "a draft-only row was queued by a card-wide queue")

    # -- a legacy Instagram row with no usable media is skipped, not queued, and
    #    reported. Instagram is hidden now, so such rows exist only as legacy
    #    ones, and this whole-card road is the one that still reaches them.
    ids4 = rig.card("Instagram card", ["linkedin", "instagram"], image_url=None)
    all4 = ",".join(str(i) for i in ids4.values())
    res = rig.client.post(f"/compose/post/{ids4['linkedin']}/queue",
                          data={"all": "1", "post_ids": all4})
    body = res.get_json()
    check(res.status_code == 200 and body["partial"] and len(body["queued"]) == 1
          and body["queued"][0]["platform"] == "linkedin"
          and len(body["skipped"]) == 1 and body["skipped"][0]["platform"] == "instagram"
          and "image" in body["skipped"][0]["error"].lower(),
          f"a card with an image-less Instagram row did not queue partially: {res.status_code} {body}")
    check(not rig.pending_for(ids4["instagram"]), "an image-less Instagram row was queued")

    print("single-card platform tick and queue-whole-card behave as pinned: the trio ticks, the West does not")
    print("SINGLE_CARD_GUARD_OK")


# ===========================================================================
# add: every selected card gets the chosen targets
# ===========================================================================
def section_add():
    rig = Rig()
    db, P = rig.database, rig.P

    brief = db.create_content_brief("Gate brief", "make posts", db_path=P)
    # Four cards: a wechat-only one, a wechat+xiaohongshu one, one that already
    # has douyin, and one that is not selected.
    a = rig.card("Copy A", ["wechat"], brief_id=brief)
    b = rig.card("Copy B", ["wechat", "xiaohongshu"], image_url=None)
    c = rig.card("Copy C", ["wechat", "douyin"])
    rig.card("Copy D", ["xiaohongshu"])
    check(len(rig.cards()) == 4, "the seed did not make 4 cards")

    status, res = rig.post({
        "post_ids": [a["wechat"], b["wechat"], c["wechat"]],
        "targets": ["douyin"], "queue": False})
    check(status == 200 and res.get("success"), f"bulk add failed: {status} {trim(res)}")
    check(res["cards"] == 3, f"expected 3 cards considered, got {res['cards']}")
    check(res["added"] == 2, f"expected 2 rows added (C already had douyin), got {res['added']}")
    check(res["already"] == 1, f"expected 1 already-there, got {res['already']}")
    check(res["queued"] == 0 and res["already_queued"] == 0, "queue=false still queued something")

    # Each new row carries the card's exact copy, image and brief, and aims at
    # no account: the offered trio has no logins to aim at.
    for content, image, has_brief in (("Copy A", IMAGE, True), ("Copy B", None, False)):
        new = [r for r in rig.rows_for(content) if r["platform"] == "douyin"]
        check(len(new) == 1, f"{content}: expected exactly one douyin row, got {len(new)}")
        check(new[0]["content"] == content and new[0]["image_url"] == image,
              f"{content}: new row did not copy copy/image: {new[0]}")
        check(new[0]["account_id"] is None,
              f"{content}: a trio row must not name an account: {new[0]}")
        check((new[0]["brief_id"] == brief) == has_brief, f"{content}: brief not carried")
        check(new[0]["used"] == 0, f"{content}: new row was born used")
    check(len([r for r in rig.rows_for("Copy C") if r["platform"] == "douyin"]) == 1,
          "Copy C already had douyin and must still have exactly one")
    check([r["platform"] for r in rig.rows_for("Copy D")] == ["xiaohongshu"],
          "an unselected card was changed")

    # Nothing split: still 4 cards, and each selected one is one card of the right size.
    cards = rig.cards()
    check(len(cards) == 4, f"the bulk add split or merged cards: {len(cards)} cards")
    sizes = sorted(len(g["platforms"]) for g in cards)
    check(sizes == [1, 2, 2, 3], f"unexpected card sizes {sizes}")

    # A hidden Western platform is not a target any more, and is refused before
    # anything is written.
    before = len(rig.rows())
    status, res = rig.post({"post_ids": [a["wechat"]], "targets": ["twitter"], "queue": False})
    check(status == 400 and res.get("error") == "Invalid platform: twitter",
          f"a Western target was not refused in the server's words: {status} {trim(res)}")
    check(len(rig.rows()) == before, "a refused Western target still wrote rows")

    # The same request again adds nothing and says so.
    before = len(rig.rows())
    status, res = rig.post({
        "post_ids": [a["wechat"], b["wechat"], c["wechat"]],
        "targets": ["douyin"], "queue": False})
    check(status == 200 and res["added"] == 0 and res["already"] == 3,
          f"a repeat request was not idempotent: {status} {res}")
    check(len(rig.rows()) == before, "a repeat request wrote rows")

    # Several targets at once. A named second account cannot be part of that:
    # the trio has no accounts to name, so a bare platform is the only shape.
    # A and B both took douyin further up, so wechat is already on both.
    status, res = rig.post({
        "post_ids": [a["wechat"], b["wechat"]],
        "targets": ["xiaohongshu", "wechat"], "queue": False})
    check(status == 200 and res["added"] == 1 and res["already"] == 3,
          f"multi-target add counted wrongly: {status} {res}")
    a_targets = rig.targets_of("Copy A")
    check(("douyin", None) in a_targets and ("xiaohongshu", None) in a_targets,
          f"card A did not gain both targets: {a_targets}")
    per = by_target(res)
    check(per[("xiaohongshu", None)]["added"] == 1
          and per[("xiaohongshu", None)]["already"] == 1
          and per[("wechat", None)]["already"] == 2,
          f"per-target breakdown is wrong: {res['by_target']}")
    check(len(rig.cards()) == 4, "multi-target add split a card")

    # (The old needs_image claims are gone with Instagram: the bulk route
    #  refuses a hidden platform, so it can no longer add an image-less
    #  Instagram row to flag. Instagram's media rules survive on legacy rows —
    #  the queue section pins them there — and its people fields are covered by
    #  check_ig_people.py.)

    # render=true hands back a fresh card per changed card and none for untouched ones.
    d2 = rig.card("Copy E", ["wechat"])
    e2 = rig.card("Copy F", ["wechat", "douyin"])
    status, res = rig.post({
        "post_ids": [d2["wechat"], e2["wechat"]], "targets": ["douyin"],
        "queue": False, "render": True})
    check(status == 200 and len(res["results"]) == 1,
          f"render should return only the changed card: {status} {len(res.get('results', []))}")
    only = res["results"][0]
    check(d2["wechat"] in only["anchor_ids"], "render result is not keyed to the selected row")
    check(sorted(only["post_ids"]) == sorted(r["id"] for r in rig.rows_for("Copy E")),
          "render result does not list the card's rows")
    check('data-platform="douyin"' in only["html"] and "Copy E" in only["html"],
          "render result is not the re-rendered card")
    status, res = rig.post({"post_ids": [d2["wechat"]], "targets": ["douyin"]})
    check(status == 200 and "results" not in res, "render leaked into a request that did not ask")

    print(f"bulk add gave {len(rig.rows())} rows over {len(rig.cards())} cards with copy, image and brief intact")
    print("BULK_ADD_OK")


# ===========================================================================
# queue: trio rows are added but never queued; legacy rows still queue honestly
# ===========================================================================
def section_queue():
    # Slots exist for the legacy Western platforms only. The offered trio can
    # never take one — that is what draft-only means.
    rig = Rig(slot_platforms=("linkedin", "threads", "twitter", "instagram"))
    db, P = rig.database, rig.P

    A = rig.card("Q-A plain", ["wechat"])
    B = rig.card("Q-B used", ["wechat"])
    C = rig.card("Q-C queued", ["wechat"])
    D = rig.card("Q-D posted", ["wechat"])
    E = rig.card("Q-E legacy", ["linkedin"])
    db.mark_standalone_post_used(B["wechat"], True, db_path=P)
    # A draft-only row cannot be queued through the app, so its queue and
    # posted states are seeded at the database level, the way a legacy row
    # carries them. What is pinned is the order the skips are decided in:
    # already queued first, then published, then used, then draft-only.
    db.add_scheduled_post(scheduled_for="2099-01-01T09:00:00", post_type="standalone",
                          standalone_post_id=C["wechat"], platform="wechat", db_path=P)
    db.add_scheduled_post(scheduled_for="2020-01-01T09:00:00", post_type="standalone",
                          standalone_post_id=D["wechat"], platform="wechat", status="posted",
                          db_path=P)
    anchors = [A["wechat"], B["wechat"], C["wechat"], D["wechat"], E["linkedin"]]

    # -- add a trio target with queue: every card gets the row, none of it queues
    status, res = rig.post({"post_ids": anchors, "targets": ["wechat"], "queue": True})
    check(status == 200 and res["success"], f"add+queue failed: {status} {res}")
    check(res["added"] == 1 and res["already"] == 4,
          f"expected the wechat row added only to the legacy card: {res}")
    check(res["queued"] == 0 and res["already_queued"] == 1 and res["skipped_count"] == 4,
          f"a draft-only target is added but never queued: {res}")
    reasons = [s["reason"] for s in res["skipped"]]
    check(reasons.count(DRAFT_ONLY_SKIP.format("wechat")) == 2,
          f"the draft-only skip does not say so in the user's words: {reasons}")
    check(any("used" in r for r in reasons) and any("published" in r for r in reasons),
          f"skip reasons do not say used / already published: {reasons}")
    check(all(s["platform"] == "wechat" for s in res["skipped"]),
          f"a skip was reported against another platform's row: {res['skipped']}")
    check(not [s for s in rig.pending() if s["platform"] in OFFERED and s["standalone_post_id"] != C["wechat"]],
          "a draft-only target entered the queue")
    check(len(rig.cards()) == 5, "queueing changed the number of cards")
    stats = by_target(res)[("wechat", None)]
    check(stats["label"] == "Wechat" and stats["skipped"] == 4 and stats["already_queued"] == 1,
          f"by_target misses the skips: {res['by_target']}")

    # A repeat is a no-op: nothing new added, nothing new queued.
    status, res = rig.post({"post_ids": anchors, "targets": ["wechat"], "queue": True})
    check(status == 200 and res["added"] == 0 and res["already"] == 5
          and res["queued"] == 0 and res["already_queued"] == 1 and res["skipped_count"] == 4,
          f"a repeat add+queue changed its mind: {res}")
    check(len([s for s in rig.pending() if s["platform"] == "wechat"]) == 1,
          "a repeat add+queue touched the queue")

    # -- a legacy Western row still queues through the single-card route: its
    #    own slot, carrying its account
    F = rig.card("Q-F legacy queue", ["twitter"])
    q = rig.queue_one(F["twitter"], "twitter").get_json()
    check(q and q["success"], f"queueing a legacy Western row failed: {q}")
    entries = rig.pending_for(F["twitter"])
    check(len(entries) == 1 and entries[0]["platform"] == "twitter"
          and entries[0]["account_id"] == rig.accounts["twitter"],
          f"the queue entry does not carry the legacy row's platform and account: {entries}")

    # -- a platform with no slots: the legacy row stays unqueued, the miss says why
    bare = Rig(slot_platforms=("linkedin",))
    G = bare.card("Q-G no slots", ["facebook"])
    response = bare.queue_one(G["facebook"], "facebook")
    body = response.get_json()
    check(response.status_code == 400 and "slot" in body.get("error", "").lower(),
          f"a platform with no slots was queued or unexplained: {response.status_code} {body}")
    check(not bare.pending_for(G["facebook"]), "a row with no slot to take was queued")

    # -- legacy Instagram rows still answer for their media through the
    #    whole-card queue, the one road left that reaches them (its own rig, so
    #    Instagram has slots to take). First with no image and no stock image:
    ig_rig = Rig(slot_platforms=("linkedin", "instagram"))
    H = ig_rig.card("Q-H stock", ["linkedin", "instagram"], image_url=None)
    ig_rig.stock_url = None
    all_h = ",".join(str(i) for i in H.values())
    response = ig_rig.client.post(f"/compose/post/{H['linkedin']}/queue",
                                  data={"all": "1", "post_ids": all_h})
    body = response.get_json()
    check(response.status_code == 200 and body["partial"] and len(body["queued"]) == 1
          and len(body["skipped"]) == 1 and "image" in body["skipped"][0]["error"].lower(),
          f"an image-less legacy Instagram row did not report why it stayed out: "
          f"{response.status_code} {body}")
    check(not ig_rig.pending_for(H["instagram"]), "an image-less Instagram row was queued")

    # Then with a stock image it can find: the image lands on EVERY row of the
    # card, or the card would silently split in two.
    I = ig_rig.card("Q-I stock", ["linkedin", "instagram"], image_url=None)
    ig_rig.stock_url = "https://stock.test/found.jpg"
    cards_before = len(ig_rig.cards())
    all_i = ",".join(str(i) for i in I.values())
    response = ig_rig.client.post(f"/compose/post/{I['linkedin']}/queue",
                                  data={"all": "1", "post_ids": all_i})
    body = response.get_json()
    check(response.status_code == 200 and len(body["queued"]) == 2,
          f"a card whose stock image was findable did not queue whole: {response.status_code} {body}")
    images = {r["image_url"] for r in ig_rig.rows_for("Q-I stock")}
    check(images == {"https://stock.test/found.jpg"},
          f"the stock image did not reach every row of the card: {images}")
    check(len(ig_rig.cards()) == cards_before, "attaching the Instagram image split the card")

    # (The old "unconnected platform" skip is gone with the West: a bulk target
    #  is refused before it can queue, and every offered target is draft-only,
    #  so the "Connect X before queueing" branch has nothing left to say.)

    # -- the reported skips are capped but counted in full
    many = Rig(slot_platforms=("linkedin",))
    seeded = [many.card(f"Q-many {i}", ["wechat"]) for i in range(70)]
    status, res = many.post({"post_ids": [s["wechat"] for s in seeded],
                             "targets": ["douyin"], "queue": True})
    check(status == 200 and res["added"] == 70 and res["skipped_count"] == 70
          and len(res["skipped"]) <= 50,
          f"skips were not capped while still counted: {status} added={res.get('added')} "
          f"count={res.get('skipped_count')} listed={len(res.get('skipped', []))}")

    print("trio rows are added but never queued; legacy rows queue on with their skips named")
    print("BULK_QUEUE_OK")


# ===========================================================================
# select: which cards, and what is refused
# ===========================================================================
def section_select():
    rig = Rig()
    X = rig.card("S-X", ["wechat", "threads"])
    Y = rig.card("S-Y", ["wechat"])
    Z = rig.card("S-Z", ["xiaohongshu"])

    # A platform-filtered selection sends one row id per card — not the card's
    # first row — and must still act on the whole card. The ids here are a
    # legacy Threads row and a trio row: either kind has to find its card.
    status, res = rig.post({"post_ids": [X["threads"], Y["wechat"]],
                            "targets": ["douyin"], "queue": False})
    check(status == 200 and res["cards"] == 2 and res["added"] == 2,
          f"a one-row-per-card selection did not resolve to 2 cards: {status} {res}")
    check(rig.targets_of("S-X") == sorted([("wechat", None), ("threads", rig.accounts["threads"]),
                                           ("douyin", None)]),
          f"card X did not gain exactly one douyin row: {rig.targets_of('S-X')}")
    check(len([r for r in rig.rows_for("S-Z") if r["platform"] == "douyin"]) == 0,
          "an unselected card was touched")

    # Two rows of one card, and one id repeated, are still one card processed once.
    status, res = rig.post({"post_ids": [X["wechat"], X["threads"], X["wechat"]],
                            "targets": ["xiaohongshu"], "queue": False})
    check(status == 200 and res["cards"] == 1 and res["added"] == 1,
          f"two rows of one card were processed twice: {status} {res}")
    check(len([r for r in rig.rows_for("S-X") if r["platform"] == "xiaohongshu"]) == 1,
          "card X got more than one xiaohongshu row")

    # Stale ids are ignored beside real ones; only-stale is a clear refusal.
    status, res = rig.post({"post_ids": [Z["xiaohongshu"], 987654], "targets": ["douyin"], "queue": False})
    check(status == 200 and res["cards"] == 1 and res["added"] == 1,
          f"a stale id beside a real one was not ignored: {status} {res}")
    before = len(rig.rows())
    status, res = rig.post({"post_ids": [987654, 987655], "targets": ["douyin"]})
    check(status == 400 and "error" in res, f"an all-stale selection was not refused: {status} {res}")
    check(len(rig.rows()) == before, "an all-stale selection wrote rows")

    # Refusals write nothing. A hidden platform is refused like an unknown one,
    # and a named account is refused too: the trio has none to name.
    other_account = rig.accounts["threads"]
    bad = [
        ("no selection", {"targets": ["douyin"]}),
        ("empty ids", {"post_ids": [], "targets": ["douyin"]}),
        ("no targets", {"post_ids": [Z["xiaohongshu"]]}),
        ("empty targets", {"post_ids": [Z["xiaohongshu"]], "targets": []}),
        ("unknown platform", {"post_ids": [Z["xiaohongshu"]], "targets": ["myspace"]}),
        ("hidden platform", {"post_ids": [Z["xiaohongshu"]], "targets": ["twitter"]}),
        ("account the platform does not own", {"post_ids": [Z["xiaohongshu"]],
                                               "targets": [f"wechat:{other_account}"]}),
        ("one bad target among good ones", {"post_ids": [Z["xiaohongshu"]],
                                            "targets": ["douyin", "myspace"]}),
        ("non-numeric ids", {"post_ids": ["abc"], "targets": ["douyin"]}),
        ("filters not an object", {"filters": "unused", "targets": ["douyin"]}),
    ]
    for name, payload in bad:
        status, res = rig.post(payload)
        check(status == 400 and res.get("error"), f"'{name}' was not refused with 400: {status} {res}")
    check(len(rig.rows()) == before, "a refused request still wrote rows")

    # A bare form post (no JSON) is not a supported shape and must not 500.
    response = rig.client.post(BULK, data={"targets": "douyin"})
    check(response.status_code == 400, f"a form-encoded request did not answer 400: {response.status_code}")

    # A string flag is read like the other bulk endpoints read theirs: the
    # draft-only skip firing is what proves queue was read as true.
    W = rig.card("S-W", ["wechat"])
    status, res = rig.post({"post_ids": [W["wechat"]], "targets": ["xiaohongshu"], "queue": "true"})
    check(status == 200 and res["added"] == 1 and res["skipped_count"] == 1,
          f"queue:'true' was not read as true: {status} {res}")

    # Every Rig repoints the app's default database at its own directory, so the
    # earlier rig is finished with before this one is built.
    # "Select all across every page": the server re-derives the cards from filters.
    rig2 = Rig()
    P2, db2 = rig2.P, rig2.database
    rig2.card("F-1 unused", ["wechat"])
    rig2.card("F-2 unused", ["wechat", "xiaohongshu"])
    used = rig2.card("F-3 used", ["wechat"])
    rig2.card("F-4 xiaohongshu", ["xiaohongshu"])
    db2.mark_standalone_post_used(used["wechat"], True, db_path=P2)

    expected = {g["head"]["content"] for g in rig2.web._filtered_post_groups({"used": "unused"})[0]}
    check(expected == {"F-1 unused", "F-2 unused", "F-4 xiaohongshu"},
          f"the filter bar's own answer is not what the seed implies: {expected}")
    status, res = rig2.post({"filters": {"used": "unused"}, "targets": ["douyin"], "queue": False})
    check(status == 200 and res["cards"] == 3 and res["added"] == 3,
          f"filters mode did not act on exactly the 3 unused cards: {status} {res}")
    got = {r["content"] for r in rig2.rows() if r["platform"] == "douyin"}
    check(got == expected, f"filters mode touched {got}, the filter bar shows {expected}")
    check("results" not in res, "filters mode rendered cards nobody asked for")

    # A platform filter chooses cards; it does not narrow what gets added to a
    # card. F-2 already has wechat, so only F-4 takes a new row.
    status, res = rig2.post({"filters": {"platform": "xiaohongshu"}, "targets": ["wechat"], "queue": False})
    check(status == 200 and res["cards"] == 2 and res["added"] == 1 and res["already"] == 1,
          f"platform-filtered scope did not resolve to the 2 cards with xiaohongshu: {status} {res}")
    check({r["content"] for r in rig2.rows() if r["platform"] == "wechat"}
          == {"F-1 unused", "F-2 unused", "F-3 used", "F-4 xiaohongshu"},
          "platform-filtered scope hit the wrong cards")

    print("bulk add resolves cards from ids and filters the way the page does, and refuses bad requests cleanly")
    print("BULK_SELECT_OK")


# ===========================================================================
# remove: take chosen targets off every selected card, safely
# ===========================================================================
def section_remove():
    rig = Rig()
    db, P = rig.database, rig.P

    def remove(anchors, targets, **extra):
        return rig.post({"post_ids": anchors, "targets": targets, **extra}, url=BULK_REMOVE)

    def gone(row_id):
        return db.get_standalone_post(row_id, db_path=P) is None

    def orphans():
        """Pending queue entries whose saved post no longer exists."""
        return [s_ for s_ in rig.pending()
                if s_["standalone_post_id"] and gone(s_["standalone_post_id"])]

    A = rig.card("R-A", ["wechat", "xiaohongshu", "threads"])
    B = rig.card("R-B", ["wechat", "xiaohongshu"], image_url=None)
    C = rig.card("R-C only", ["wechat"])
    cards_before = len(rig.cards())

    # -- remove xiaohongshu from three cards: exactly those rows go, cards stay whole
    status, res = remove([A["wechat"], B["wechat"], C["wechat"]], ["xiaohongshu"])
    check(status == 200 and res.get("success"), f"bulk remove failed: {status} {trim(res)}")
    check(res["cards"] == 3 and res["removed"] == 2 and res["absent"] == 1 and res["skipped_count"] == 0,
          f"expected 2 removed, 1 card without xiaohongshu, no skips: {trim(res)}")
    check(gone(A["xiaohongshu"]) and gone(B["xiaohongshu"]), "the xiaohongshu rows were not deleted")
    check(not gone(A["wechat"]) and not gone(A["threads"]) and not gone(B["wechat"]),
          "a row that was not asked for was deleted")
    check(rig.targets_of("R-A") == sorted([("wechat", None), ("threads", rig.accounts["threads"])]),
          f"card A does not have exactly wechat and Threads left: {rig.targets_of('R-A')}")
    check(len(rig.cards()) == cards_before, "removing a platform changed the number of cards")
    check(all(len({(r["content"], r["image_url"]) for r in rig.rows_for(c)}) == 1
              for c in ("R-A", "R-B", "R-C only")), "removing a platform split a card")

    # -- the same request again removes nothing and says so
    before = len(rig.rows())
    status, res = remove([A["wechat"], B["wechat"], C["wechat"]], ["xiaohongshu"])
    check(status == 200 and res["removed"] == 0 and res["absent"] == 3 and res["skipped_count"] == 0,
          f"a repeat request was not a no-op: {trim(res)}")
    check(len(rig.rows()) == before, "a repeat request deleted rows")

    # -- a post's only platform is never removed: that would delete the post
    status, res = remove([C["wechat"]], ["wechat"])
    check(status == 200 and res["removed"] == 0 and res["skipped_count"] == 1
          and res["skipped"][0]["code"] == "last", f"an only platform was not protected: {trim(res)}")
    check(not gone(C["wechat"]), "the post's only row was deleted")
    check(rig.rows_for("R-C only")[0]["content"] == "R-C only", "the protected post lost its copy")

    # -- asking for every platform of a card keeps the card whole, all reported
    G = rig.card("R-G both", ["wechat", "xiaohongshu"])
    status, res = remove([G["wechat"]], ["wechat", "xiaohongshu"])
    check(status == 200 and res["removed"] == 0 and res["skipped_count"] == 2
          and {s_["code"] for s_ in res["skipped"]} == {"last"},
          f"removing every platform of a card was not refused as a whole: {trim(res)}")
    check(not gone(G["wechat"]) and not gone(G["xiaohongshu"]), "a card lost rows it should have kept")

    # -- but two of three is fine
    H = rig.card("R-H three", ["wechat", "xiaohongshu", "threads"])
    status, res = remove([H["wechat"]], ["wechat", "xiaohongshu"])
    check(status == 200 and res["removed"] == 2 and res["skipped_count"] == 0
          and rig.targets_of("R-H three") == [("threads", rig.accounts["threads"])],
          f"removing two of three platforms failed: {trim(res)}")

    # -- a used row is just a row: used is not a queue state
    F = rig.card("R-F used", ["wechat", "douyin"])
    db.mark_standalone_post_used(F["douyin"], True, db_path=P)
    status, res = remove([F["wechat"]], ["douyin"])
    check(status == 200 and res["removed"] == 1 and gone(F["douyin"]), f"a used row was not removable: {trim(res)}")

    # -- a queued row is kept unless the caller says to take it out of the queue.
    #    No route can put a draft-only row in the queue any more, so the state
    #    is seeded at the database level; the guard runs on whatever the
    #    database holds, and it must keep holding it.
    D = rig.card("R-D queued", ["wechat", "douyin"])
    db.add_scheduled_post(scheduled_for="2099-01-01T09:00:00", post_type="standalone",
                          standalone_post_id=D["douyin"], platform="douyin", db_path=P)
    status, res = remove([D["wechat"]], ["douyin"])
    check(status == 200 and res["removed"] == 0 and res["skipped_count"] == 1
          and res["skipped"][0]["code"] == "queued", f"a queued row was not kept: {trim(res)}")
    check(not gone(D["douyin"]) and len(rig.pending_for(D["douyin"])) == 1,
          "an unforced remove touched a queued row or its schedule")
    status, res = remove([D["wechat"]], ["douyin"], force=True)
    check(status == 200 and res["removed"] == 1 and res["unqueued"] == 1 and gone(D["douyin"]),
          f"a forced remove did not remove the queued row: {trim(res)}")
    check(not orphans(), "a forced remove left a queue entry pointing at a deleted post")

    # -- a published row is kept unless forced, and its history survives a forced remove
    E = rig.card("R-E posted", ["wechat", "douyin"])
    db.add_scheduled_post(scheduled_for="2020-01-01T09:00:00", post_type="standalone",
                          standalone_post_id=E["douyin"], platform="douyin", status="posted",
                          db_path=P)
    status, res = remove([E["wechat"]], ["douyin"])
    check(status == 200 and res["removed"] == 0 and res["skipped"][0]["code"] == "published",
          f"a published row was not kept: {trim(res)}")
    check(not gone(E["douyin"]), "an unforced remove deleted a published row")
    status, res = remove([E["wechat"]], ["douyin"], force="true")
    check(status == 200 and res["removed"] == 1 and gone(E["douyin"]),
          f"a forced remove (string flag) did not remove the published row: {trim(res)}")
    history = [h for h in db.list_scheduled_posts(status="posted", db_path=P)
               if h["standalone_post_id"] == E["douyin"]]
    check(len(history) == 1, "removing a published row erased its posting history")

    # -- force never overrides the only-platform rule, and touches no queue entry
    K = rig.card("R-K both queued", ["wechat", "douyin"])
    for platform in ("wechat", "douyin"):
        db.add_scheduled_post(scheduled_for="2099-02-01T09:00:00", post_type="standalone",
                              standalone_post_id=K[platform], platform=platform, db_path=P)
    status, res = remove([K["wechat"]], ["wechat", "douyin"], force=True)
    check(status == 200 and res["removed"] == 0 and res["unqueued"] == 0
          and {s_["code"] for s_ in res["skipped"]} == {"last"},
          f"force overrode the only-platform rule: {trim(res)}")
    check(len(rig.pending_for(K["wechat"])) == 1 and len(rig.pending_for(K["douyin"])) == 1,
          "a refused forced remove still took entries out of the queue")

    # -- a kept (queued) row means the card is not emptied, so the rest can go
    M = rig.card("R-M partly queued", ["wechat", "douyin"])
    db.add_scheduled_post(scheduled_for="2099-03-01T09:00:00", post_type="standalone",
                          standalone_post_id=M["wechat"], platform="wechat", db_path=P)
    status, res = remove([M["wechat"]], ["wechat", "douyin"])
    check(status == 200 and res["removed"] == 1 and res["skipped_count"] == 1
          and res["skipped"][0]["code"] == "queued" and gone(M["douyin"]) and not gone(M["wechat"]),
          f"a card with one queued row could not shed its other platform: {trim(res)}")

    # -- a named account is not a target any more: the trio has no logins to
    #    name (the old second-LinkedIn-account claims went with the West), so
    #    the request is refused before it deletes anything
    L = rig.card("R-L accounts", ["wechat", "xiaohongshu"])
    before = len(rig.rows())
    status, res = remove([L["wechat"]], [f"wechat:{rig.accounts['threads']}"])
    check(status == 400 and "not connected" in res.get("error", ""),
          f"an account target on the trio was not refused: {status} {trim(res)}")
    check(len(rig.rows()) == before, "a refused account target still deleted rows")

    # -- render hands back the redrawn cards for the changed ones only
    N = rig.card("R-N render", ["wechat", "xiaohongshu"])
    O = rig.card("R-O render", ["wechat"])
    status, res = remove([N["wechat"], O["wechat"]], ["xiaohongshu"], render=True)
    check(status == 200 and len(res["results"]) == 1, f"render should redraw only the changed card: {trim(res)}")
    only = res["results"][0]
    check(N["wechat"] in only["anchor_ids"] and only["post_ids"] == [N["wechat"]],
          f"render result does not list the surviving rows: {only['post_ids']}")
    check(f'data-post-ids="{N["wechat"]}"' in only["html"] and "R-N render" in only["html"],
          "render result is not the redrawn card")
    status, res = remove([O["wechat"]], ["xiaohongshu"])
    check("results" not in res, "render leaked into a request that did not ask")

    check(not orphans(), "the run left queue entries pointing at deleted posts")
    print("bulk remove: rows deleted per target, queued/published kept unless forced, an only platform never removed")
    print("BULK_REMOVE_OK")


# ===========================================================================
# removeselect: which cards, and what is refused
# ===========================================================================
def section_removeselect():
    rig = Rig()
    X = rig.card("RS-X", ["wechat", "douyin", "threads"])
    Y = rig.card("RS-Y", ["wechat", "douyin"])
    Z = rig.card("RS-Z", ["wechat", "douyin"])

    def remove(payload):
        return rig.post(payload, url=BULK_REMOVE)

    # A platform-filtered selection sends one row id per card, and it need not
    # be the row being removed: X is named by its legacy Threads row.
    status, res = remove({"post_ids": [X["threads"], Y["wechat"]], "targets": ["wechat"]})
    check(status == 200 and res["cards"] == 2 and res["removed"] == 2,
          f"a one-row-per-card selection did not resolve to 2 cards: {status} {trim(res)}")
    check(sorted(r["platform"] for r in rig.rows_for("RS-X")) == ["douyin", "threads"],
          "the wrong rows were removed from card X")
    check(sorted(r["platform"] for r in rig.rows_for("RS-Y")) == ["douyin"],
          "the wrong rows were removed from card Y")
    check(sorted(r["platform"] for r in rig.rows_for("RS-Z")) == ["douyin", "wechat"],
          "an unselected card was touched")

    # Two rows of one card and a repeated id are one card, processed once.
    status, res = remove({"post_ids": [Z["wechat"], Z["douyin"], Z["wechat"]],
                          "targets": ["xiaohongshu"]})
    check(status == 200 and res["cards"] == 1 and res["absent"] == 1 and res["removed"] == 0,
          f"one card selected by two rows was counted twice: {status} {trim(res)}")

    # Stale ids beside real ones are ignored; only-stale is refused.
    status, res = remove({"post_ids": [Z["wechat"], 987654], "targets": ["wechat"]})
    check(status == 200 and res["cards"] == 1 and res["removed"] == 1,
          f"a stale id beside a real one was not ignored: {status} {trim(res)}")
    before = len(rig.rows())
    status, res = remove({"post_ids": [987654, 987655], "targets": ["wechat"]})
    check(status == 400 and res.get("error"), f"an all-stale selection was not refused: {status} {res}")
    check(len(rig.rows()) == before, "an all-stale selection deleted rows")

    # Refusals delete nothing.
    W = rig.card("RS-W", ["wechat", "douyin"])
    before = len(rig.rows())
    other_account = rig.accounts["threads"]
    bad = [
        ("no selection", {"targets": ["wechat"]}),
        ("empty ids", {"post_ids": [], "targets": ["wechat"]}),
        ("no targets", {"post_ids": [W["wechat"]]}),
        ("empty targets", {"post_ids": [W["wechat"]], "targets": []}),
        ("unknown platform", {"post_ids": [W["wechat"]], "targets": ["myspace"]}),
        ("hidden platform", {"post_ids": [W["wechat"]], "targets": ["linkedin"]}),
        ("account the platform does not own", {"post_ids": [W["wechat"]],
                                               "targets": [f"douyin:{other_account}"]}),
        ("one bad target among good ones", {"post_ids": [W["wechat"]],
                                            "targets": ["wechat", "myspace"]}),
        ("non-numeric ids", {"post_ids": ["abc"], "targets": ["wechat"]}),
        ("ids not a list", {"post_ids": "12", "targets": ["wechat"]}),
        ("filters not an object", {"filters": "unused", "targets": ["wechat"]}),
    ]
    for name, payload in bad:
        status, res = remove(payload)
        check(status == 400 and res.get("error"), f"'{name}' was not refused with 400: {status} {res}")
    check(len(rig.rows()) == before, "a refused request still deleted rows")
    response = rig.client.post(BULK_REMOVE, data={"targets": "wechat"})
    check(response.status_code == 400, f"a form-encoded request did not answer 400: {response.status_code}")

    # Every Rig repoints the app's default database at its own directory, so the
    # earlier rig is finished with before this one is built.
    rig2 = Rig()
    db2, P2 = rig2.database, rig2.P
    rig2.card("RF-1 unused", ["wechat", "douyin"])
    rig2.card("RF-2 unused", ["wechat", "douyin"])
    used = rig2.card("RF-3 used", ["wechat", "douyin"])
    rig2.card("RF-4 wechat only", ["wechat"])
    db2.mark_standalone_post_used(used["wechat"], True, db_path=P2)
    db2.mark_standalone_post_used(used["douyin"], True, db_path=P2)

    expected = {g["head"]["content"] for g in rig2.web._filtered_post_groups({"used": "unused"})[0]}
    check(expected == {"RF-1 unused", "RF-2 unused", "RF-4 wechat only"},
          f"the filter bar's own answer is not what the seed implies: {expected}")
    status, res = rig2.post({"filters": {"used": "unused"}, "targets": ["douyin"]}, url=BULK_REMOVE)
    check(status == 200 and res["cards"] == 3 and res["removed"] == 2 and res["absent"] == 1,
          f"filters mode did not act on exactly the 3 unused cards: {status} {trim(res)}")
    douyin_left = {r["content"] for r in rig2.rows() if r["platform"] == "douyin"}
    check(douyin_left == {"RF-3 used"}, f"filters mode removed douyin from the wrong cards: {douyin_left}")
    check("results" not in res, "filters mode rendered cards nobody asked for")

    print("bulk remove resolves cards from ids and filters the way the page does, and refuses bad requests cleanly")
    print("BULK_REMOVE_SELECT_OK")


SECTIONS = {
    "guard": section_guard,
    "add": section_add,
    "queue": section_queue,
    "select": section_select,
    "remove": section_remove,
    "removeselect": section_removeselect,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_bulk_platform.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
