import ast, io, os

SCOPED = {
    "add_standalone_post", "get_standalone_post", "add_scheduled_post", "list_standalone_posts",
    "get_scheduled_post", "list_scheduled_posts", "reorder_scheduled_posts", "move_posts_to_position",
    "update_scheduled_post_status", "get_scheduled_group_ids", "delete_standalone_post",
    "list_recent_prompts", "add_article", "add_social_post", "create_content_brief",
    "get_brief_run", "add_url_source", "update_standalone_post_video", "mark_standalone_post_used",
}
FILES = [
    "check_ig_people.py", "check_video.py", "check_bulk_platform.py", "check_crosspost.py",
    "check_queue_groups.py", "check_scheduler_accounts.py", "check_manual_posts.py",
    "check_domestic_publisher.py", "check_backcompat.py", "_video_ui_gate.py",
    "check_accounts_ui.py", "check_agent_push.py", "check_brief_fallback.py",
    "check_accounts_model.py", "check_compose_paging.py", "check_agent_ui.py",
    "check_login_without_identity.py", "check_bulk_platform_ui.py", "check_channel_defaults.py",
    "check_compose_js.py",
]
HERE = r"D:\git3\Insights\scripts"
bad = 0
total = 0
for fn in FILES:
    tree = ast.parse(io.open(os.path.join(HERE, fn), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in SCOPED:
                total += 1
                kws = {k.arg for k in node.keywords}
                positional_uid = any(
                    (isinstance(a, ast.Attribute) and a.attr == "uid")
                    or (isinstance(a, ast.Name) and a.id == "uid")
                    for a in node.args)
                if "user_id" not in kws and not positional_uid:
                    bad += 1
                    print(f"MISSING user_id: {fn}:{node.lineno} {name}")
print(f"{total} scoped calls checked, {bad} missing user_id")
