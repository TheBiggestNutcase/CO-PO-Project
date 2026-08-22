"""
Lightweight schema upgrades for existing databases.

There's no Alembic/Flask-Migrate set up for this project (it's a small
personal tool, not worth the ceremony yet) - `db.create_all()` only
creates *missing tables*, it never adds a column to a table that already
exists. Without something like this, adding a field to a model (e.g. the
`description` column on ProgramOutcome) would work fine for someone
starting fresh, but crash with "no such column" for anyone who already
has data in instance/copo.db from before that change.

`ensure_columns()` checks each (table, column) pair against what's
actually in the database and ALTERs the table to add anything missing,
so existing databases pick up new columns automatically the next time the
app starts - no manual migration step, no data loss.

`ensure_admin_account()` is a different kind of startup-time invariant,
not a schema change: it makes sure exactly one Admin account exists,
creating it from ADMIN_NAME/ADMIN_PASSWORD if one doesn't yet - see
app/__init__.py for where it's called and DEPLOYMENT.md for the env vars.
It lives here rather than in auth_routes.py because, like ensure_columns,
it's "something that runs once at startup to bring an existing database
in line," not a request-handling route.

If this list grows large enough to become unwieldy, that's the signal to
switch to Flask-Migrate/Alembic instead.
"""
import sqlalchemy as sa

from app.constants import STANDARD_PO_DESCRIPTIONS

# (table_name, column_name, column_type_sql, default_sql_or_None)
COLUMNS_TO_ENSURE = [
    ("program_outcome", "description", "TEXT", None),
    ("course", "section", "TEXT", "''"),
    ("course", "coordinator_id", "INTEGER", None),
    ("student", "section", "TEXT", "''"),
    ("assessment_item", "main_question", "INTEGER", None),
]


def ensure_columns(db):
    inspector = sa.inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    newly_added = set()

    for table_name, column_name, column_type_sql, default_sql in COLUMNS_TO_ENSURE:
        if table_name not in existing_tables:
            continue  # table doesn't exist yet - db.create_all() will make it with the column already present
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue
        default_clause = f" DEFAULT {default_sql}" if default_sql is not None else ""
        with db.engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}{default_clause}"))
        newly_added.add((table_name, column_name))

    # One-time backfill: if program_outcome.description just appeared on an
    # existing database, fill in the standard wording for any row whose code
    # matches a standard PO (PO1..PO12) - those rows were almost certainly
    # created by the old "Add PO1-PO12" button before it set descriptions,
    # so this gives existing users the same result a fresh install would.
    if ("program_outcome", "description") in newly_added:
        with db.engine.begin() as conn:
            for code, description in STANDARD_PO_DESCRIPTIONS.items():
                conn.execute(
                    sa.text(
                        "UPDATE program_outcome SET description = :description "
                        "WHERE code = :code AND description IS NULL"
                    ),
                    {"description": description, "code": code},
                )


def ensure_admin_account(db, admin_name, admin_password):
    """Creates the one Admin account from ADMIN_NAME/ADMIN_PASSWORD if
    both are set and no Admin account exists yet. Does nothing otherwise
    - in particular, does nothing if an Admin already exists (so this is
    safe to call on every startup) and does nothing if the env vars
    aren't set (so a deploy that forgot to set them just has no Admin
    yet, rather than crashing or falling back to some default credentials
    that would be a real hole on a public URL).

    admin_password is checked with `is None`, not a truthiness check:
    None means "ADMIN_PASSWORD isn't set at all" (see app/__init__.py),
    but "" is a deliberately supported blank password (same as everywhere
    else in this app), so it must still go through and create the
    account rather than being treated as "unset."

    Deliberately not exposed through any route/form - see the ROLE_ADMIN
    comment in app/models.py for why."""
    from app.models import User, ROLE_ADMIN  # local import: avoids a circular import with app/extensions.py

    if not admin_name or admin_password is None:
        return
    if User.query.filter_by(role=ROLE_ADMIN).first() is not None:
        return
    admin = User(username=admin_name, display_name=admin_name, role=ROLE_ADMIN)
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.commit()
