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

If this list grows large enough to become unwieldy, that's the signal to
switch to Flask-Migrate/Alembic instead.
"""
import sqlalchemy as sa

from app.constants import STANDARD_PO_DESCRIPTIONS

# (table_name, column_name, column_type_sql, default_sql_or_None)
COLUMNS_TO_ENSURE = [
    ("program_outcome", "description", "TEXT", None),
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
