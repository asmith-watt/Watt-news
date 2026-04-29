from flask import request, session


SESSION_KEY = 'current_publication_id'


def resolve_publication_id(publications):
    """Resolve the active publication ID for the current request.

    Precedence:
    1. ``?publication_id=`` in the query string (validated against ``publications``)
       — also persisted to the session so subsequent navigations remember it.
    2. ``session['current_publication_id']`` if still in the user's accessible set.
    3. The first publication in ``publications`` (also persisted).

    Returns ``None`` if the user has no accessible publications.
    """
    allowed_ids = {p.id for p in publications}

    arg_id = request.args.get('publication_id', type=int)
    if arg_id and arg_id in allowed_ids:
        session[SESSION_KEY] = arg_id
        return arg_id

    session_id = session.get(SESSION_KEY)
    if session_id and session_id in allowed_ids:
        return session_id

    if publications:
        default_id = publications[0].id
        session[SESSION_KEY] = default_id
        return default_id

    session.pop(SESSION_KEY, None)
    return None