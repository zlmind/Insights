"""Gate for the channel pickers' default state: nothing ships pre-ticked.

    python scripts/check_channel_defaults.py    CHANNEL_DEFAULTS_OK

The compose and article pages' 发布渠道 boxes used to default to all-ticked,
so generating meant "blast every channel" unless you unticked by hand. The
default is now none-ticked; an empty selection is refused with a prompt, not
a silent blast.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()

    html = client.get("/compose").get_data(as_text=True)
    check('id="platform-checkboxes"' in html, "the compose channel picker renders")
    check("platform-checkbox-box selected" not in html,
          "no compose channel box carries the pre-ticked style")
    check(not re.search(r'id="platform-[a-z]+"[^>]*checked', html),
          "no compose channel input ships pre-ticked")

    article_id = database.add_article(None, "gate topic", "style", "gate body")
    page = client.get(f"/article/{article_id}").get_data(as_text=True)
    check('id="platform-checkboxes"' in page, "the article channel picker renders")
    check(not re.search(r'id="platform-[a-z]+"[^>]*checked', page),
          "no article channel input ships pre-ticked")

    print("CHANNEL_DEFAULTS_OK")


if __name__ == "__main__":
    main()
