import os
import urllib.parse
import re
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===== Bark 配置 =====
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")

# ===== A 股代码 -> 中文名本地缓存（常用的可以先写这里）=====
STOCK_NAMES: dict[str, str] = {
    "000559": "万向钱潮",
    "600519": "贵州茅台",
    "000858": "五粮液",
    "601318": "中国平安",
    "300750": "宁德时代",
    # 以后你常用的票可以往这里追加几只
}

A_SHARE_CODE_RE = re.compile(r"^\d{6}$")

# 为了减少 Bark 请求延迟，用一个全局 Session 复用连接
session = requests.Session()


def guess_market_prefix(ticker: str) -> str:
    """根据 6 位代码猜测交易所前缀（主板 + 创业板 + 科创板）"""
    if not A_SHARE_CODE_RE.match(ticker):
        return ""
    if ticker.startswith("6"):      # 沪市（主板 + 科创）
        return "sh"
    if ticker.startswith("0") or ticker.startswith("3"):  # 深市（主板 + 创业板）
        return "sz"
    return ""


def fetch_name_from_tencent(ticker: str) -> str:
    """
    从腾讯行情接口获取中文名：
    例：https://qt.gtimg.cn/q=sh600519
    返回格式：v_sh600519="1~贵州茅台~600519~..."
    """
    prefix = guess_market_prefix(ticker)
    if not prefix:
        return ""

    url = f"https://qt.gtimg.cn/q={prefix}{ticker}"
    try:
        resp = session.get(url, timeout=2)
        if resp.status_code != 200:
            return ""
        text = resp.text
        parts = text.split("~")
        if len(parts) > 1:
            name = parts[1].strip()
            return name or ""
        return ""
    except Exception:
        return ""


def get_stock_name(ticker: str) -> str:
    """优先用缓存，没有就调用腾讯接口获取中文名并写入缓存。"""
    if not ticker:
        return ""
    if ticker in STOCK_NAMES:
        return STOCK_NAMES[ticker]
    if not A_SHARE_CODE_RE.match(ticker):
        return ""

    name = fetch_name_from_tencent(ticker)
    if name:
        STOCK_NAMES[ticker] = name
        return name
    return ""


def build_bark_message(data: dict):
    """
    根据 TradingView 传来的 JSON，构造 Bark 标题和正文。

    预期 TV 传入字段示例：
    {
      "ticker": "000559",
      "price": 11.82,
      "side": "BUY"
    }
    其他字段（strategy / timeframe / time）你可以随意加，这里不强依赖。
    """

    ticker = str(data.get("ticker", "") or "")
    price = data.get("price", None)
    side = str(data.get("side", "") or "").upper()

    # 价格格式化
    try:
        price_val = float(price)
        price_text = f"{price_val:.2f}"
    except (TypeError, ValueError):
        price_text = str(price) if price is not None else ""

    # 自动获取中文名
    name = get_stock_name(ticker)
    if name:
        name_code = f"{name} {ticker}"
    else:
        name_code = ticker or "未知标的"

    # ===== 标题（方案 A）：🟢 𝐁【万向钱潮 000559】11.82 / 🔴 𝐒【万向钱潮 000559】11.82 =====
    if side == "BUY":
        title = f"🟢 𝐁{price_text}" if price_text else f"🟢 𝐁"
    elif side == "SELL":
        title = f"🔴 𝐒{price_text}" if price_text else f"🔴 𝐒"
    else:
        title = f"{name_code} {price_text}" if price_text else name_code

    # 正文你说可以不显示，这里给一个很短的占位
    body = ""

    return title, body


@app.route("/", methods=["GET"])
def health():
    return "TV -> Bark relay is running."


@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    """TradingView Webhook 入口"""
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "invalid json", "detail": str(e)}), 400

    if not BARK_KEY:
        return jsonify({"ok": False, "error": "BARK_KEY not set"}), 500

    title, body = build_bark_message(data)

    title_enc = urllib.parse.quote(title)
    body_enc = urllib.parse.quote(body)
    bark_url = f"{BARK_SERVER}/{BARK_KEY}/{title_enc}/{body_enc}"

    try:
        resp = session.get(bark_url, timeout=3)
        return jsonify(
            {
                "ok": True,
                "bark_status_code": resp.status_code,
                "bark_response": resp.text,
                "title": title,
                "body": body,
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "bark request failed", "detail": str(e)}), 500


@app.route("/test", methods=["GET"])
def test():
    """测试接口，浏览器打开就会给自己发一条测试通知。"""
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
        session.get(bark_url, timeout=3)
    except Exception:
        pass

    return f"Test notification sent: {title}"


if __name__ == "__main__":
    # 本地调试用；Railway 上不会执行这一段
    app.run(host="0.0.0.0", port=8000, debug=True)
