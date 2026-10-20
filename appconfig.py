"""Instance settings: what an admin saves on /settings wins over .env.

The app used to read every piece of configuration straight from environment
variables, which works for the one person running it and stops working the
moment other people use it — nobody hands each teammate's OpenAI key to a
server admin to paste into a .env file. This module is the one seam between
the two worlds:

    get("OPENAI_API_KEY")   # saved setting → environment → registry default

The registry below keeps each key's original environment-variable name, its
default and whether it is a secret, and groups them for the settings page.
Readers elsewhere in the codebase call :func:`get` instead of ``os.environ``
so a deployment can start from .env and migrate to the database one key at a
time — nothing breaks while a key exists only in the file.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import database

# Group id, heading shown on /settings. Order is the page order.
SETTING_GROUPS: List[tuple] = [
    ("ai", "AI service"),
    ("platforms", "Platform apps (OAuth credentials)"),
    ("domestic", "国内渠道（文章推送）"),
    ("media", "Image hosting & stock photos"),
    ("search", "Web search"),
    ("access", "Access"),
]

# key → metadata. ``secret`` values are written masked in the UI and never
# sent back to the browser; ``choices`` renders a <select> instead of a text
# input. Defaults mirror what the old os.environ.get calls passed.
SETTINGS: Dict[str, dict] = {
    # ── AI service ──
    "LLM_PROVIDER": {
        "group": "ai", "label": "Cloud LLM provider",
        "choices": ["openai", "anthropic"], "default": "openai",
        "help": "Text and vision generation. Whisper and thumbnails always use OpenAI.",
    },
    "OPENAI_API_KEY": {
        "group": "ai", "label": "OpenAI API key", "secret": True,
        "help": "Also used for transcription and thumbnail images even when the provider is Anthropic.",
    },
    "OPENAI_BASE_URL": {
        "group": "ai", "label": "OpenAI-compatible base URL",
        "help": "Send chat calls to another /chat/completions endpoint instead of "
                "api.openai.com — a GLM proxy, or DeepSeek (https://api.deepseek.com) "
                "with the model set to deepseek-chat. Leave blank for OpenAI itself.",
    },
    "OPENAI_MODEL": {
        "group": "ai", "label": "OpenAI model", "default": "glm-5.3",
    },
    "OPENAI_REASONING_EFFORT": {
        "group": "ai", "label": "OpenAI reasoning effort",
        "choices": ["low", "medium", "high", "xhigh", "max"], "default": "low",
    },
    "ANTHROPIC_API_KEY": {
        "group": "ai", "label": "Anthropic API key", "secret": True,
        "help": "Required only when the provider is anthropic.",
    },
    "ANTHROPIC_MODEL": {
        "group": "ai", "label": "Anthropic model", "default": "claude-opus-5-5",
    },
    "ANTHROPIC_EFFORT": {
        "group": "ai", "label": "Anthropic thinking depth",
        "choices": ["low", "medium", "high", "xhigh", "max"], "default": "low",
    },
    "OLLAMA_BASE_URL": {
        "group": "ai", "label": "Ollama base URL",
        "default": "http://localhost:11434",
        "help": "Local models, used for free post generation and library captioning.",
    },
    "OLLAMA_VISION_MODEL": {
        "group": "ai", "label": "Ollama vision model", "default": "llama3.2-vision",
    },
    "OLLAMA_TEXT_MODEL": {
        "group": "ai", "label": "Ollama text model", "default": "llama3.2",
    },
    "USAGE_COST_MULTIPLIER": {
        "group": "ai", "label": "Usage cost multiplier",
        "default": "1.0",
        "help": "Cost recorded into the database = real model price × this factor "
                "(e.g. 1.2 stores 20% above list price). Affects events logged "
                "after the change; existing rows keep their stored price.",
    },
    # ── 国内渠道（文章推送）──
    "AGENT_TOKEN": {
        "group": "domestic", "label": "Agent 共享密钥", "secret": True,
        "help": "本机 agent 与服务器之间的 Bearer Token，两侧必须一致。"
                "用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成。",
    },
    "WECHATSYNC_ENABLED_PLATFORMS": {
        "group": "domestic", "label": "启用推送的平台",
        "default": "wechat,zhihu,toutiao",
        "help": "逗号分隔；可推送 = 平台在列且本机 agent 在线。",
    },
    "DOMESTIC_EXECUTORS": {
        "group": "domestic", "label": "毕业到扩展的平台",
        "default": "",
        "help": 'JSON 映射，如 {"wechat": "extension"}。列出的平台由浏览器扩展执行，'
                "其余走本机 agent。留空 = 全部走本机 agent。",
    },
    # ── Image hosting & stock photos ──
    "CLOUDINARY_CLOUD_NAME": {
        "group": "media", "label": "Cloudinary cloud name",
        "help": "Public image hosting that the platforms can fetch from.",
    },
    "CLOUDINARY_API_KEY": {
        "group": "media", "label": "Cloudinary API key", "secret": True,
    },
    "CLOUDINARY_API_SECRET": {
        "group": "media", "label": "Cloudinary API secret", "secret": True,
    },
    "UNSPLASH_ACCESS_KEY": {
        "group": "media", "label": "Unsplash access key", "secret": True,
        "help": "Any one of the stock providers enables auto images.",
    },
    "PEXELS_API_KEY": {
        "group": "media", "label": "Pexels API key", "secret": True,
    },
    "PIXABAY_API_KEY": {
        "group": "media", "label": "Pixabay API key", "secret": True,
    },
    # ── Web search ──
    "WEB_SEARCH_PROVIDER": {
        "group": "search", "label": "Search provider",
        "choices": ["auto", "tavily", "brave", "openai", "none"], "default": "auto",
        "help": "auto picks the first provider with a key, else OpenAI native search.",
    },
    "TAVILY_API_KEY": {"group": "search", "label": "Tavily API key", "secret": True},
    "BRAVE_SEARCH_API_KEY": {
        "group": "search", "label": "Brave Search API key", "secret": True,
    },
    # ── Access ──
    "ALLOW_SIGNUP": {
        "group": "access", "label": "Allow signup",
        "choices": ["false", "true"], "default": "false",
        "help": "Whether new people can register after the first admin. Invite by sharing the URL is not supported.",
    },
}

# The keys an admin can save. Server-level configuration (PORT, the Flask
# secret key, session cookie flags) is deliberately not here: it is read
# before the database exists and cannot live in it.
SETTING_KEYS = tuple(SETTINGS)


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Effective value for one setting: saved → environment → registry default.

    ``default`` only answers when the key is unknown to the registry; known
    keys fall back to their registry default, matching what the pre-settings
    ``os.environ.get(key, default)`` calls passed. The database path is read
    through the module attribute at call time so tests that repoint it are
    honoured.
    """
    saved = database.get_setting(key, db_path=database.DB_PATH)
    if saved is not None:
        return saved
    from_env = os.environ.get(key)
    if from_env:
        return from_env
    meta = SETTINGS.get(key)
    if meta is not None and meta.get("default") is not None:
        return meta["default"]
    return default


def source(key: str) -> str:
    """Where the effective value came from: ``db``, ``env`` or ``default``."""
    if database.get_setting(key, db_path=database.DB_PATH) is not None:
        return "db"
    if os.environ.get(key):
        return "env"
    return "default"


def is_set(key: str) -> bool:
    """Whether a secret has a value from any source (never the value itself)."""
    return bool(get(key))
