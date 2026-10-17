"""G5: the screens really expose accounts and cross-posting, and are wired up.

A backend that supports several accounts is worth nothing if no page lets you
add a second one or aim a post at it. This gate renders the real pages and
checks that the controls are present AND bound to the endpoints that do the
work, rather than being inert markup that looks right in a screenshot.
"""

import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import (  # noqa: E402
    isolated_app, install_fake_clients, connect, check,
)

directory, database, web, publisher, client = isolated_app()
uid = database.get_user_by_username('gate')['id']
P = database.DB_PATH
install_fake_clients(publisher, web)

# --- the accounts page exists and is reachable from every page ------------
work = connect(database, "linkedin", "li-work", "Work")
studio = connect(database, "linkedin", "li-studio", "Studio")
threads = connect(database, "threads", "th-1", "brandco")
database.set_social_account_label(studio, "Studio", db_path=P)

response = client.get("/accounts")
check(response.status_code == 200,
      f"the accounts page did not render ({response.status_code})")
accounts_html = response.get_data(as_text=True)

for name, token in (
    ("page heading", "Accounts"),
    ("connect another control", "Connect another"),
    ("both LinkedIn accounts", "Studio"),
    ("default marker", "Default"),
    ("rename handler", "async function renameAccount"),
    ("default handler", "async function makeAccountDefault"),
    ("disconnect handler", "async function disconnectAccount"),
    ("rename endpoint", "/label"),
    ("default endpoint", "/default"),
    ("disconnect endpoint", "/disconnect"),
):
    check(token in accounts_html, f"the accounts page is missing its {name}")

check(re.search(r'href="/linkedin/auth\?return=accounts"', accounts_html),
      "the connect control does not start an OAuth run that returns here")
check(re.search(r'onclick="disconnectAccount\(\d+', accounts_html),
      "the disconnect button is not wired to a specific account")
check(re.search(r'onclick="makeAccountDefault\(\d+', accounts_html),
      "the make-default button is not wired to a specific account")

nav = client.get("/compose").get_data(as_text=True)
# The Western platforms are hidden from the UI, so the accounts page is off the
# navigation — it stays served for managing the logins that already exist.
check('href="/accounts"' not in nav,
      "the accounts page is still offered in the navigation")
check(client.get("/accounts").status_code == 200,
      "the accounts page is not served at its own URL any more")

# --- the composer offers per-account targets ------------------------------
# Seeded like legacy rows: the create API now refuses the hidden platforms.
ids = [
    database.add_standalone_post(
        source_type="manual", source_content="gate", platform="linkedin",
        content="Ready to go out everywhere", account_id=work, user_id=uid, db_path=P),
    database.add_standalone_post(
        source_type="manual", source_content="gate", platform="linkedin",
        content="Ready to go out everywhere", account_id=studio, user_id=uid, db_path=P),
    database.add_standalone_post(
        source_type="manual", source_content="gate", platform="threads",
        content="Ready to go out everywhere", account_id=threads, user_id=uid, db_path=P),
]

compose = client.get("/compose").get_data(as_text=True)
for name, token in (
    ("per-target chip data", "data-account-id="),
    ("card target list", "data-targets="),
    ("account picker for generation", "account-target-checkbox"),
    ("target collector", "function selectedAccountTargets"),
    ("card target reader", "function cardTargets"),
    ("cross-target publish handler", "async function postNowAllFromCard"),
    ("cross-target queue handler", "async function queueAllFromCard"),
):
    check(token in compose, f"the composer is missing its {name}")

# The legacy rows are not offered targets any more, but they are still
# publishable rows: the card's menus keep reaching them.
check("Post to all 3 accounts" in compose,
      "the card does not offer publishing to every account at once")
check("Queue all 3 accounts" in compose,
      "the card does not offer queueing every account at once")
check(re.search(r"/compose/post/\$\{postItem\.dataset\.postId\}/publish", compose),
      "the publish-to-all button is not wired to the fan-out endpoint")
check("body.append('all', '1')" in compose,
      "the queue-all button does not ask the server for a card-wide queue")
check("body.append('account_id', accountId)" in compose,
      "publishing a single chip does not name its account")

# The chips a card draws are the targets on offer (the domestic platforms).
# Legacy rows ride along in the card's ids and menus, not as chips of their own.
card_start = compose.index('<div class="post-item"')
card_html = compose[card_start:compose.index('class="post-content-wrapper"', card_start)]
chips = re.findall(r'<span class="platform-chip[^"]*"\s*data-platform="(\w+)"\s*'
                   r'data-account-id="(\d*)"', card_html)
ticked = re.findall(r'<span class="platform-chip is-on"\s*data-platform="(\w+)"\s*'
                    r'data-account-id="(\d*)"', card_html)
offered = {p for p, _ in chips}
check(offered == {"xiaohongshu", "douyin", "wechat", "zhihu", "toutiao"},
      f"the card offers chips beyond the domestic set: {sorted(offered)}")
check(not ticked,
      f"a legacy card ticked chips for targets it was never aimed at: {ticked}")

# The "write a new post" composer offers the same targets.
new_post = re.search(r'id="new-post-platforms"(.*?)</div>', compose, re.S)
check(new_post is not None, "the manual composer lost its target chips")
manual_chips = re.findall(r'data-platform="(\w+)" data-account-id="(\d*)"',
                          new_post.group(1))
check({p for p, _ in manual_chips} == {"xiaohongshu", "douyin", "wechat", "zhihu", "toutiao"},
      f"the manual composer offers chips beyond the domestic set: {manual_chips}")
check("function newPostTargets" in compose,
      "the manual composer does not collect per-account targets")

# --- the target chips actually move rows, not just styling ----------------
before = len(database.list_standalone_posts(user_id=uid, db_path=P))
added = client.post(f"/compose/post/{ids[0]}/platform", data={
    "platform": "wechat", "action": "add",
    "post_ids": ",".join(str(i) for i in ids),
}).get_json()
check(added and added.get("success"), f"ticking a target failed: {added}")
after = len(database.list_standalone_posts(user_id=uid, db_path=P))
check(after == before + 1, "ticking a target did not create its row")

# --- the accounts JSON the composer reads is real -------------------------
payload = client.get("/accounts/list").get_json()
check(payload and "platforms" in payload, "the accounts list endpoint returned nothing")
linkedin = next(p for p in payload["platforms"] if p["platform"] == "linkedin")
check(len(linkedin["accounts"]) == 2,
      f"the accounts list reports {len(linkedin['accounts'])} LinkedIn accounts")
check(any(a["is_default"] for a in linkedin["accounts"]),
      "the accounts list names no default account")

print("UI: the accounts page manages logins, and the composer draws a chip per "
      "target with working publish-all and queue-all controls")
print("ACCOUNTS_UI_OK")
