"""Gates for the AI usage meters: per-user scoping, model pricing, page isolation.

One script, one section per claim, picked by argument so each gate reports its
own token only after proving its claim against the real routes and the real
schema:

    python scripts/check_usage.py scoping    USAGE_SCOPING_OK
    python scripts/check_usage.py pricing    USAGE_PRICING_OK
    python scripts/check_usage.py page       USAGE_PAGE_OK

Usage rows carry the triggering user, and the page used to sum the whole
instance for everyone. ``scoping`` pins the read side: every aggregate, list,
count and export honours a ``user_id`` filter, a legacy row with no user stays
visible only in the unfiltered view, and ``usage_meter.user_context`` lets a
background thread attribute the usage it records to the user who created the
task. ``pricing`` pins the environment-variable pricing rule: a model priced via
``USAGE_PRICE_MODEL_*`` (normalized name, longest prefix, yuan per 1M tokens) is
billed at that rate ahead of the built-in tables, unknown models still fall back
to the default, and non-chat providers stay free. ``page`` drives the real
/usage and /usage.csv: the default view is the viewer's own data, ``?scope=all``
widens it for the admin only, a non-admin asking for all silently sees their
own, and costs render as yuan directly.

Every section runs on a throwaway database, so it makes no network calls and
cannot touch a real insights.db or a real account.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402


def _chat_response(prompt_tokens=1_000_000, completion_tokens=1_000_000):
    """A fake chat response whose usage block reads like OpenAI's."""
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                            input_tokens=None, output_tokens=None)
    return SimpleNamespace(usage=usage)


def _log(database, *, mode, category, cost, user_id, username):
    database.log_usage(
        mode=mode, category=category, provider="openai", model="gate-model",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        cost_cny=cost, user_id=user_id, username=username,
        db_path=database.DB_PATH,
    )


def section_scoping():
    directory, database, web, publisher, client = isolated_app()
    import usage_meter

    other_id = database.create_user("colleague", generate_password_hash("p"),
                                    db_path=database.DB_PATH)
    me = database.get_user_by_username("gate")

    _log(database, mode="reactive", category="gate_own", cost=1.0,
         user_id=me["id"], username="gate")
    _log(database, mode="proactive", category="gate_own", cost=2.0,
         user_id=me["id"], username="gate")
    _log(database, mode="reactive", category="gate_other", cost=4.0,
         user_id=other_id, username="colleague")
    _log(database, mode="proactive", category="gate_legacy", cost=8.0,
         user_id=None, username=None)

    mine = database.usage_totals(user_id=me["id"])
    check(mine["events"] == 2 and abs(mine["cost_cny"] - 3.0) < 1e-9,
          f"totals honour the user filter: {mine}")
    everything = database.usage_totals()
    check(everything["events"] == 4 and abs(everything["cost_cny"] - 15.0) < 1e-9,
          f"no filter sums the whole instance, legacy rows included: {everything}")

    by_mode = database.usage_by_mode(user_id=me["id"])
    check(set(by_mode) == {"reactive", "proactive"},
          f"by_mode honours the user filter: {by_mode}")
    by_cat = database.usage_by_category(user_id=other_id)
    check(len(by_cat) == 1 and by_cat[0]["category"] == "gate_other",
          f"by_category honours the user filter: {by_cat}")
    by_model = database.usage_by_model(user_id=other_id)
    check(len(by_model) == 1 and by_model[0]["model"] == "gate-model",
          f"by_model honours the user filter: {by_model}")
    daily = database.usage_daily(user_id=me["id"])
    check(len(daily) == 1 and abs(daily[0]["cost_cny"] - 3.0) < 1e-9,
          f"daily honours the user filter: {daily}")

    rows = database.list_usage(user_id=other_id)
    check(len(rows) == 1 and rows[0]["user_id"] == other_id,
          "list honours the user filter")
    check(database.count_usage(user_id=me["id"]) == 2,
          "count honours the user filter")
    exported = list(database.iter_usage_for_export(user_id=other_id))
    check(len(exported) == 1 and exported[0]["user_id"] == other_id,
          "the CSV export honours the user filter")

    # Background attribution: a worker thread wraps its work in user_context and
    # everything recorded inside lands on that user, tagged proactive.
    with usage_meter.user_context(other_id, "colleague"):
        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="gate-model")
    row = database.list_usage(user_id=other_id, limit=1)[0]
    check(row["mode"] == "proactive" and row["username"] == "colleague",
          "user_context attributes background usage to the task's owner")

    print("usage reads scope by user, legacy rows stay admin-only, and user_context attributes background work")
    print("USAGE_SCOPING_OK")


def section_pricing():
    directory, database, web, publisher, client = isolated_app()
    import usage_meter

    def last_cost():
        return database.list_usage(limit=1)[0]["cost_cny"]

    os.environ["USAGE_PRICE_MODEL_DEEPSEEK_CHAT_IN"] = "1"
    os.environ["USAGE_PRICE_MODEL_DEEPSEEK_CHAT_OUT"] = "2"
    try:
        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="deepseek-chat")
        check(abs(last_cost() - 3.0) < 1e-6,
              f"an env-priced model bills at its env rates: {last_cost()}")

        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="deepseek-chat-v3")
        check(abs(last_cost() - 3.0) < 1e-6,
              f"the env match is a longest prefix, so versioned names hit too: {last_cost()}")

        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="deepseek-v4-flash")
        check(abs(last_cost() - 3.0) < 1e-6,
              f"the built-in table carries DeepSeek's official yuan prices: {last_cost()}")

        os.environ["USAGE_PRICE_MODEL_GPT_4O_IN"] = "9"
        os.environ["USAGE_PRICE_MODEL_GPT_4O_OUT"] = "9"
        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="gpt-4o")
        check(abs(last_cost() - 18.0) < 1e-6,
              f"the env rule wins over the built-in table: {last_cost()}")
        del os.environ["USAGE_PRICE_MODEL_GPT_4O_IN"]
        del os.environ["USAGE_PRICE_MODEL_GPT_4O_OUT"]

        usage_meter.record_chat(_chat_response(200_000, 100_000), category="summary",
                                provider="anthropic", model="claude-haiku-4-5")
        check(abs(last_cost() - (0.2 * 7.2 + 0.1 * 36.0)) < 1e-6,
              f"an unenv-priced model still resolves through the built-in tables: {last_cost()}")

        usage_meter.record_chat(_chat_response(), category="summary",
                                provider="openai", model="totally-unknown-model")
        check(abs(last_cost() - 90.0) < 1e-6,
              f"an unknown model still falls back to the default rate: {last_cost()}")
    finally:
        os.environ.pop("USAGE_PRICE_MODEL_DEEPSEEK_CHAT_IN", None)
        os.environ.pop("USAGE_PRICE_MODEL_DEEPSEEK_CHAT_OUT", None)

    # Local providers stay free, env-priced name or not.
    usage_meter.record_chat(_chat_response(), category="summary",
                            provider="ollama", model="deepseek-chat")
    check(abs(last_cost()) < 1e-9, "non-chat providers are still recorded at $0")

    print("env-priced models bill at their env rates and outrank the built-in tables")
    print("USAGE_PRICING_OK")


def section_page():
    directory, database, web, publisher, client = isolated_app()

    other_id = database.create_user("colleague", generate_password_hash("p"),
                                    db_path=database.DB_PATH)
    me = database.get_user_by_username("gate")

    _log(database, mode="reactive", category="gate_own", cost=1.0,
         user_id=me["id"], username="gate")
    _log(database, mode="reactive", category="gate_other", cost=2.0,
         user_id=other_id, username="colleague")

    # Default view: the admin sees only their own rows, in yuan directly
    # (a stored 1.0 renders as ¥1.00 with no rate applied).
    r = client.get("/usage")
    body = r.get_data(as_text=True)
    check(r.status_code == 200, f"the page renders ({r.status_code})")
    check("gate_own" in body and "gate_other" not in body,
          "the default view shows only the viewer's own categories")
    check("¥1.00" in body, f"cost renders as yuan directly: found {'¥1.00' in body}")
    check('name="scope"' in body, "an admin gets the scope switch")

    r = client.get("/usage?scope=all")
    body = r.get_data(as_text=True)
    check("gate_other" in body and "colleague" in body,
          "scope=all widens the admin's view to everyone")

    # A non-admin is pinned to their own data, whatever they ask for.
    plain = web.app.test_client()
    with plain.session_transaction() as session:
        session["user_id"] = other_id
    r = plain.get("/usage?scope=all")
    body = r.get_data(as_text=True)
    check("gate_other" in body and "gate_own" not in body,
          "a non-admin's scope=all silently falls back to their own data")
    check('name="scope"' not in body, "a non-admin gets no scope switch")

    # The CSV export follows the same scope.
    mine_csv = client.get("/usage.csv").get_data(as_text=True)
    check("gate_own" in mine_csv and "gate_other" not in mine_csv,
          "the admin's default CSV holds only their own rows")
    all_csv = client.get("/usage.csv?scope=all").get_data(as_text=True)
    check("gate_other" in all_csv, "the admin's scope=all CSV holds everyone")
    plain_csv = plain.get("/usage.csv?scope=all").get_data(as_text=True)
    check("gate_other" in plain_csv and "gate_own" not in plain_csv,
          "a non-admin's CSV stays pinned to their own rows")

    print("the page defaults to the viewer's own data, scope=all is admin-only, and CSV follows the scope")
    print("USAGE_PAGE_OK")


SECTIONS = {
    "scoping": section_scoping,
    "pricing": section_pricing,
    "page": section_page,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_usage.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
