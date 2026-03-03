"""AI helpers for newsletter content generation.

Uses Anthropic SDK directly, same pattern as app/research/triage.py.
"""
import logging

from flask import current_app

logger = logging.getLogger(__name__)


def generate_article_summary(content_text, title, publication):
    """Generate a condensed newsletter-appropriate summary of an article.

    Parameters
    ----------
    content_text : str
        Full article content.
    title : str
        Article title.
    publication : Publication
        The publication model instance for context.

    Returns
    -------
    str
        A 2-3 paragraph summary suitable for a newsletter.
    """
    import anthropic

    api_key = (current_app.config.get('ANTHROPIC_API_KEY') or '').strip().split()[0] if current_app.config.get('ANTHROPIC_API_KEY') else None
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured")
        return ''

    system_prompt = (
        "You are a newsletter editor for a trade publication. "
        "Write a concise, engaging summary of the following article suitable for inclusion in a newsletter. "
        "The summary should be 2-3 short paragraphs, capturing the key points and why they matter to the reader. "
        "Write in a professional, journalistic tone. Do not use markdown formatting — output plain text only."
    )

    if publication.industry_description:
        system_prompt += f"\n\nPublication industry: {publication.industry_description}"
    if publication.reader_personas:
        system_prompt += f"\n\nTarget readers: {publication.reader_personas}"

    user_message = f"Article title: {title}\n\nArticle content:\n{content_text[:8000]}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_message}],
        )

        for block in response.content:
            if hasattr(block, 'text') and block.text:
                return block.text.strip()

        return ''

    except Exception as e:
        logger.error(f"Failed to generate article summary: {e}", exc_info=True)
        return ''


def generate_newsletter_intro(articles, publication):
    """Generate an intro paragraph that ties together the newsletter's articles.

    Parameters
    ----------
    articles : list[dict]
        List of dicts with 'title' and 'teaser' keys.
    publication : Publication
        The publication model instance for context.

    Returns
    -------
    str
        An intro paragraph for the newsletter.
    """
    import anthropic

    api_key = (current_app.config.get('ANTHROPIC_API_KEY') or '').strip().split()[0] if current_app.config.get('ANTHROPIC_API_KEY') else None
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured")
        return ''

    system_prompt = (
        "You are the friendly editor of a trade publication newsletter. "
        "Write a warm, fun, and conversational greeting to open this newsletter issue. "
        "Start with a short friendly hello and an engaging hook — mention a standout story or timely industry moment. "
        "Then include a 'Today's newsletter:' section that lists each article with a relevant emoji and a short "
        "(5-8 word) teaser for each. "
        "\n\nExample format:\n"
        "Good morning. Firefly scrubbed its Alpha 7 launch expected to lift off last night due to high upper level winds. "
        "A new date for the highly anticipated return to flight mission is still TBD.\n\n"
        "Today's newsletter:\n"
        "☀️ Starpath's new solar panels\n"
        "🛰️ Austria's first military sat\n"
        "🌕 NASA delays astronaut Moon landing\n"
        "💫 In other news\n\n"
        "Follow this format closely. The greeting should feel personal and enthusiastic — like a knowledgeable friend "
        "catching the reader up over coffee. Keep the greeting portion to 2-3 sentences. "
        "Output plain text only, no markdown formatting."
    )

    if publication.industry_description:
        system_prompt += f"\n\nPublication industry: {publication.industry_description}"
    if publication.reader_personas:
        system_prompt += f"\n\nTarget readers: {publication.reader_personas}"

    article_list = "\n".join(
        f"- {a['title']}" + (f": {a['teaser'][:200]}" if a.get('teaser') else "")
        for a in articles
    )
    user_message = f"Articles in this newsletter issue:\n{article_list}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_message}],
        )

        for block in response.content:
            if hasattr(block, 'text') and block.text:
                return block.text.strip()

        return ''

    except Exception as e:
        logger.error(f"Failed to generate newsletter intro: {e}", exc_info=True)
        return ''
