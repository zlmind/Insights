"""Chinese prompt set, selected wherever generated content should be 中文.

The English prompts live inline in ``insights.py`` / ``research_engine.py`` /
``content_agent.py`` and stay untouched: ``lang="en"`` (the default) must send
byte-identical requests to before. When a generation call resolves to Chinese
— a domestic platform like 小红书 for posts, or the request/worker language
for summaries and articles — the builders here return the Chinese versions.

Structure mirrors the English sites: small constants for the shared pieces
(tone, platform guidelines, the JSON format instruction) and one ``*_messages``
builder per generation call, returning the ready ``[system, user]`` message
list. JSON keys, platform identifiers and the overall reply shape stay exactly
as the English prompts specify them — only the prose and the output language
change.
"""

NO_EM_DASH_RULE = (
    "输出中不要使用破折号（——）或连接号（–）作插入语，"
    "改用逗号、句号、括号或改写句子。"
    "只有在连接词语时才使用普通的连字符（-）。"
)

# Sits in every Chinese system prompt: it is what makes the output Chinese.
OUTPUT_LANGUAGE_RULE = (
    "全部用简体中文写作。技术专有名词、产品名和公司名可保留英文。"
    "话题标签以 # 开头，可以直接用中文标签（如 #人工智能）。"
)

TONE_GUIDES = {
    "professional": "专业、权威，适合商业与行业受众",
    "casual": "轻松口语化，友好、有亲和力",
    "witty": "机智风趣，在合适的地方加入幽默",
    "educational": "教育向、信息量大，注重讲解与启发",
    "promotional": "推广导向、有说服力，推动读者行动",
}

PLATFORM_GUIDELINES = {
    "xiaohongshu": (
        "小红书笔记：第一行是标题，不超过 20 个字，可带 1-2 个 emoji；"
        "随后空一行接正文，正文 600-1000 字，口语化、像真实用户在分享心得，"
        "多用 emoji 和短段落，可以用小标题分点；结尾另起一行放 3-6 个"
        "话题标签（#标签 形式，如 #人工智能）。正文里不要放链接，"
        "小红书正文链接不可点击"
    ),
    "douyin": (
        "抖音文案：第一行是刷到就停下的钩子，不超过 20 个字；随后接 200-500 字"
        "口播风正文，短段落、多 emoji、口语化；结尾另起一行放 3-5 个"
        "热点话题标签（#标签 形式）。文案里不要放链接，抖音链接不可点击"
    ),
    "wechat": (
        "公众号推文：先给一个有点开欲的标题（不超过 30 个字），"
        "随后是 800-2000 字的文章，短段落、适当用小标题分节，"
        "结尾引导关注或留言；话题标签最多 3 个（#标签 形式）。"
        "正文外的位置不要放链接"
    ),
    "zhihu": "知乎文章：开头直接给结论，正文用小标题分节论证，克制口语，结尾一句观点收束，不用话题标签",
    "toutiao": "头条号文章：标题信息量足可带数字，开头三句给足钩子，段落短节奏快，口语化，结尾引导评论互动",
}

DEFAULT_PLATFORM_GUIDELINE = "标准社交媒体帖子，带话题标签"

_JSON_SINGLE = "\n每个键对应一条帖子字符串。\n"
_JSON_ARRAY = "\n每个键对应一个包含 {n} 条帖子的数组。\n"

def tone_instruction(tone: str) -> str:
    """Chinese counterpart of the ``TONE_GUIDES`` lookup."""
    return TONE_GUIDES.get(tone, TONE_GUIDES["professional"])

def platform_list(platforms) -> str:
    """Chinese counterpart of the ``- PLATFORM: guideline`` block."""
    return "\n".join(
        f"- {p.upper()}: {PLATFORM_GUIDELINES.get(p, DEFAULT_PLATFORM_GUIDELINE)}"
        for p in platforms
    )

def build_format_instruction(platforms, posts_per_platform: int) -> str:
    """Chinese counterpart of ``insights._build_format_instruction``.

    Same JSON contract as English (keys are platform identifiers), Chinese
    prose around it.
    """
    platform_keys = ", ".join(f'"{p}"' for p in platforms)
    if posts_per_platform > 1:
        example_val = '["第一条帖子 #话题标签", "第二条帖子 #话题标签"]'
    else:
        example_val = '"这里写帖子内容 #话题标签"'
    json_example = "{" + ", ".join(f'"{p}": {example_val}' for p in platforms) + "}"

    lines = (
        "\n只回复一个 JSON 对象，不要输出任何其他文字。\n"
        f"只能使用这些键：{platform_keys}\n"
    )
    if posts_per_platform > 1:
        lines += _JSON_ARRAY.format(n=posts_per_platform)
    else:
        lines += _JSON_SINGLE
    lines += f"示例：\n{json_example}"
    return lines

# ── Social posts ──────────────────────────────────────────────────────────

def posts_from_prompt_messages(*, prompt, tone, extra_context, platform_list,
                               platforms, n):
    """``generate_posts_from_prompt`` messages, Chinese."""
    return [
        {"role": "system", "content": (
            "你是一位社交媒体内容创作与营销专家。"
            "你创作能引起受众共鸣、针对平台优化的帖子。"
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE + " "
            "你始终只回复合法的 JSON。"
        )},
        {"role": "user", "content": (
            "根据下面的主题/提示创作社交媒体帖子：\n\n"
            f"主题/提示：{prompt}\n\n"
            f"语气：{tone_instruction(tone)}\n\n"
            + (f"补充背景：\n{extra_context}\n\n" if extra_context else "")
            + f"为以下平台创作帖子：\n{platform_list}\n\n"
            "每条帖子：\n"
            "1. 针对该平台的受众和格式优化\n"
            "2. 带相关话题标签\n"
            "3. 有吸引力、易于传播\n"
            "4. 扣题并提供价值\n"
            + build_format_instruction(platforms, n)
        )},
    ]

def posts_from_url_messages(*, url, extracted_content, tone, extra_context,
                            platform_list, platforms, n):
    """``generate_posts_from_url`` messages, Chinese."""
    return [
        {"role": "system", "content": (
            "你是一位专注于分享和推广网络内容的社交媒体创作者。"
            "你创作总结、点评或推广文章和网页的帖子。"
            "关键要求：根据网页生成帖子时，每条帖子、每个平台都必须原样包含源 URL。"
            "不得缩短、改写、省略，也不得用“[链接]”“链接在简介里”之类的占位文本代替。"
            "URL 必须以可点击链接的形式出现在帖子正文中。"
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE + " "
            "你始终只回复合法的 JSON。"
        )},
        {"role": "user", "content": (
            "创作社交媒体帖子来分享以下网页内容：\n\n"
            f"源 URL（每条帖子必须原样包含）：{url}\n\n"
            f"{extracted_content}\n\n"
            f"语气：{tone_instruction(tone)}\n\n"
            + (f"补充背景：\n{extra_context}\n\n" if extra_context else "")
            + f"为以下平台创作帖子：\n{platform_list}\n\n"
            "每条帖子：\n"
            "1. 概括或点评要点\n"
            f"2. 必须在帖子正文原样包含源 URL（{url}），每个平台都不例外\n"
            "3. 带相关话题标签\n"
            "4. 有吸引力，引导点击和互动\n"
            + build_format_instruction(platforms, n)
        )},
    ]

def posts_from_text_messages(*, text, topic, source_url, tone, extra_context,
                             platform_list, platforms, n):
    """``generate_posts_from_text`` messages, Chinese."""
    if source_url:
        url_rule = (
            " 关键要求：本条内容带有源 URL。每条帖子、每个平台都必须原样包含该 URL，"
            "不得缩短、改写、省略，也不得用“[链接]”“链接在简介里”之类的占位文本代替，"
            "URL 必须以可点击链接的形式出现在帖子正文中。"
        )
        steps = (
            "1. 抓住核心信息或洞见\n"
            f"2. 必须在帖子正文原样包含源 URL（{source_url}），每个平台都不例外\n"
            "3. 针对平台格式优化\n"
            "4. 带相关话题标签\n"
            "5. 有吸引力、易于传播\n"
        )
    else:
        url_rule = ""
        steps = (
            "1. 抓住核心信息或洞见\n"
            "2. 针对平台格式优化\n"
            "3. 带相关话题标签\n"
            "4. 有吸引力、易于传播\n"
        )
    topic_section = f"主题：{topic}\n\n" if topic else ""
    url_section = f"源 URL（每条帖子必须原样包含）：{source_url}\n\n" if source_url else ""
    return [
        {"role": "system", "content": (
            "你是一位社交媒体内容创作者。你把文字内容改写成针对不同平台优化的、"
            "有吸引力的帖子。"
            f"{url_rule} "
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE + " "
            "你始终只回复合法的 JSON。"
        )},
        {"role": "user", "content": (
            "把下面的内容改写成社交媒体帖子：\n\n"
            f"{topic_section}"
            f"{url_section}"
            f"内容：\n{text[:5000]}\n\n"
            f"语气：{tone_instruction(tone)}\n\n"
            + (f"补充背景：\n{extra_context}\n\n" if extra_context else "")
            + f"为以下平台创作帖子：\n{platform_list}\n\n"
            "每条帖子：\n"
            + steps
            + build_format_instruction(platforms, n)
        )},
    ]

def posts_from_images_messages(*, focus, context, tone, platform_list,
                               platforms, n):
    """``generate_posts_from_images`` messages, Chinese (images appended by caller)."""
    text = (
        "看这张图，围绕它写社交媒体帖子。\n"
        + (f"重点：{focus}\n" if focus else "")
        + (f"背景：{context}\n" if context else "")
        + f"语气：{tone_instruction(tone)}\n"
        + f"平台：{platform_list}\n\n"
        + build_format_instruction(platforms, n)
    )
    return [
        {"role": "system", "content": (
            "你是一位社交媒体内容创作者。你围绕图片撰写有吸引力的帖子。"
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE + " "
            "你始终只回复合法的 JSON。"
        )},
        {"role": "user", "content": [{"type": "text", "text": text}]},
    ]

def summarize_messages(text: str) -> list:
    """``summarize_text`` messages, Chinese."""
    return [{"role": "user", "content": (
        "总结下面的文字。" + NO_EM_DASH_RULE + "\n\n" + text
    )}]

def action_items_messages(text: str) -> list:
    """``extract_action_items`` messages, Chinese."""
    return [{"role": "user", "content": (
        "从下面的文字中提炼一份简明的行动项列表。每行一条，"
        "不要附加任何说明。用简体中文。" + NO_EM_DASH_RULE + "\n" + text
    )}]

# Article source labels mirror the English pair (podcast vs text article).

def article_messages(*, transcript, summary, topic, podcast_title,
                     episode_title, style, extra_context, is_text_source):
    """``generate_article`` messages, Chinese."""
    style_guides = {
        "blog": "用有感染力、对话感的博客文风写作，带个人视角。",
        "news": "用专业的新闻报道文体写作，客观、以事实为主。",
        "opinion": "写成观点/评论文章，立场清晰、有分析。",
        "technical": "写成面向从业者的技术深度文，讲解详尽。",
    }
    style_instruction = style_guides.get(style, style_guides["blog"])

    extra_section = ""
    if extra_context:
        extra_section = (
            f"\n作者补充的背景：\n{extra_context}\n\n"
            "请把上面的背景、观点或要求融入文章。\n"
        )

    if is_text_source:
        source_type = "文章"
        source_label = "来源出版物"
        content_label = "文章"
        full_label = "文章全文"
        credit = (
            "6. 重要：在文章结尾加一个标题为“## 阅读原文”的小节，"
            "点名致谢来源出版物，提到具体的文章标题，并鼓励读者去阅读原文、"
            "关注该出版物获取更多优质内容。语气要真诚、有谢意，"
            "不要写成公式化的免责声明。"
        )
    else:
        source_type = "播客"
        source_label = "来源播客"
        content_label = "单集"
        full_label = "完整文字稿"
        credit = (
            "6. 重要：在文章结尾加一个标题为“## 收听完整单集”的小节，"
            "点名致谢来源播客，提到具体的单集标题，并鼓励读者去收听完整讨论、"
            "关注该播客获取更多优质内容。语气要真诚、有热情，"
            "不要写成公式化的免责声明。"
        )

    return [
        {"role": "system", "content": (
            "你是一位擅长网络安全、隐私和科技话题的资深技术作者。"
            "你写的文章考证严谨、引人入胜，能让读者有所收获。"
            "文章使用 Markdown 格式，在合适的地方使用标题、段落和强调。"
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE
        )},
        {"role": "user", "content": (
            f"根据下面的{source_type}内容，写一篇聚焦以下主题的文章：{topic}\n\n"
            f"文风：{style_instruction}\n\n"
            f"{source_label}：{podcast_title}\n"
            f"{content_label}：{episode_title}\n\n"
            f"{extra_section}"
            f"摘要：\n{summary}\n\n"
            f"{full_label}：\n{transcript[:15000]}\n\n"
            "写一篇有吸引力的文章（800-1500 字），要求：\n"
            "1. 标题抓人\n"
            "2. 对主题给出有价值的洞见\n"
            f"3. 引用{source_type}中的具体内容\n"
            "4. 结尾有力，给出可带走的要点\n"
            "5. 适合科技/安全领域的读者\n"
            + credit
        )},
    ]

def refine_messages(*, current_content, user_feedback, article_topic) -> list:
    """``refine_article`` messages, Chinese."""
    return [
        {"role": "system", "content": (
            "你是一位专注科技与网络安全内容的资深编辑。"
            "你根据用户反馈润色和改进文章，同时保持文章的语气、结构和要点。"
            "以 Markdown 格式返回修改后的完整文章。"
            " " + OUTPUT_LANGUAGE_RULE + " "
            + NO_EM_DASH_RULE
        )},
        {"role": "user", "content": (
            "请根据我的反馈修改下面的文章。\n\n"
            f"文章主题：{article_topic}\n\n"
            f"当前文章：\n{current_content}\n\n"
            f"我的反馈/要求：\n{user_feedback}\n\n"
            "请按我的反馈修改并返回完整的修改后文章（Markdown 格式）。"
            "除非我明确要求，否则保持整体结构不变；"
            "除非另有指示，保持原有的语气和文风。"
        )},
    ]

def condense_chunk_messages(chunk: str) -> list:
    """``insights._summarize_chunk`` messages, Chinese."""
    return [
        {"role": "system", "content": (
            "你从一个长文档的节选中提取关键事实、洞见、数据、引文和结论，"
            "用简明的要点列表回复。保留能成为优质社交媒体素材的具体细节"
            "（数字、人名、发现、直接引语）。不要加评论、开场白或总结，"
            "只输出提取的要点。用简体中文。"
        )},
        {"role": "user", "content": f"提取这个节选的要点：\n\n{chunk}"},
    ]

# ── Research pipeline (content agent) ─────────────────────────────────────

def query_plan_messages(*, topic, audience, keywords, max_queries) -> list:
    """``research_engine._build_query_plan`` messages, Chinese."""
    aud = f" 目标受众是{audience}。" if audience else ""
    kw = f" 必须与这些内容相关：{'、'.join(str(k) for k in keywords)}。" if keywords else ""
    return [
        {"role": "system", "content": (
            "你生成多样化、高信息量的网络搜索查询词。"
            "只回复一个由简短查询字符串组成的 JSON 数组。"
        )},
        {"role": "user", "content": (
            f"主题：{topic[:1000]}{aud}{kw} "
            f"请给出 {max_queries} 个互不相同的搜索查询词，每个不超过 12 个词，"
            "合在一起能较全面地覆盖该主题。用简体中文写查询词。"
            "只回复一个由字符串组成的 JSON 数组。"
        )},
    ]

def synthesize_messages(*, topic, audience, sources_joined) -> list:
    """``research_engine._synthesize_brief`` messages, Chinese."""
    aud = f" 目标受众是{audience}。" if audience else ""
    return [
        {"role": "system", "content": (
            "你把研究笔记综合成一份写作者可以直接使用的简明简报。"
            "写 4 到 6 句话。用简体中文。" + insights_no_em_dash()
        )},
        {"role": "user", "content": (
            f"主题：{topic}。{aud}\n\n来源：\n{sources_joined}\n\n"
            "写一段简短的综合概述，突出值得覆盖的关键角度、事实和主题。"
        )},
    ]

def insights_no_em_dash() -> str:
    """Re-exported so research_engine can reuse the same rule text."""
    return NO_EM_DASH_RULE

# ── Content agent context labels ──────────────────────────────────────────
# The agent injects brief metadata into ``extra_context``; when the brief runs
# Chinese, the labels around that metadata switch too. (A brief's post
# generation still splits per platform language inside insights.py, so these
# labels are chosen by the brief's own language, not the platform's.)

def creative_context(audience_persona, keywords) -> str:
    """``content_agent._creative_context``, Chinese labels."""
    parts = []
    if audience_persona:
        parts.append(f"目标受众/画像：{audience_persona}。")
    kws = keywords or []
    if kws:
        parts.append(
            "在每条帖子中自然地融入这些关键词/话题标签/行动号召："
            + "，".join(str(k) for k in kws) + "。"
        )
    return "\n".join(parts)

def editorial_angle(instructions: str) -> str:
    return f"编辑视角：{instructions}"

def why_relevant(reason: str) -> str:
    return f"该来源的相关性：{reason}"
