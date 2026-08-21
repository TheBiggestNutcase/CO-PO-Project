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

from flask import Flask

from app.extensions import db


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

    with app.app_context():
        # Import models here so they're registered on `db` before create_all.
        from app import models  # noqa: F401
        from app.migrations import ensure_columns

        db.create_all()
        ensure_columns(db)

    from app.routes.setup_routes import setup_bp
    from app.routes.structure_routes import structure_bp
    from app.routes.marks_routes import marks_bp
    from app.routes.results_routes import results_bp
    from app.routes.import_routes import import_bp
    from app.routes.main_routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(structure_bp)
    app.register_blueprint(marks_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(import_bp)

    return app
