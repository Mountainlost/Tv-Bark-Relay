import os
import urllib.parse

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# 从环境变量读取 Bark 配置
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")

# ====== A 股代码 -> 中文名称 映射表 ======
STOCK_NAMES = {
    "000559": "万向钱潮",
    "600519": "贵州茅台",
    "000858": "五粮液",
    "601318": "中国平安",
    "300750": "宁德时代",
    # 继续追加你需要的股票
}


def build_bark_message(data: dict):
    """根据 TradingView 传来的 JSON，构造 Bark 标题和正文"""

    ticker = str(data.get("ticker", "") or "")
    price = data.get("price", None)
    side = str(data.get("side", "") or "").upper()

    # 名称映射
    name = STOCK_NAMES.get(ticker, "")
    if name:
        name_code = f"{name} {ticker}"
    else:
        name_code = ticker or "未知标的"

    # 价格格式化
    try:
        price_val = float(price)
        price_text = f"{price_val:.2f}"
    except Exception:
        price_text = str(price) if price else ""

    # ===== 方案 A —— 标题紧凑格式 =====
    # 🟢 𝐁【万向钱潮 000559】11.82
    if side == "BUY":
        title = f"🟢 𝐁【{name_code}】{price_text}"
    elif side == "SELL":
        title = f"🔴 𝐒【{name_code}】{price_text}"
    else:
        title = f"{name_code} {price_text}"

    # ===== 正文不显示 =====
    body = ""

    return title, body


@app.route("/", methods=["GET"])
def health():
    return "TV -> Bark relay is running."


@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    """接收 TradingView Webhook"""
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "invalid json", "detail": str(e)}), 400

    if not BARK_KEY:
        return jsonify({"ok": False, "error": "BARK_KEY not set"}), 500

    title, body = build_bark_message(data)

    # URL 编码
    title_enc = urllib.parse.quote(title)
    body_enc = urllib.parse.quote(body)

    bark_url = f"{BARK_SERVER}/{BARK_KEY}/{title_enc}/{body_enc}"

    try:
        resp = requests.get(bark_url, timeout=5)
        return jsonify({
            "ok": True,
            "bark_status_code": resp.status_code,
            "bark_response": resp.text,
            "title": title,
            "body": body,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": "bark request failed", "detail": str(e)}), 500


@app.route("/test", methods=["GET"])
def test():
    """发送示例通知，方便自测"""
    if not BARK_KEY:
        return "BARK_KEY not set", 500

    sample = {
        "ticker": "000559",
        "price": 11.82,
        "side": "BUY",
    }

    title, body = build_bark_message(sample)

    title_enc = urllib.parse.quote(title)
    body_enc = urllib.parse.quote(body)

    bark_url = f"{BARK_SERVER}/{BARK_KEY}/{title_enc}/{body_enc}"

    try:
        requests.get(bark_url, timeout=5)
    except Exception:
        pass

    return f"Test notification sent: {title}"
