"""美師傅購物網站後端 API (Flask)

前後端分離:前端僅透過此 API 存取資料,不直接連資料庫。
啟動:  python app.py   (預設 http://localhost:5000)
"""
import os

from flask import Flask, jsonify
from flask_cors import CORS

import config
from image_storage import serve_upload
from routes.shop import shop_bp
from routes.admin import admin_bp
from routes.logistics import logistics_bp
from routes.manage import manage_bp
from routes.payment import payment_bp


def create_app():
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    if config.CORS_ORIGINS:
        CORS(app, resources={r"/api/*": {"origins": config.CORS_ORIGINS}})

    app.register_blueprint(shop_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(manage_bp, url_prefix="/api/admin")
    app.register_blueprint(payment_bp, url_prefix="/api/payment")
    app.register_blueprint(logistics_bp, url_prefix="/api/logistics")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/assets/uploads/<path:filename>")
    def uploaded_asset(filename):
        return serve_upload(filename)

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "internal server error"}), 500

    return app


if __name__ == "__main__":
    create_app().run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "").lower() in ("1", "true"),
    )
