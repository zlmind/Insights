"""Gate for the /settings page and the settings layer behind it.

    python scripts/check_settings_ui.py    SETTINGS_UI_OK

The app used to read all of its configuration from the environment, which only
works for the one person running it. Settings now live in the ``app_settings``
table, edited on /settings by the instance admin, with ``appconfig`` resolving
each key saved-value → environment → default so an existing .env keeps working
until the same key is saved in the database.

This gate drives the real pages on a throwaway database: the first created
user must be the admin, a second account must get 403 on the page and its
POST, a secret saved through the form must be readable by ``appconfig`` but
never echoed back to the browser, a blank resubmit must keep the secret,
"clear" must fall back to the environment, and flipping ALLOW_SIGNUP from the
page must actually open /signup.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()

    import appconfig
    import insights
    from werkzeug.security import generate_password_hash

    # The bootstrap account is the admin; a second account is not.
    admin_id = database.create_user("second", generate_password_hash("p"),
                                    db_path=database.DB_PATH)
    check(database.get_user_by_username("gate")["is_admin"] == 1,
          "first created user is admin")
    check(database.get_user_by_username("second")["is_admin"] == 0,
          "later users are not admins")

    plain = web.app.test_client()
    uid = database.create_user("peasant", generate_password_hash("p"),
                               db_path=database.DB_PATH)
    with plain.session_transaction() as session:
        session["user_id"] = uid

    # Visibility and the admin gate.
    r = client.get("/settings")
    check(r.status_code == 200, f"admin sees /settings ({r.status_code})")
    check(b"OPENAI_API_KEY" in r.data, "registry keys are on the page")
    # The Western platforms' OAuth group is retired from the page: those
    # channels are no longer offered, so there is nothing left to wire up. The
    # registry keeps the keys, so old saved values still resolve.
    check(b"LINKEDIN_CLIENT_ID" not in r.data,
          "the retired platforms OAuth group is hidden on the page")
    check(b"ALLOW_SIGNUP" in r.data, "the groups that remain still render")
    client.post("/settings", data={"LINKEDIN_CLIENT_ID": "legacy-app-id"})
    check(appconfig.get("LINKEDIN_CLIENT_ID") == "legacy-app-id",
          "a hidden group's key still saves through the POST")
    r = plain.get("/settings")
    check(r.status_code == 403, f"non-admin gets 403 ({r.status_code})")
    r = plain.post("/settings", data={"OPENAI_MODEL": "sneaky"})
    check(r.status_code == 403, f"non-admin POST gets 403 ({r.status_code})")
    check(appconfig.get("OPENAI_MODEL") != "sneaky", "rejected POST saved nothing")

    # Save a secret and a plain value through the real form.
    r = client.post("/settings", data={
        "OPENAI_API_KEY": "sk-test-secret",
        "OPENAI_MODEL": "saved-model",
    }, follow_redirects=True)
    check(r.status_code == 200, "admin POST succeeds")
    check(b"sk-test-secret" not in r.data, "the page never echoes the secret")
    check(appconfig.get("OPENAI_API_KEY") == "sk-test-secret",
          "appconfig reads the saved secret")
    check(appconfig.get("OPENAI_MODEL") == "saved-model",
          "appconfig reads the saved value")
    check(insights.OPENAI_MODEL == "saved-model",
          "the LLM module picks the saved model up live")

    # A blank secret keeps the saved one; a new value replaces it.
    client.post("/settings", data={"OPENAI_API_KEY": "", "OPENAI_MODEL": ""})
    check(appconfig.get("OPENAI_API_KEY") == "sk-test-secret",
          "blank secret resubmit keeps the saved value")
    client.post("/settings", data={"OPENAI_API_KEY": "sk-rotated"})
    check(appconfig.get("OPENAI_API_KEY") == "sk-rotated",
          "a typed secret replaces the saved one")

    # Clear falls back to the environment, then the default.
    os.environ["TAVILY_API_KEY"] = "env-key"
    client.post("/settings", data={"TAVILY_API_KEY": "db-key"})
    check(appconfig.get("TAVILY_API_KEY") == "db-key", "db beats env")
    client.post("/settings", data={"clear_TAVILY_API_KEY": "1"})
    check(appconfig.get("TAVILY_API_KEY") == "env-key", "clear falls back to env")

    # ALLOW_SIGNUP from the page opens registration for strangers.
    plain_session = web.app.test_client()
    r = plain_session.get("/signup")
    check(r.status_code == 404, f"signup closed by default ({r.status_code})")
    client.post("/settings", data={"ALLOW_SIGNUP": "true"})
    r = plain_session.get("/signup")
    check(r.status_code == 200, f"signup opens from the setting ({r.status_code})")
    r = plain_session.post("/signup", data={
        "username": "newcomer", "password": "longenough1", "confirm": "longenough1",
    })
    check(database.get_user_by_username("newcomer") is not None,
          "stranger could register while the setting is on")
    check(database.get_user_by_username("newcomer")["is_admin"] == 0,
          "a signup during open registration is still not an admin")

    # Cloudinary credentials from the page reach the upload path.
    client.post("/settings", data={
        "CLOUDINARY_CLOUD_NAME": "test-cloud",
        "CLOUDINARY_API_KEY": "k",
        "CLOUDINARY_API_SECRET": "s",
    })
    check(web.cloudinary_ready() is True, "cloudinary_ready with saved credentials")
    client.post("/settings", data={"clear_CLOUDINARY_CLOUD_NAME": "1"})
    check(web.cloudinary_ready() is False, "cloudinary_ready without credentials")

    print("SETTINGS_UI_OK")


if __name__ == "__main__":
    main()
