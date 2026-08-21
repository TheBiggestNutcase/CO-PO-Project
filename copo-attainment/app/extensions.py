"""Shared Flask extension instances, kept separate from app/__init__.py to
avoid circular imports between the app factory and the models module."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
