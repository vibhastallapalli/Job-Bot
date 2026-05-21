from flask import Flask
from web.web_routes import main_bp
from database.database_db import init_db, close_db
import json
import os


def create_app():
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        app.config["JOB_BOT"] = json.load(f)

    app.secret_key = app.config["JOB_BOT"].get("secret_key", "dev-secret-change-me")

    init_db()
    app.teardown_appcontext(close_db)
    app.register_blueprint(main_bp)

    @app.template_filter("fmtdt")
    def fmtdt(s):
        if not s:
            return "—"
        return str(s)[:16].replace("T", " ")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
