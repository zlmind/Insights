"""Gate for the content brief's no-source fallback: write from model knowledge.

    python scripts/check_brief_fallback.py    BRIEF_FALLBACK_OK

A brief whose research surfaces nothing used to produce a silent "success" with
zero drafts: the posts path only drafted straight from the prompt when sourcing
was unticked entirely, and the articles path had no fallback at all — so the
default "use web search" brief on an instance with no search key always wrote
nothing. This gate pins the new contract, with ``research_engine.run_research``
and the ``insights`` generators replaced by recorders so nothing touches a
network:

* research reports ``no_candidates`` (no provider / search failed / zero hits)
  and the brief wants articles → one article is drafted from the instructions,
  the run carries a ``no_sources_model_knowledge`` warning;
* the same for posts → the prompt-only posts path runs;
* research found sources but none were new (``all_duplicates``) → still skipped
  with ``no_new_sources``, so scheduled runs never spam repetitive generic
  content;
* the fallback's generator failing marks the run ``error`` — never a silent
  "success" with zero drafts.

Everything runs on a throwaway database, so it cannot touch a real insights.db.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()
    uid = database.get_user_by_username('gate')['id']
    import content_agent
    import insights
    import research_engine

    calls = {"article": 0, "posts": 0}
    real_article = insights.generate_article
    real_posts = insights.generate_posts_from_prompt
    real_research = research_engine.run_research

    def fake_article(**kwargs):
        calls["article"] += 1
        check(bool(kwargs.get("topic")), "the article fallback passes the brief's instructions as topic")
        return "FAKE ARTICLE BODY"

    def fake_posts(**kwargs):
        calls["posts"] += 1
        return {p: [f"post via {p}"] for p in kwargs["platforms"]}

    def research_with(warnings):
        def _run(brief, **kw):
            return research_engine.ResearchResult(
                topic=brief.topic, query_plan=["q"], research_brief="",
                items=[], stats=research_engine.ResearchStats(), warnings=warnings)
        return _run

    insights.generate_article = fake_article
    insights.generate_posts_from_prompt = fake_posts

    try:
        # --- articles: search ran, nothing found → draft from knowledge ----
        research_engine.run_research = research_with(["no_candidates"])
        bid = database.create_content_brief(
            name="AI新闻", instructions="写一篇AI的新闻", content_type="articles",
            platforms=["zhihu"], article_count=1, posts_per_platform=1,
            use_web_search=True, use_saved_sources=False, cadence="manual", user_id=uid)
        res = content_agent.run_brief(bid, trigger="manual")
        check(res["articles_created"] == 1 and calls["article"] == 1,
              f"a no-candidate articles brief drafts one article from knowledge: {res}")
        check("no_sources_model_knowledge" in res["warnings"],
              f"the fallback says so in the warnings: {res['warnings']}")
        check(res["status"] == "success", f"the run still succeeds: {res}")

        # --- posts: same fallback uses the prompt-only posts path ----------
        research_engine.run_research = research_with(["no_candidates"])
        bid = database.create_content_brief(
            name="AI新闻帖", instructions="写AI新闻帖子", content_type="posts",
            platforms=["zhihu", "toutiao"], posts_per_platform=1,
            use_web_search=True, use_saved_sources=False, cadence="manual", user_id=uid)
        res = content_agent.run_brief(bid, trigger="manual")
        check(res["posts_created"] == 2 and calls["posts"] == 1,
              f"a no-candidate posts brief drafts from the prompt: {res}")
        check("no_sources_model_knowledge" in res["warnings"],
              "the posts fallback says so in the warnings")

        # --- search worked but nothing new: still skipped, no generic spam --
        calls["posts"] = 0
        research_engine.run_research = research_with(["all_duplicates"])
        bid = database.create_content_brief(
            name="旧闻", instructions="写点旧闻", content_type="posts",
            platforms=["zhihu"], posts_per_platform=1,
            use_web_search=True, use_saved_sources=False, cadence="manual", user_id=uid)
        res = content_agent.run_brief(bid, trigger="manual")
        check(res["posts_created"] == 0 and calls["posts"] == 0,
              f"an all-duplicates run still skips generation: {res}")
        check("no_new_sources" in res["warnings"], "the skip says why in the warnings")

        # --- a failing fallback generator is an error, not a silent success -
        def dead_article(**kwargs):
            raise RuntimeError("model 400")
        insights.generate_article = dead_article
        research_engine.run_research = research_with(["no_candidates"])
        bid = database.create_content_brief(
            name="坏模型", instructions="写一篇AI的新闻", content_type="articles",
            platforms=["zhihu"], article_count=1,
            use_web_search=True, use_saved_sources=False, cadence="manual", user_id=uid)
        res = content_agent.run_brief(bid, trigger="manual")
        run_row = database.get_brief_run(res["run_id"], user_id=uid)
        check(res["status"] == "error" and run_row is not None
              and "All content generation failed" in (run_row["error_message"] or ""),
              f"a failed fallback marks the run error: {res}")
    finally:
        insights.generate_article = real_article
        insights.generate_posts_from_prompt = real_posts
        research_engine.run_research = real_research

    print("briefs with no sources draft from model knowledge; all-duplicates still skips; failures surface as errors")
    print("BRIEF_FALLBACK_OK")


if __name__ == "__main__":
    main()
