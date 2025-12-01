import os
import urllib.parse
import re

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ========= Bark 配置 =========
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")

# ========= A 股代码 -> 中文名 缓存（可选预填一些常用的）=========
STOCK_NAMES = {
    "000559": "万向钱潮",
    "600519": "贵州茅台",
    "000858": "五粮液",
    "601318": "中国平安",
    "300750": "宁德时代",
    # 这里可以按需继续加，但不加也没关系，会自动从腾讯接口查
}

A_SHARE_CODE_RE = re.compile(r"\d{6}")
session = requests.Session()


# ========= 工具函数：从任意 ticker 中提取 6 位代码 =========
def extract_code(ticker: str) -> str:
    """
    支持:
    - "603626"
    - "603626.SH"
    - "SH603626"
    - "sh603626"
    都会被识别成 "603626"
    """
    if not ticker:
        return ""
    m = A_SHARE_CODE_RE.search(str(ticker))
    return m.group(0) if m else ""


# ========= 推断上交所 / 深交所 =========
def guess_market_prefix(code: str) -> str:
    """
    简单规则：
    - 6 开头 -> 上交所 sh （含科创板）
    - 0 / 3 开头 -> 深交所 sz （主板 + 创业板）
    """
    if not code or len(code) != 6:
        return ""
    if code.startswith("6"):
        return "sh"
    if code.startswith("0") or code.startswith("3"):
        return "sz"
    return ""


# ========= 调腾讯接口取中文名 =========
def fetch_name_from_tencent(code: str) -> str:
    prefix = guess_market_prefix(code)
    if not prefix:
        return ""

    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        resp = session.get(url, timeout=2)
        if resp.status_code != 200:
            return ""
        text = resp.text  # 形如：v_sh603626="1~科森科技~603626~..."
        parts = text.split("~")
        if len(parts) > 1:
            name = parts[1].strip()
            return name or ""
        return ""
    except Exception:
        return ""


# ========= 对外统一获取中文名 =========
def get_stock_name(ticker_raw: str) -> str:
    code = extract_code(ticker_raw)
    if not code:
        return ""

    # 先看缓存
    if code in STOCK_NAMES:
        return STOCK_NAMES[code]

    # 缓存里没有，就从腾讯接口查
    name = fetch_name_from_tencent(code)
    if name:
        STOCK_NAMES[code] = name  # 写入缓存，后面会更快
        return name

    return ""  # 查不到就没名字，只显示代码


# ========= 构建 Bark 标题 / 正文 =========
def build_bark_message(data: dict):
    """
    预期 TradingView JSON：
    {
      "ticker": "603626",
      "price": 18.55,
      "side": "BUY",
      ...
    }
    """

    ticker_raw = str(data.get("ticker", "") or "")
    code = extract_code(ticker_raw)

    price = data.get("price", None)
    side = str(data.get("side", "") or "").upper()

    # 价格格式化
    try:
        price_val = float(price)
        price_text = f"{price_val:.2f}"
    except Exception:
        price_text = str(price) if price is not None else ""

    # 自动获取中文名
    name = get_stock_name(ticker_raw)
    if name and code:
        name_code = f"{name} {code}"
    elif code:
        name_code = code
    else:
        name_code = ticker_raw or "未知标的"

   # ----- 标题格式（你要求的格式）-----
    if side == "BUY":
        title = f"🟢 𝐁【{name_code}】{price_text}" if price_text else f"🟢 𝐁【{name_code}】"
    elif side == "SELL":
        title = f"🔴 𝐒【{name_code}】{price_text}" if price_text else f"🔴 𝐒【{name_code}】"
    else:
        title = f"{name_code} {price_text}"

    # 你要求正文不显示内容
    body = ""

    return title, body


# ========= 基本健康检查 =========
@app.route("/", methods=["GET"])
def health():
    return "TV -> Bark relay is running."


# ========= TradingView Webhook 主入口 =========
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
        return jsonify(
            {
                "ok": True,
                "bark_status_code": resp.status_code,
                "bark_response": resp.text,
                "title": title,
            }
        )
    except Exception as e:
        return jsonify(
            {"ok": False, "error": "bark request failed", "detail": str(e)}
        ), 500


# ========= 固定样例测试（万向钱潮） =========
@app.route("/test", methods=["GET"])
def test():
    if not BARK_KEY:
        return "BARK_KEY not set", 500

    sample = {
        "ticker": "000559",
        "price": 11.82,
        "side": "BUY",
    }
    title, body = build_bark_message(sample)

    url = f"{BARK_SERVER}/{BARK_KEY}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
    try:
        session.get(url, timeout=3)
    except Exception:
        pass

    return f"Test notification sent: {title}"


# ========= 通用测试接口：可传任意代码，例如 603626 =========
@app.route("/test_custom", methods=["GET"])
def test_custom():
    """
    例子：
    https://web-production-67710.up.railway.app/test_custom?ticker=603626&price=18.55&side=BUY
    """
    if not BARK_KEY:
        return "BARK_KEY not set", 500

    ticker = request.args.get("ticker", "000559")
    price = request.args.get("price", "11.82")
    side = request.args.get("side", "BUY")

    sample = {
        "ticker": ticker,
        "price": price,
        "side": side,
    }

    title, body = build_bark_message(sample)
    url = f"{BARK_SERVER}/{BARK_KEY}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"

    try:
        session.get(url, timeout=3)
    except Exception:
        pass

    return f"Custom test notification sent: {title}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
