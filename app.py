import os
import urllib.parse

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# 从环境变量里取你的 Bark Key
# 下面这一行的默认值可以临时写上你的 Key，或者留空以后到 Railway 里配置
BARK_KEY = os.environ.get("BARK_KEY", "NTWAydgg2zQsHNpmm9uGBV")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")


@app.route("/", methods=["GET"])
def health():
    return "TV -> Bark relay is running."


@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    """
    TradingView Webhook 统一打到这里。
    Body 是我们在 Pine 脚本里 alert() 发出来的 JSON 字符串。
    """
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "invalid json", "detail": str(e)}), 400

    # 从 JSON 里取关键信息（Pine 里会发这些字段）
    ticker = data.get("ticker", "UNKNOWN")
    price = data.get("price", "N/A")
    side = data.get("side", "UNKNOWN")  # BUY / SELL
    strategy_name = data.get("strategy", "多空终极策略")
    timeframe = data.get("timeframe", "")
    time_str = data.get("time", "")

    title = f"{strategy_name} - {side} 信号"

    lines = [
        f"标的: {ticker}",
        f"方向: {side}",
        f"价格: {price}",
    ]
    if timeframe:
        lines.append(f"周期: {timeframe}")
    if time_str:
        lines.append(f"时间: {time_str}")
    body = "\n".join(lines)

    # URL 编码
    title_enc = urllib.parse.quote(title)
    body_enc = urllib.parse.quote(body)

    bark_key = BARK_KEY
    if not bark_key:
        return jsonify({"ok": False, "error": "BARK_KEY not set"}), 500

    bark_url = f"{BARK_SERVER}/{bark_key}/{title_enc}/{body_enc}"

    try:
        resp = requests.get(bark_url, timeout=5)
        return jsonify(
            {
                "ok": True,
                "bark_status_code": resp.status_code,
                "bark_response": resp.text,
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "bark request failed", "detail": str(e)}), 500


if __name__ == "__main__":
    # 本地调试用，Railway 上会用 gunicorn 启动
    app.run(host="0.0.0.0", port=8000)
