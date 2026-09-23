"""Apply an editor's free-text revision instructions to an article version.

The editor types what they want changed ("tighten the lede", "drop the second
quote", "make the headline figure 4.2%, not 4.1%") and Claude returns the full
set of revised fields plus a short summary of what it changed. Nothing is
written to the version here; the result is held on a VersionRevision as a
preview until the editor applies it.
"""
import json
import logging

from flask import current_app

logger = logging.getLogger(__name__)

REVISABLE_FIELDS = ('deck', 'teaser', 'summary', 'content', 'notes')
MAX_INSTRUCTIONS_CHARS = 4000

_FIELD_DESCRIPTIONS = {
    'deck': 'Deck: the subheadline shown under the title.',
    'teaser': 'Teaser: a short promotional blurb.',
    'summary': 'Summary: a brief summary of the article.',
    'content': 'Content: the article body, in markdown.',
    'notes': "Notes: the AI's internal notes about the article. Not published.",
}

_OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        **{field: {'type': 'string'} for field in REVISABLE_FIELDS},
        'change_summary': {'type': 'string'},
    },
    'required': [*REVISABLE_FIELDS, 'change_summary'],
    'additionalProperties': False,
}


def snapshot_version(version):
    """The revisable fields of a version as a plain dict."""
    return {field: getattr(version, field) or '' for field in REVISABLE_FIELDS}


def _build_system_prompt(publication, style_guide):
    parts = [
        f"You are a senior editor at {publication.name}. An editor on staff has asked "
        "for changes to an article that has already been through fact-checking and "
        "an editorial audit.",
        "",
        "Make the changes the editor asks for, and only those. Leave everything else "
        "exactly as written, word for word. Do not polish or restyle untouched "
        "passages, and do not add facts, figures, quotes or sources that are not already "
        "in the article unless the editor supplies them in the instructions. If an "
        "instruction can't be carried out without inventing information, make the rest "
        "of the changes and say so in change_summary.",
        "",
        "Every field is returned in full, including the ones you left alone. Keep the "
        "body in markdown with the same heading and link conventions it already uses. "
        "An empty field stays empty unless the editor asks you to write it.",
        "",
        "change_summary: two to five short plain-text bullet lines (each starting with "
        "\"- \") telling the editor what you changed, and anything you could not do.",
        "",
        "Fields:",
        *[f"- {desc}" for desc in _FIELD_DESCRIPTIONS.values()],
    ]
    if style_guide:
        parts += [
            "",
            "House style guide (any new or rewritten sentences should follow it):",
            style_guide,
        ]
    return '\n'.join(parts)


def _build_user_message(title, fields, instructions):
    article = {'title': title or '', **fields}
    return (
        "## Article\n\n"
        f"{json.dumps(article, indent=2, ensure_ascii=False)}\n\n"
        "## Editor's revision instructions\n\n"
        f"{instructions}"
    )


def generate_revision(publication, title, fields, instructions, style_guide=None):
    """Ask Claude to revise `fields` per `instructions`.

    Returns (revised_fields, change_summary, model). Raises on API failure,
    refusal, or a truncated response so the caller can record the error.
    """
    import anthropic

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY is not configured')

    model = current_app.config.get('REVISION_MODEL', 'claude-opus-5')
    client = anthropic.Anthropic(api_key=api_key)

    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=_build_system_prompt(publication, style_guide),
        messages=[{'role': 'user', 'content': _build_user_message(title, fields, instructions)}],
        output_config={'format': {'type': 'json_schema', 'schema': _OUTPUT_SCHEMA}},
        # On a safety decline, re-run on Anthropic's recommended fallback model
        betas=['server-side-fallback-2026-07-01'],
        extra_body={'fallbacks': 'default'},
    )

    if response.stop_reason == 'refusal':
        raise RuntimeError('Claude declined to make this revision')
    if response.stop_reason == 'max_tokens':
        raise RuntimeError('The revised article was too long and got cut off')

    text = next((b.text for b in response.content if b.type == 'text'), '')
    data = json.loads(text)

    revised = {field: data.get(field) or '' for field in REVISABLE_FIELDS}
    return revised, (data.get('change_summary') or '').strip(), response.model
