import os
import json
import logging
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify

@app.route("/version")
def version():
    return "build-20251201-eastmoney-v1"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

BARK_KEY = os.environ.get("BARK_KEY")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app").rstrip("/")


# ---------------------------
# 工具函数：标准化 ticker
# ---------------------------

def normalize_ticker(raw_ticker: str) -> str:
    """
    把 TradingView 传来的 ticker 统一转换为 6 位 A 股代码：
      - "000001"
      - "SZSE:000001"
      - "SHSE:600000"
      - "000001.SZ"
      - "600000.SH"
    最终返回 "000001" / "600000"
    """
    if not raw_ticker:
        return ""

    s = str(raw_ticker).strip().upper()

    # 去掉前缀（例如 "SZSE:000001", "SHSE:600000"）
    if ":" in s:
        s = s.split(":")[-1]

    # 去掉后缀（例如 "000001.SZ", "600000.SH"）
    for suf in (".SZ", ".SH", ".SS", ".CSI"):
        if s.endswith(suf):
            s = s[: -len(suf)]

    # 只保留数字
    s = "".join(ch for ch in s if ch.isdigit())
    return s


# ---------------------------
# 工具函数：东方财富查股票中文名
# ---------------------------

def fetch_stock_name_from_eastmoney(code: str) -> str:
    """
    使用东方财富 push2 接口，根据 6 位代码获取中文名。
    例：
      000001 -> secid=0.000001  （深市）
      600000 -> secid=1.600000  （沪市）
    """
    if not code or len(code) != 6 or not code.isdigit():
        return ""

    # 约定：6 打头为沪市，0/3 打头为深市
    if code.startswith("6"):
        market = "1"  # 沪
    else:
        market = "0"  # 深

    secid = f"{market}.{code}"

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        # 只要名称字段 f58，其他字段省略
        "fields": "f58",
        # 按常见调用习惯带上这几个参数，减少被风控概率
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fltt": "2",
        "invt": "2",
    }

    try:
        resp = requests.get(url, params=params, timeout=2)
        if resp.status_code != 200:
            app.logger.warning(f"Eastmoney name query failed, code={code}, status={resp.status_code}")
            return ""

        j = resp.json()
        data = j.get("data") or {}
        name = data.get("f58") or ""
        if not name:
            app.logger.warning(f"Eastmoney no name for code={code}, resp={j}")
        return name
    except Exception as e:
        app.logger.exception(f"Eastmoney request error for code={code}: {e}")
        return ""


def build_name_code(raw_ticker: str) -> (str, str):
    """
    综合处理：输入 TradingView 的 ticker，
    返回：
      name_code: "股票名 代码" 或 "代码" 或原始 ticker
      code:      标准 6 位代码（可能为空）
    """
    code = normalize_ticker(raw_ticker)
    name = fetch_stock_name_from_eastmoney(code) if code else ""

    if name and code:
        name_code = f"{name} {code}"
    elif code:
        name_code = code
    else:
        name_code = raw_ticker or "Unknown"

    return name_code, code


def format_price(price_raw):
    """
    价格统一成字符串，保留两位小数；如果为空则返回 ""。
    """
    if price_raw in (None, ""):
        return ""

    try:
        p = float(price_raw)
        return f"{p:.2f}"
    except Exception:
        return str(price_raw)


# ---------------------------
# Bark 发送函数
# ---------------------------

def send_bark(title: str, body: str = "", group: str = "TV") -> dict:
    if not BARK_KEY:
        app.logger.warning("BARK_KEY not set in env")
        return {"ok": False, "error": "BARK_KEY not set"}

    bark_url = f"{BARK_SERVER}/{BARK_KEY}/{quote(title)}/{quote(body or '')}"

    params = {
        "group": group,
    }

    try:
        resp = requests.get(bark_url, params=params, timeout=3)
        return {
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "text": resp.text,
        }
    except Exception as e:
        app.logger.exception("Send Bark error")
        return {"ok": False, "error": str(e)}


# ---------------------------
# 基本路由
# ---------------------------

@app.route("/")
def index():
    return jsonify({"status": "ok", "msg": "TV → Bark Relay Running"})


@app.route("/health")
def health():
    return "ok"


# ---------------------------
# /test：支持 code / price / side
# ---------------------------

@app.route("/test")
def test():
    raw_ticker = request.args.get("code", "000559")
    side = request.args.get("side", "BUY").upper()
    price_raw = request.args.get("price", "")

    name_code, code = build_name_code(raw_ticker)
    price_text = format_price(price_raw)

    # ----- 标题格式（你要求的格式）-----
    if side == "BUY":
        title = f"🟢 𝐁{price_text}" if price_text else f"🟢 𝐁"
    elif side == "SELL":
        title = f"🔴 𝐒{price_text}" if price_text else f"🔴 𝐒"
    else:
        title = f"{name_code} {price_text}"
    # ----- 上面这段逻辑保持不变 -----

    body = "TV→Bark 测试推送"
    result = send_bark(title, body, group="TV-TEST")

    return jsonify({
        "ticker": raw_ticker,
        "code": code,
        "name_code": name_code,
        "side": side,
        "price": price_text,
        "title": title,
        "bark_result": result,
    })


# ---------------------------
# TradingView Webhook 路由
# ---------------------------

@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    """
    TradingView Webhook JSON 示例：

    {
      "ticker": "{{ticker}}",
      "price": "{{close}}",
      "side": "{{strategy.order.action}}",
      "timeframe": "{{interval}}",
      "strategy": "多空终极策略",
      "time": "{{timenow}}"
    }
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    app.logger.info(f"Received webhook: {data}")

    raw_ticker = data.get("ticker", "")
    side = str(data.get("side", "")).upper()
    price_raw = data.get("price", "")
    strategy_name = data.get("strategy", "")
    timeframe = data.get("timeframe", "")
    time_text = data.get("time", "")

    name_code, code = build_name_code(raw_ticker)
    price_text = format_price(price_raw)

    # ----- 标题格式（你要求的格式）-----
    if side == "BUY":
        title = f"🟢 𝐁{price_text}" if price_text else f"🟢 𝐁"
    elif side == "SELL":
        title = f"🔴 𝐒{price_text}" if price_text else f"🔴 𝐒"
    else:
        title = f"{name_code} {price_text}"
    # ----- 上面这段逻辑保持不变 -----

    # 副标题 / 内容
    body_parts = []
    if time_text:
        body_parts.append(f"时间：{time_text}")
    if timeframe:
        body_parts.append(f"周期：{timeframe}")
    if strategy_name:
        body_parts.append(f"策略：{strategy_name}")

    body = " | ".join(body_parts) if body_parts else "TradingView 信号"

    result = send_bark(title, body, group="TV")

    return jsonify({
        "ok": True,
        "ticker": raw_ticker,
        "code": code,
        "name_code": name_code,
        "side": side,
        "price": price_text,
        "title": title,
        "body": body,
        "bark_result": result,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
