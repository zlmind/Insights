"""Gate for the X/Twitter fetch path: URL routing, payload mapping, error shape.

    python scripts/check_x_fetch.py    X_FETCH_OK

x.com can never go through the generic trafilatura path (login wall, and on a
China-network host the GFW besides), so tweet URLs take fxtwitter's open API.
This gate swaps the HTTP seam for fixtures and pins: which URLs route to the
tweet path, how the fxtwitter payload maps onto source_data, the fail-closed
error when the API dies, and the single message prefix (the doubled
"Failed to fetch content from URL: Failed to fetch..." read is what this
restructure retired).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _accounts_gate import check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL_SHAPE = {
    "code": 200,
    "tweet": {
        "text": "15 Sites That Reveal Everything About Your Home\n\n1.) site one",
        "author": {"name": "Monica", "screen_name": "monicaa_AI"},
        "media": {"photos": [{"url": "https://pbs.twimg.com/media/abc.jpg"}]},
    },
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def main():
    import insights

    # --- non-X URLs stay on the trafilatura path ---
    for url in ("https://blog.example.com/post/1",
                "https://x.com/monicaa_AI",
                "https://x.com/monicaa_AI/status/",
                "not a url at all"):
        check(insights._fetch_tweet_source(url) is None,
              f"a non-tweet URL routes to trafilatura: {url}")

    # --- X URL variants all route to the tweet path ---
    for url in ("https://x.com/monicaa_AI/status/2108523986804158792",
                "https://twitter.com/monicaa_AI/status/2108523986804158792",
                "https://mobile.twitter.com/monicaa_AI/status/2108523986804158792",
                "https://x.com/monicaa_AI/status/2108523986804158792?s=20"):
        seen = {}

        def fake(endpoint, _url=url, _seen=seen):
            _seen["endpoint"] = endpoint
            return REAL_SHAPE

        insights._tweet_fetch_json = fake
        data = insights._fetch_tweet_source(url)
        check(data is not None and data["content"].startswith("15 Sites"),
              f"tweet payload maps onto source_data: {url}")
        check("/status/2108523986804158792" in seen.get("endpoint", ""),
              f"the fxtwitter endpoint carries the status id: {url}")

    # --- payload mapping: author prefix, og_image, truncation-free content ---
    insights._tweet_fetch_json = lambda endpoint: REAL_SHAPE
    data = insights._fetch_tweet_source("https://x.com/monicaa_AI/status/123")
    check(data["title"].startswith("Monica on X: 15 Sites"),
          "the title leads with the author and the text head")
    check(data["og_image"] == "https://pbs.twimg.com/media/abc.jpg",
          "the first photo becomes og_image")
    check(data["description"].startswith("15 Sites"), "description carries the text")

    no_media = {"code": 200, "tweet": {"text": "plain", "author": {"name": "A"}}}
    insights._tweet_fetch_json = lambda endpoint: no_media
    data = insights._fetch_tweet_source("https://x.com/a/status/1")
    check(data["og_image"] is None, "a tweet without photos has no og_image")

    # --- API failure: readable, single-prefix, fail-closed ---
    def dead(endpoint):
        raise RuntimeError("conn refused")
    insights._tweet_fetch_json = dead
    try:
        insights._fetch_tweet_source("https://x.com/a/status/1")
        check(False, "an API failure must raise")
    except RuntimeError as e:
        check("fxtwitter" in str(e), "the API failure names the path")
        check(str(e).count("Failed to fetch") == 0,
              "the tweet-path error carries no trafilatura prefix")

    bad_code = {"code": 404, "tweet": {}}
    insights._tweet_fetch_json = lambda endpoint: bad_code
    try:
        insights._fetch_tweet_source("https://x.com/a/status/1")
        check(False, "a non-200 payload must raise")
    except RuntimeError as e:
        check("404" in str(e), "the non-200 error names the code")

    # --- the generic path still raises with exactly one prefix ---
    import trafilatura
    real_fetch = trafilatura.fetch_url
    trafilatura.fetch_url = lambda u: None
    try:
        insights.generate_posts_from_url("https://blog.example.com/post/1")
        check(False, "an empty download must raise")
    except RuntimeError as e:
        check(str(e).startswith("Failed to fetch content from URL: "),
              "the generic error keeps its prefix")
        check(str(e).count("Failed to fetch content from URL") == 1,
              "and carries it exactly once — the doubled read is gone")
    finally:
        trafilatura.fetch_url = real_fetch

    print("X_FETCH_OK")


if __name__ == "__main__":
    main()
