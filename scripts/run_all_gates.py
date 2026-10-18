"""Run every completion gate in the repository, and fail if one is unaccounted for.

This is the single entry point for both a local run and CI, so the two cannot
drift apart:

    python scripts/run_all_gates.py

It runs the multi-account gates (defined once, in run_account_gates.py),
each in its own process. A gate passes only if it exits 0 AND prints its own
OK token, which is what stops a gate that quietly did nothing from reading as
a pass.

It also checks the gates themselves. Every scripts/check_*.py must be registered
here or listed in NOT_GATES with a reason. A check that exists but is not
registered is worse than no check, because it looks like coverage, so adding one
without wiring it in fails the run instead of being silently skipped.
"""

import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from run_account_gates import GATES as ACCOUNT_GATES, run_gate  # noqa: E402

# Each gate: the file, the token it must print, the claim it makes, and
# optionally the arguments that select one section of a script holding several.
# The multi-account gates in run_account_gates.py are plain three-item entries.

# The instance settings page and the appconfig layer behind it. One script, one
# gate: it drives the real /settings page on a throwaway database.
SETTINGS_GATES = [
    ("check_settings_ui.py", "SETTINGS_UI_OK",
     "settings saved on /settings beat .env, secrets never echo back, and the retired platforms group stays hidden while its keys still save"),
]

# The domestic-article agent: queue semantics on the server side.
X_FETCH_GATES = [
    ("check_x_fetch.py", "X_FETCH_OK",
     "X/Twitter URLs extract via fxtwitter and errors read with one prefix"),
]

# The AI usage / cost meters. One script, one gate per claim, picked by
# argument: per-user scoping of every read, environment-variable model pricing
# and background attribution, then the real /usage page and CSV isolation.
USAGE_GATES = [
    ("check_usage.py", "USAGE_SCOPING_OK",
     "usage reads scope by user, legacy rows stay admin-only, and user_context attributes background work",
     ("scoping",)),
    ("check_usage.py", "USAGE_PRICING_OK",
     "env-priced models bill at their env rates and outrank the built-in tables",
     ("pricing",)),
    ("check_usage.py", "USAGE_PAGE_OK",
     "the page defaults to the viewer's own data, scope=all is admin-only, and CSV follows the scope",
     ("page",)),
]

# The content brief's no-source fallback: draft from model knowledge when
# research finds nothing, skip when sources exist but are all stale, and never
# report a failed run as a successful zero.
BRIEF_GATES = [
    ("check_brief_fallback.py", "BRIEF_FALLBACK_OK",
     "briefs with no sources draft from model knowledge; all-duplicates still skips; failures surface as errors"),
]

AGENT_GATES = [
    ("check_agent_jobs.py", "AGENT_JOBS_OK",
     "the agent job queue claims once, retries with backoff and reclaims stale claims"),
    ("check_agent_hub.py", "AGENT_HUB_OK",
     "the /agent/* door authenticates by token and moves jobs through events"),
    ("check_domestic_publisher.py", "DOMESTIC_PUBLISHER_OK",
     "card pushes enqueue verbatim copy and dedupe while a job is in flight"),
    ("check_agent_agent.py", "AGENT_AGENT_OK",
     "the local agent claims over real HTTP, reports honestly and survives errors"),
    ("check_agent_drivers.py", "AGENT_DRIVERS_OK",
     "the wechatsync driver preflights login, classifies failures and cleans temp files"),
    ("check_agent_push.py", "AGENT_PUSH_OK",
     "the compose push route queues through the hub and the new platforms render"),
    ("check_agent_ui.py", "AGENT_UI_OK",
     "the push button renders with its disabled reasons and results show per platform"),
    ("check_login_states.py", "LOGIN_STATES_OK",
     "the login-states view merges probed beats unprobed and flips all-offline past the window"),
    ("check_extension_tests.py", "EXTENSION_TESTS_OK",
     "the publisher extension's vitest suite passes and esbuild emits dist/"),
    ("check_extension_protocol.py", "EXTENSION_PROTOCOL_OK",
     "the compiled extension client heartbeats, claims only its lane and reports events over real HTTP"),
]

# The Compose page's gates. Several scripts hold more than one gate and pick one
# by argument, each printing its own token, so one file appears once per section.
# Two of them run the page's JavaScript through node, so CI installs it.
COMPOSE_GATES = [
    ("check_bulk_platform.py", "SINGLE_CARD_GUARD_OK",
     "ticking one platform and queueing a whole card behave as pinned",
     ("guard",)),
    ("check_bulk_platform.py", "BULK_ADD_OK",
     "bulk Add Platform keeps each card's copy, image and brief intact",
     ("add",)),
    ("check_bulk_platform.py", "BULK_QUEUE_OK",
     "bulk add and queue give each row its own slot and leave the rest alone",
     ("queue",)),
    ("check_bulk_platform.py", "BULK_SELECT_OK",
     "bulk add resolves cards the way the page does and refuses bad requests",
     ("select",)),
    ("check_bulk_platform.py", "BULK_REMOVE_OK",
     "bulk Remove Platform keeps queued, published and only-platform rows unless told otherwise",
     ("remove",)),
    ("check_bulk_platform.py", "BULK_REMOVE_SELECT_OK",
     "bulk remove resolves cards the way the page does and refuses bad requests",
     ("removeselect",)),
    ("check_bulk_platform_ui.py", "BULK_PLATFORM_UI_OK",
     "the Compose page exposes Add and Remove Platform and binds them to their endpoints",
     ("wiring",)),
    ("check_bulk_platform_ui.py", "COMPOSE_JS_SYNTAX_OK",
     "every inline script on the Compose page parses",
     ("syntax",)),
    ("check_compose_js.py", "COMPOSE_JS_NO_DUPES_OK",
     "no function on the Compose page is declared twice",
     ("duplicates",)),
    ("check_compose_js.py", "REFRESH_CARD_OK",
     "refreshCard redraws, drops an emptied card, and runs under node",
     ("refreshcard",)),
    ("check_compose_js.py", "BULK_TOTALS_OK",
     "the bulk result's batch totals sum correctly in both directions, under node",
     ("totals",)),
    ("check_channel_defaults.py", "CHANNEL_DEFAULTS_OK",
     "the compose and article channel pickers ship with nothing pre-ticked"),
]

# Video posting. One script holds a gate per claim and picks one by argument,
# each printing its own token. They use fake platform clients and a real local
# HTTP server, so nothing posts anywhere. Two of them need ffmpeg/ffprobe to
# make and read a real MP4, so CI installs it.
VIDEO_GATES = [
    ("check_video.py", "VIDEO_SCHEMA_OK",
     "a video persists on a saved post, upgrades an old database, and groups cards",
     ("schema",)),
    ("check_video.py", "VIDEO_ROUTER_OK",
     "the shared publisher hands a video to each platform's video method",
     ("router",)),
    ("check_video.py", "VIDEO_LINKEDIN_OK",
     "LinkedIn gets the exact bytes in ordered parts and never degrades to a text post",
     ("linkedin",)),
    ("check_video.py", "VIDEO_TWITTER_OK",
     "X gets the exact bytes in segments and asks for a reconnect without media.write",
     ("twitter",)),
    ("check_video.py", "VIDEO_THREADS_OK",
     "Threads waits for FINISHED before publishing a video; the image path is unchanged",
     ("threads",)),
    ("check_video.py", "VIDEO_FACEBOOK_OK",
     "Facebook posts a video by file_url and a video beats an image",
     ("facebook",)),
    ("check_video.py", "VIDEO_INSTAGRAM_OK",
     "an Instagram card with a video publishes as a Reel and needs no image",
     ("instagram",)),
    ("check_video.py", "VIDEO_ROUTES_OK",
     "the video route attaches, replaces and clears across a whole card and refuses unsafe URLs",
     ("routes",)),
    ("check_video.py", "VIDEO_FANOUT_OK",
     "post-now, post-to-all and the schedule queue each deliver the video",
     ("fanout",)),
    ("check_video.py", "VIDEO_COPY_OK",
     "ticking a platform and bulk Add Platform copy the video and keep one card",
     ("copy",)),
    ("check_video.py", "VIDEO_COMPAT_OK",
     "a real MP4 is probed and each platform's limits hold at the boundary",
     ("compat",)),
    ("check_video.py", "VIDEO_DOWNLOAD_OK",
     "the downloader streams within its cap and refuses unsafe URLs at every hop",
     ("download",)),
    ("check_video.py", "VIDEO_UI_OK",
     "the Compose card and schedule page show video, and the page functions run",
     ("ui",)),
    ("check_video.py", "VIDEO_NEWPOST_OK",
     "a new post takes a video, refuses an unusable one before creating anything",
     ("newpost",)),
    ("check_video.py", "VIDEO_REELOPTS_OK",
     "a Reel's cover frame and collaborators are stored, validated and sent when it publishes",
     ("reelopts",)),
]

# Writing a post by hand, and the model adapters behind generation. One script,
# one gate per claim, picked by argument. They use fake clients and no keys, and
# the wiring gates read the rendered Compose page, one under node.
MANUAL_POST_GATES = [
    ("check_manual_posts.py", "MANUAL_POSTS_ROUTE_OK",
     "a hand-written post writes one row per ticked account, shows as one card, and refusals write nothing",
     ("route",)),
    ("check_manual_posts.py", "MANUAL_POSTS_WIRING_OK",
     "the Write Manually tab is first and default, hides the model controls, and is bound to its endpoint",
     ("wiring",)),
    ("check_manual_posts.py", "MANUAL_POSTS_TARGETS_OK",
     "the composer builds the accounts and platforms the server expects, under node",
     ("targets",)),
    ("check_manual_posts.py", "MANUAL_POSTS_VIDEO_OK",
     "the Write Manually tab attaches a video (file or URL, never both), sends it instead of the image, and the route takes it",
     ("video",)),
    ("check_manual_posts.py", "LLM_ADAPTERS_OK",
     "GPT-6 and Claude 5 requests are translated, older models pass through, and prices resolve",
     ("llm",)),
]

# The accounts and cross-posting JavaScript, run under node rather than read. The
# markup gate in ACCOUNT_GATES only proves the controls exist; these prove what
# they send and what they tell the user, including on every failure path.
ACCOUNT_JS_GATES = [
    ("check_accounts_js.py", "COMPOSE_ACCOUNTS_JS_OK",
     "the Compose cross-posting controls send the right account and report each target honestly",
     ("compose",)),
    ("check_accounts_js.py", "ACCOUNTS_PAGE_JS_OK",
     "the accounts page's default, rename and disconnect controls behave, including on failure",
     ("accounts",)),
    ("check_accounts_js.py", "ACCOUNTS_JS_SYNTAX_OK",
     "every inline script on the accounts page parses",
     ("syntax",)),
]

# The schedule queue shows one row for a post that goes to several platforms.
# One script, one gate per claim, picked by argument. Fake clients, no keys; the
# JavaScript ones lift the page's own functions and run them under node.
QUEUE_GATES = [
    ("check_queue_groups.py", "QUEUE_GROUP_OK",
     "queue entries that are one post collapse; a different moment, copy, image, state or repost does not",
     ("group",)),
    ("check_queue_groups.py", "QUEUE_PAGE_OK",
     "the schedule page and its JSON draw one row per post, carry every member id, and count honestly",
     ("page",)),
    ("check_queue_groups.py", "QUEUE_ORDER_OK",
     "drag-reorder and move to top or bottom never split a post that goes to several platforms",
     ("order",)),
    ("check_queue_groups.py", "QUEUE_EDIT_OK",
     "editing a collapsed row's time moves every platform and nothing outside it",
     ("edit",)),
    ("check_queue_groups.py", "QUEUE_PARITY_OK",
     "the server-rendered queue rows and the script-redrawn rows are identical, under node",
     ("parity",)),
    ("check_queue_groups.py", "QUEUE_JS_OK",
     "a collapsed row's Post Now, Cancel, Retry, Delete, Edit and bulk actions reach every platform, under node",
     ("js",)),
    ("check_queue_groups.py", "QUEUE_SYNTAX_OK",
     "every inline script on the schedule page parses and none is declared twice",
     ("syntax",)),
]

# Loading the rest of the Compose list: the route's batch size, and the Load more,
# Load all and scroll-loading controls, the latter run under node against a fake
# DOM, a fake paging server and a fake IntersectionObserver.
PAGING_GATES = [
    ("check_compose_paging.py", "PAGING_ROUTE_OK",
     "the list route takes a batch size and paging covers each matching card exactly once",
     ("route",)),
    ("check_compose_paging.py", "PAGING_WIRING_OK",
     "the Compose list has Load more, Load all and the scroll switch, and the script redraws them identically",
     ("wiring",)),
    ("check_compose_paging.py", "PAGING_LOADALL_OK",
     "Load all pages through the filtered set, can be stopped, survives failure and stale filters, under node",
     ("loadall",)),
    ("check_compose_paging.py", "PAGING_SCROLL_OK",
     "scrolling loads the next page once at a time, is switchable, and pauses after a failure, under node",
     ("scroll",)),
]

# Instagram collaborators and Reel people-tags. One script, one gate per claim,
# picked by argument. The client ones replace `requests` with a recorder and read
# the HTTP the client would send; the rest drive the real routes, database and
# publisher with a recording client. Nothing posts anywhere. Two run the pages'
# JavaScript under node.
IG_PEOPLE_GATES = [
    ("check_ig_people.py", "IG_PEOPLE_CLIENT_OK",
     "collaborators and Reel tags reach Instagram's request exactly as entered, on a reel, an image and a carousel",
     ("client",)),
    ("check_ig_people.py", "IG_PEOPLE_NONE_OK",
     "a post with no collaborators or tags sends the same requests as before they existed",
     ("none",)),
    ("check_ig_people.py", "IG_PEOPLE_REFUSE_OK",
     "a bad handle or too many collaborators is refused before any request or row is made",
     ("refuse",)),
    ("check_ig_people.py", "IG_PEOPLE_REJECT_OK",
     "Instagram rejecting a collaborator fails the post naming them, publishes nothing, and is not retried without them",
     ("reject",)),
    ("check_ig_people.py", "IG_PEOPLE_PERSIST_OK",
     "the new columns migrate an old database, the routes round-trip, and a card's people stay together",
     ("persist",)),
    ("check_ig_people.py", "IG_PEOPLE_PUBLISH_OK",
     "Post now, the whole-card publish and the scheduler each deliver a post's collaborators and tags",
     ("publish",)),
    ("check_ig_people.py", "IG_PEOPLE_UI_OK",
     "Write Manually, the card and the schedule page show the people controls, the invite copy and a queued post's people",
     ("ui",)),
    ("check_ig_people.py", "IG_PEOPLE_JS_OK",
     "the Compose page's people functions send what the server expects, show a refusal and follow the format, under node",
     ("js",)),
    ("check_ig_people.py", "IG_PEOPLE_QUEUE_OK",
     "the server-rendered queue row and the script redraw show the same collaborators and tags, under node",
     ("queue",)),
]

# Multi-user content isolation. One script, one gate per section, picked by
# argument: the migration's schema guarantees, the data layer's per-user
# scoping, a static scan of database.py's SQL against the twelve content
# tables, and the two-account end-to-end pass over the real routes.
ISOLATION_GATES = [
    ("check_content_isolation.py", "CONTENT_ISOLATION_SCHEMA_OK",
     "迁移幂等：12 表带 user_id，feeds/episodes/url_sources 复合唯一，同 URL 双用户各一行",
     ("schema",)),
    ("check_content_isolation.py", "CONTENT_ISOLATION_DATA_OK",
     "双账号互不可见，NULL 存量对含 admin 的所有人不可见，bulk 与写入口均不越权",
     ("data",)),
    ("check_content_isolation.py", "CONTENT_ISOLATION_SCAN_OK",
     "database.py 内容表 SELECT 带 user 过滤、INSERT 带归属列（白名单除外）",
     ("scan",)),
    ("check_content_isolation.py", "CONTENT_ISOLATION_E2E_OK",
     "双账号端到端：新账号全页空、直访他人 404 同响应、存量 NULL 不可见、worker 产物归属、/activity /usage 视角",
     ("e2e",)),
]

# Per-user agent tokens for the browser extension and /agent/* door.
CHANNEL_GATES = [
    ("check_channel_isolation.py", "CHANNEL_ISOLATION_SCHEMA_OK",
     "agent_tokens 建表与存量全局密钥一次性映射 admin，吊销即失效",
     ("schema",)),
    ("check_channel_isolation.py", "CHANNEL_ISOLATION_TOKENS_OK",
     "token 解析用户：claim/events/读路径只认本人，跨用户与不存在同响应体",
     ("tokens",)),
    ("check_channel_isolation.py", "CHANNEL_ISOLATION_HEARTBEAT_OK",
     "心跳按用户分桶：登录态互不可见、离线互不影响、过期整桶清理、compose 按本人可选平台",
     ("heartbeat",)),
]

# check_*.py files that are deliberately not gates, with the reason. Empty is the
# healthy state: a script that is not a gate should not carry the check_ prefix.
NOT_GATES = {
    "check_agent_e2e.py": "needs a local wechatsync login; run manually with INSIGHTS_AGENT_E2E=1",
}

ALL_GATES = (ACCOUNT_GATES + ACCOUNT_JS_GATES + SETTINGS_GATES
             + X_FETCH_GATES + USAGE_GATES + BRIEF_GATES + AGENT_GATES + COMPOSE_GATES + VIDEO_GATES + MANUAL_POST_GATES
             + QUEUE_GATES + PAGING_GATES + IG_PEOPLE_GATES + ISOLATION_GATES + CHANNEL_GATES)


def _unpack(entry):
    """(file, token, claim, args) for a gate entry, with or without arguments."""
    filename, token, claim, *rest = entry
    return filename, token, claim, (rest[0] if rest else ())


def gate_inventory_problems():
    """Gates on disk that are not registered, and registered gates that are gone."""
    registered = {_unpack(entry)[0] for entry in ALL_GATES}
    on_disk = {os.path.basename(p) for p in glob.glob(os.path.join(HERE, "check_*.py"))}
    unregistered = sorted(on_disk - registered - set(NOT_GATES))
    missing = sorted(registered - on_disk)
    return unregistered, missing


def main():
    print("Completion gates\n")

    unregistered, missing = gate_inventory_problems()
    problems = []
    for name in unregistered:
        problems.append(f"{name} exists but is not registered in run_all_gates.py, "
                        f"so it would never run")
    for name in missing:
        problems.append(f"{name} is registered but the file does not exist")

    failures = []
    for entry in ALL_GATES:
        filename, token, claim, args = _unpack(entry)
        if filename in missing:
            continue
        started = time.time()
        passed, summary, detail = run_gate(filename, token, args)
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {claim}  ({time.time() - started:.1f}s)")
        if summary:
            print(f"       {summary}")
        if not passed:
            failures.append(" ".join((filename, *args)))
            print(f"       {detail}".replace("\n", "\n       "))
        print()

    if problems:
        print("Gate inventory:")
        for problem in problems:
            print(f"  - {problem}")
        print()

    if failures or problems:
        if failures:
            print(f"{len(failures)} of {len(ALL_GATES)} gates failed: {', '.join(failures)}")
        if problems:
            print(f"{len(problems)} gate inventory problem(s)")
        return 1

    print(f"All {len(ALL_GATES)} gates passed.")
    print("ALL_GATES_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
