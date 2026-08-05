"""美師傅購物網站後端 API (Flask)

前後端分離:前端僅透過此 API 存取資料,不直接連資料庫。
啟動:  python app.py   (預設 http://localhost:5000)
"""
from flask import Flask, jsonify
from flask_cors import CORS

from routes.shop import shop_bp
from routes.admin import admin_bp
from routes.manage import manage_bp
from routes.payment import payment_bp


def create_app():
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    CORS(app)  # 開發環境允許所有來源;上線請改為白名單

    app.register_blueprint(shop_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(manage_bp, url_prefix="/api/admin")
    app.register_blueprint(payment_bp, url_prefix="/api/payment")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "internal server error"}), 500

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5001, debug=True)
