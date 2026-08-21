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
    os.makedirs(app.instance_path, exist_ok=True)
    default_db_path = os.path.join(app.instance_path, "copo.db")
    app.config.from_mapping(
        SECRET_KEY="dev-only-secret-key-change-if-this-ever-leaves-localhost",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{default_db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    with app.app_context():
        # Import models here so they're registered on `db` before create_all.
        from app import models  # noqa: F401
        from app.migrations import ensure_columns

        db.create_all()
        ensure_columns(db)

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

    app.register_blueprint(main_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(structure_bp)
    app.register_blueprint(marks_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(auth_bp)

    # Two things every request needs, ahead of whatever route-specific or
    # blueprint-level access check applies (@coordinator_required, or the
    # coordinator-or-assigned-teacher before_request hooks in
    # marks_routes.py/results_routes.py):
    # (1) if nobody has an account yet, force the one-time coordinator
    #     setup screen instead of letting an unset-up app be used at all;
    # (2) otherwise, require login for everything except the login/setup
    #     pages themselves and static assets. This is the safety net that
    #     catches any route that forgot its own auth decorator.
    @app.before_request
    def _require_login():
        from app.models import User

        if request.endpoint is None or request.endpoint == "static":
            return None
        if User.query.count() == 0:
            if request.endpoint != "auth.setup_coordinator":
                return redirect(url_for("auth.setup_coordinator"))
            return None
        if not current_user.is_authenticated and request.endpoint not in ("auth.login", "auth.setup_coordinator"):
            return redirect(url_for("auth.login", next=request.path))
        return None

    @app.errorhandler(403)
    def _forbidden(error):
        return render_template("errors/403.html"), 403

    return app
