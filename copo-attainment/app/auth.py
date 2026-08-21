"""
Role-enforcement helpers, layered on top of Flask-Login.

  @coordinator_required   - Unit Coordinator only. Used per-route wherever
                             a Coordinator-only view sits in an otherwise
                             shared blueprint (setup_routes.py, plus the
                             roster-management routes inside marks_routes.py).

Redirects to the login page if nobody's logged in at all (reusing
Flask-Login's own "you need to log in first" flow), and 403s if someone's
logged in but isn't allowed to do this particular thing.

The "Coordinator (any course), or a Teacher assigned to *this* course_id"
rule doesn't live here as a decorator - it's identical logic, but applied
once per blueprint via a `before_request` hook (see marks_routes.py and
results_routes.py) rather than pasted onto every individual route.
structure_routes.py does the same thing for its own "Coordinator only,
for the whole blueprint" rule.
"""
from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def coordinator_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not current_user.is_coordinator:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
