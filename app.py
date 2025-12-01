import os
import urllib.parse
import re
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===== Bark 配置 =====
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")

# ===== A 股代码 -> 中文名本地缓存（常用可以先写几只）=====
STOCK_NAMES: dict[str, str] = {
    "000559": "万向钱潮",
    "600519": "贵州茅台",
    "000858": "五粮液",
    "601318": "中国平安",
    "300750": "宁德时代",
}

A_SHARE_CODE_RE = re.compile(r"^\d{6}$")

# 尽量减少延迟，复用 TCP 连接
session = requests.Session()


# ===== 判断股票交易所（主板/创业板/科创板）=====
def guess_market_prefix(ticker: str) -> str:
    """推断股票属于 sh 或 sz（支持主板 + 创业板 + 科创）"""
    if not A_SHARE_CODE_RE.match(ticker):
        return ""

    if ticker.startswith("6"):
        return "sh"  # 上海（含科创板）
    if ticker.startswith("0") or ticker.startswith("3"):
        return "sz"  # 深圳（主板 + 创业板）
    return ""


# ===== 从腾讯接口获取中文名称 =====
def fetch_name_from_tencent(ticker: str) -> str:
    """调用腾讯行情接口获取股票中文名"""
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


# ===== 获取中文名（缓存 + 自动查询）=====
def get_stock_name(ticker: str) -> str:
    if not ticker:
        return ""
    if ticker in STOCK_NAMES:
        return STOCK_NAMES[ticker]

    if not A_SHARE_CODE_RE.match(ticker):
        return ""

    # 腾讯接口查询
    name = fetch_name_from_tencent(ticker)
    if name:
        STOCK_NAMES[ticker] = name  # 写入缓存
        return name

    return ""


# ===== 构建 Bark 推送 =====
def build_bark_message(data: dict):
    """
    预期 TradingView JSON 示例：
    {
      "ticker": "000559",
      "price": 11.82,
      "side": "BUY"
    }
    """

    ticker = str(data.get("ticker", "") or "")
    price = data.get("price")
    side = str(data.get("side", "") or "").upper()

    # ----- 格式化价格 -----
    try:
        price_val = float(price)
        price_text = f"{price_val:.2f}"
    except:
        price_text = str(price) if price is not None else ""

    # ----- 自动中文名 -----
    name = get_stock_name(ticker)
    if name:
        name_code = f"{name} {ticker}"
    else:
        name_code = ticker or "未知标的"

    # ----- 标题格式（你要求的格式）-----
    if side == "BUY":
        title = f"🟢 𝐁【{name_code}】{price_text}" if price_text else f"🟢 𝐁【{name_code}】"
    elif side == "SELL":
        title = f"🔴 𝐒【{name_code}】{price_text}" if price_text else f"🔴 𝐒【{name_code}】"
    else:
        title = f"{name_code} {price_text}"

    # 正文不显示（你要求）
    body = ""

    return title, body


# ===== 健康检查 =====
@app.route("/", methods=["GET"])
def health():
    return "TV -> Bark relay is running."


# ===== TradingView Webhook 接口 =====
@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
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
        return jsonify({
            "ok": True,
            "bark_status_code": resp.status_code,
            "bark_response": resp.text,
            "title": title
        })
    except Exception as e:
        return jsonify({"ok": False, "error": "bark request failed", "detail": str(e)}), 500


# ===== 测试接口 =====
@app.route("/test", methods=["GET"])
def test():
    if not BARK_KEY:
        return "BARK_KEY not set", 500

    sample = {"ticker": "000559", "price": 11.82, "side": "BUY"}

    title, body = build_bark_message(sample)

    url = f"{BARK_SERVER}/{BARK_KEY}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"

    try:
        session.get(url, timeout=3)
    except:
        pass

    return f"Test notification sent: {title}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
