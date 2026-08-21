"""Entry point: `python run.py` starts the app on http://localhost:8000
with data persisted in instance/copo.db (created automatically)."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
