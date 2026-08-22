"""
CO-PO Attainment Calculator
----------------------------
A personal tool that reimplements the calculations from the "attainment
template.xlsx" spreadsheet (AMC Engineering College's CO/PO attainment
workbook) as a small web app, so a faculty member can enter course info,
CO/PO mapping, assessment structure and marks once, and get CO & PO
attainment computed automatically instead of hand-wiring spreadsheet
formulas.

App factory pattern: `create_app()` builds and returns a configured Flask
app. Kept this way (instead of one flat script) so the test suite can spin
up an isolated app + in-memory database per test.
"""
import os

from flask import Flask, redirect, request, render_template, url_for
from flask_login import current_user

from app.extensions import db, login_manager


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # Default config: a SQLite file that lives in instance/ so data
    # persists across restarts (this is our "save and resume" requirement).
    # In production (Render, etc.) both of these are overridden by env
    # vars - see DEPLOYMENT.md. SECRET_KEY especially: the hardcoded
    # fallback below is fine for localhost only, since anyone who can read
    # this source (i.e. anyone) could otherwise forge login sessions on a
    # publicly reachable deployment that didn't set a real one.
    os.makedirs(app.instance_path, exist_ok=True)
    default_db_path = os.path.join(app.instance_path, "copo.db")
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{default_db_path}")
    # Some providers (Heroku-style) still hand out "postgres://" URLs;
    # SQLAlchemy 2.x + psycopg2 require the "postgresql://" scheme.
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-secret-key-change-if-this-ever-leaves-localhost"),
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # The one Admin account is created from these on startup if it
        # doesn't exist yet - see ensure_admin_account() below and
        # DEPLOYMENT.md. No default fallback here (unlike SECRET_KEY):
        # None means "the env var isn't set, so no Admin gets created" -
        # deliberately not defaulted to "", since this app can sit on a
        # public URL and that'd be indistinguishable from "ADMIN_PASSWORD
        # was set to an empty string on purpose" (a blank password is a
        # supported choice everywhere else in this app, so it has to stay
        # a real option here too, not accidentally forbidden).
        ADMIN_NAME=os.environ.get("ADMIN_NAME"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD"),
    )

    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    with app.app_context():
        # Import models here so they're registered on `db` before create_all.
        from app import models  # noqa: F401
        from app.migrations import ensure_columns, ensure_admin_account

        db.create_all()
        ensure_columns(db)
        ensure_admin_account(db, app.config["ADMIN_NAME"], app.config["ADMIN_PASSWORD"])

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))

    from app.routes.setup_routes import setup_bp
    from app.routes.structure_routes import structure_bp
    from app.routes.marks_routes import marks_bp
    from app.routes.results_routes import results_bp
    from app.routes.import_routes import import_bp
    from app.routes.auth_routes import auth_bp
    from app.routes.main_routes import main_bp
    from app.routes.scrape_routes import scrape_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(structure_bp)
    app.register_blueprint(marks_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(scrape_bp)

    # Every request needs to be logged in, except the login page itself
    # and static assets - this is the safety net that catches any route
    # that forgot its own auth decorator. There's deliberately no
    # "nobody has an account yet" bootstrap path here anymore - the one
    # Admin account is created out-of-band from ADMIN_NAME/ADMIN_PASSWORD
    # (see ensure_admin_account() above), not through a public web form.
    @app.before_request
    def _require_login():
        if request.endpoint is None or request.endpoint == "static":
            return None
        if not current_user.is_authenticated and request.endpoint != "auth.login":
            return redirect(url_for("auth.login", next=request.path))
        return None

    @app.errorhandler(403)
    def _forbidden(error):
        return render_template("errors/403.html"), 403

    return app
