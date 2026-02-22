"""
marketing/social_content.py — Social Content Generator

Generates platform-appropriate post content from a topic string.

Platform tone rules:
  Facebook  → informative, slightly detailed, professional (2-3 sentences)
  Instagram → concise, engaging, emojis allowed (1-2 sentences)
  X         → short, punchy, under 280 characters (1 sentence)

No hashtag floods. Professional business tone throughout.
No external API calls — content is generated via template logic.
"""

import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(VAULT_ROOT))

from marketing.social_mcp import SocialPlatform


# ── Platform tone templates ───────────────────────────────────────────────────

_FACEBOOK_TEMPLATE = (
    "We're excited to share an update on {topic}. "
    "Our team has been working to deliver meaningful value and we believe this "
    "represents an important step forward for our operations. "
    "We look forward to your thoughts and feedback."
)

_INSTAGRAM_TEMPLATE = (
    "Big things happening with {topic}! "
    "Proud of the work our team is doing to drive results. Stay tuned for more updates."
)

_X_TEMPLATE = (
    "Exciting update: {topic}. "
    "Delivering results that matter. More soon."
)

# Character budget for X (hard limit)
_X_CHAR_LIMIT = 280


def generate_social_post(topic: str, platform: SocialPlatform) -> str:
    """
    Generate a platform-appropriate social media post for the given topic.

    Tone:
      FACEBOOK  — informative, 2-3 sentences, professional
      INSTAGRAM — concise, engaging, emojis permitted
      X         — punchy, hard-capped at 280 characters

    Returns a plain-text / markdown string suitable for review.
    No hashtag floods. No placeholders left unresolved.
    """
    topic = topic.strip()

    if platform == SocialPlatform.FACEBOOK:
        return _generate_facebook(topic)
    if platform == SocialPlatform.INSTAGRAM:
        return _generate_instagram(topic)
    if platform == SocialPlatform.X:
        return _generate_x(topic)

    # Fallback for any future platforms — use Facebook style
    return _generate_facebook(topic)


# ── Per-platform generators ───────────────────────────────────────────────────

def _generate_facebook(topic: str) -> str:
    """Informative, professional, 2-3 sentences."""
    post = _FACEBOOK_TEMPLATE.format(topic=_cap(topic))
    return post


def _generate_instagram(topic: str) -> str:
    """Concise, engaging, emojis allowed."""
    post = _INSTAGRAM_TEMPLATE.format(topic=_cap(topic))
    # Single tasteful emoji per post — contextually neutral
    post = post + " \U0001f4c8"  # 📈
    return post


def _generate_x(topic: str) -> str:
    """Punchy, hard limit 280 characters."""
    post = _X_TEMPLATE.format(topic=_cap(topic))
    # Truncate to char limit if topic is extremely long
    if len(post) > _X_CHAR_LIMIT:
        # Trim topic to fit, leaving room for the wrapper text
        overhead = len(_X_TEMPLATE.format(topic="")) + 3  # 3 for "..."
        safe_topic = _cap(topic)[: _X_CHAR_LIMIT - overhead] + "..."
        post = _X_TEMPLATE.format(topic=safe_topic)
    return post


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cap(text: str) -> str:
    """Capitalize the first letter of the topic."""
    return text[:1].upper() + text[1:] if text else text
