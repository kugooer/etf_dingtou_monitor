#!/usr/bin/env python3
"""
多ETF 定投监测脚本
通过环境变量 ETF_CODES 指定需要监控的 ETF 代码列表，默认监控 512890。
支持对多个 ETF 的价格、MA250、偏离度计算及通知推送。
"""

import sys
import os
import json
import datetime
import subprocess
import urllib.request
import urllib.parse

def get_baostock_code(code: str) -> str:
    if code.startswith("5"):
        return f"sh.{code}"
    else:
        return f"sz.{code}"

def get_eastmoney_secid(code: str) -> str:
    """根据代码规则判断secid: 5开头=上海(1), 其他=深圳(0)"""
    if code.startswith("5"):
        return f"1.{code}"
    else:
        return f"0.{code}"

# ── 日志 ──────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "etf_monitor.log")
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── 配置（支持环境变量）──────────────────────────────────────────────
ETF_CODES = [c.strip() for c in os.getenv("ETF_CODES", "512890").split(",") if c.strip()]

# 名称映射：优先从环境变量 ETF_NAMES 获取，格式: code:name,code:name
# 如: ETF_NAMES=512890:红利低波ETF,510300:沪深300ETF
def load_etf_names() -> dict:
    names_env = os.getenv("ETF_NAMES", "")
    if names_env:
        mapping = {}
        for item in names_env.split(","):
            if ":" in item:
                code, name = item.split(":", 1)
                mapping[code.strip()] = name.strip()
        if mapping:
            return mapping
    return {}

def fetch_etf_names_from_tencent(codes: list[str]) -> dict:
    """从腾讯行情接口批量获取 ETF 名称，返回 {code: name}"""
    import urllib.request
    symbols = ",".join(get_baostock_code(c).replace(".", "") for c in codes)
    url = f"https://qt.gtimg.cn/q={symbols}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    names = {}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        for line in text.split(";"):
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].replace("v_", "").strip()
            fields = line.split('"')[1].split("~")
            if len(fields) > 2 and fields[2] == key[2:]:
                names[fields[2]] = fields[1]
    except Exception as e:
        log(f"[tencent-name] 获取名称失败: {e}", "WARN")
    return names

ETF_NAMES_MAP = load_etf_names()

# 名称缺失时用腾讯接口自动补名（ETF_NAMES 优先级更高）
missing = [c for c in ETF_CODES if c not in ETF_NAMES_MAP]
if missing:
    ETF_NAMES_MAP.update(fetch_etf_names_from_tencent(missing))

MA_PERIOD = 250

PUSH_MODE = os.getenv("PUSH_MODE", "digest").strip() or "digest"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_GROUP = os.getenv("TELEGRAM_GROUP", "")

PROXY_URL = os.getenv("PROXY_URL", "")
BARK_URL = os.getenv("BARK_URL", "")
BARK_GROUP = os.getenv("BARK_GROUP", "")

ENABLE_BARK = bool(BARK_URL)
ENABLE_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

digest_buffers = {"Bark": [], "Telegram": []}

# ── 数据获取 ──────────────────────────────────────────────────────
def get_quote_symbol(code: str) -> str:
    """行情代码（腾讯/新浪用，无点），如 sh512890 / sz159919"""
    return get_baostock_code(code).replace(".", "")

def fetch_eastmoney_klines(code: str, days: int) -> list[float] | None:
    """东方财富日K线收盘价列表（升序），失败抛出异常。"""
    import urllib.request
    import urllib.error

    base = PROXY_URL if PROXY_URL else "https://push2his.eastmoney.com"
    secid = get_eastmoney_secid(code)
    url = (
        f"{base}/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt={days}"
    )
    log(f"[eastmoney-api] 请求URL: {url}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return None
    return [float(k.split(",")[2]) for k in klines]

def fetch_tencent_klines(code: str, count: int = 330) -> list[float] | None:
    """腾讯证券前复权日K线收盘价列表（升序），失败返回 None。"""
    symbol = get_quote_symbol(code)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{count},qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        node = data.get("data", {}).get(symbol, {})
        klines = node.get("qfqday") or node.get("day") or []
        prices = [float(k[2]) for k in klines]
        if prices:
            log(f"[tencent] 成功获取 {code} 前复权日K线 {len(prices)} 条")
            return prices
    except Exception as e:
        log(f"[tencent] {code} 获取失败: {e}", "WARN")
    return None

def fetch_sina_klines(code: str, count: int = 330) -> list[float] | None:
    """新浪日K线收盘价列表（升序），失败返回 None。"""
    symbol = get_quote_symbol(code)
    url = (
        f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={count}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        prices = [float(item["close"]) for item in data]
        if prices:
            log(f"[sina] 成功获取 {code} 日K线 {len(prices)} 条")
            return prices
    except Exception as e:
        log(f"[sina] {code} 获取失败: {e}", "WARN")
    return None

def fetch_etf_price(code: str) -> float | None:
    """
    尝试多个数据源获取ETF最新价格（收盘价）。
    返回 None 表示所有数据源均失败。
    """
    # ① 腾讯
    prices = fetch_tencent_klines(code)
    if prices:
        close = prices[-1]
        log(f"[tencent] 成功获取 {code} 最新收盘价: {close}")
        return close

    # ② 新浪
    prices = fetch_sina_klines(code)
    if prices:
        close = prices[-1]
        log(f"[sina] 成功获取 {code} 最新收盘价: {close}")
        return close

    # ③ 东方财富
    try:
        prices = fetch_eastmoney_klines(code, 250)
        if prices:
            close = prices[-1]
            log(f"[eastmoney-api] 成功获取 {code} 最新收盘价: {close}")
            return close
    except Exception as e:
        log(f"[eastmoney-api] {code} 获取失败: {e}", "WARN")

    # ④ baostock — 日K线
    try:
        import baostock as bs
        lg = bs.login()
        rs = bs.query_history_k_data_plus(
            get_baostock_code(code),
            "date,close",
            start_date=str(datetime.date.today() - datetime.timedelta(days=10)),
            end_date=str(datetime.date.today()),
            frequency="d",
            adjustflag="3"
        )
        bs.logout()
        if rs.error_code == "0":
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                for row in reversed(rows):
                    if row[1] and row[1] != "None":
                        close = float(row[1])
                        log(f"[baostock] 成功获取 {code} 最新收盘价: {close}")
                        return close
    except Exception as e:
        log(f"[baostock] {code} 获取失败: {e}", "WARN")

    log(f"[全部数据源失败] 无法获取 {code} 今日价格", "ERROR")
    return None


def fetch_historical_prices(code: str, days: int = 260) -> list[float] | None:
    """
    获取过去 N 个交易日的收盘价列表（用于计算均线）。
    返回升序排列的价格列表，失败返回 None。
    """
    count = max(days, 330)

    # ① 腾讯
    prices = fetch_tencent_klines(code, count)
    if prices:
        return prices

    # ② 新浪
    prices = fetch_sina_klines(code, count)
    if prices:
        return prices

    # ③ 东方财富
    for _ in range(3):
        try:
            prices = fetch_eastmoney_klines(code, days)
            if prices:
                log(f"[eastmoney-api] 成功获取 {code} 历史K线 {len(prices)} 条")
                return prices
        except Exception as e:
            log(f"[eastmoney-api] 重试失败: {e}", "WARN")
            import time
            time.sleep(1)

    # ④ baostock 备用（按自然日请求，需覆盖足够交易日）
    try:
        import baostock as bs
        bs.login()
        calendar_days = max(days + 150, 450)
        rs = bs.query_history_k_data_plus(
            get_baostock_code(code),
            "date,close",
            start_date=str(datetime.date.today() - datetime.timedelta(days=calendar_days)),
            end_date=str(datetime.date.today()),
            frequency="d",
            adjustflag="3"
        )
        bs.logout()
        if rs.error_code == "0":
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                prices = [float(row[1]) for row in rows if row[1] and row[1] != "None"]
                log(f"[baostock] 成功获取 {code} 历史K线 {len(prices)} 条")
                return prices
    except Exception as e:
        log(f"[baostock] 历史K线获取失败: {e}", "WARN")

    log(f"[全部数据源失败] 无法获取 {code} 历史K线", "ERROR")
    return None


# ── 均线计算 ──────────────────────────────────────────────────────
def calc_ma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


# ── 提醒推送 ──────────────────────────────────────────────────────
def send_bark_message(title: str, body: str):
    if not BARK_URL:
        return False
    try:
        url = f"{BARK_URL}/{urllib.parse.quote(title, safe='')}/{urllib.parse.quote(body, safe='')}"
        if BARK_GROUP:
            url += f"?group={urllib.parse.quote(BARK_GROUP)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0")
        urllib.request.urlopen(req, timeout=10)
        log("Bark 通知已发送")
        return True
    except Exception as e:
        log(f"Bark 通知失败: {e}", "WARN")
        return False


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }
        if TELEGRAM_GROUP:
            data["message_thread_id"] = TELEGRAM_GROUP
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        urllib.request.urlopen(req, timeout=10)
        log("Telegram 通知已发送")
        return True
    except Exception as e:
        log(f"Telegram 通知失败: {e}", "WARN")
        return False


def send_push(provider: str, title: str, body: str):
    if provider == "Bark":
        return send_bark_message(title, body)
    elif provider == "Telegram":
        text = f"{title}\n\n{body}"
        return send_telegram_message(text)
    return False


def add_to_digest(provider: str, text: str):
    if provider in digest_buffers:
        digest_buffers[provider].append(text)


def flush_digest():
    if PUSH_MODE != "digest":
        return
    texts = []
    for provider in ("Bark", "Telegram"):
        for text in digest_buffers[provider]:
            if text not in texts:
                texts.append(text)
    if not texts:
        return
    body = "\n------------------------\n".join(texts)
    if ENABLE_BARK:
        send_bark_message("📊 ETF 定投汇总", "------------------------\n" + body)
    if ENABLE_TELEGRAM:
        send_telegram_message("📊 ETF 定投汇总\n------------------------\n" + body)
    digest_buffers["Bark"] = []
    digest_buffers["Telegram"] = []


def send_notification(title: str, body: str):
    if PUSH_MODE == "digest":
        block = f"{title}\n\n{body}"
        if ENABLE_BARK:
            add_to_digest("Bark", block)
        if ENABLE_TELEGRAM:
            add_to_digest("Telegram", block)
    else:
        if ENABLE_BARK:
            send_push("Bark", title, body)
        if ENABLE_TELEGRAM:
            send_push("Telegram", title, body)


def process_etf(code: str):
    """处理单个 ETF 的监测逻辑：获取当前价、历史价、MA、偏离度，并在低于均线时发送通知"""
    name = ETF_NAMES_MAP.get(code, code)
    today = datetime.date.today()
    log(f"[{code}] 监测开始，日期: {today}, 名称: {name}")

    # ① 获取当前价格
    current_price = fetch_etf_price(code)
    if current_price is None:
        send_notification(
            f"{name} 数据获取异常",
            f"日期: {today}\n未能获取今日价格，请检查数据源。"
        )
        return

    # ② 获取历史价格 → 计算 MA250
    prices = fetch_historical_prices(code, days=MA_PERIOD + 50)
    if prices is None or len(prices) < MA_PERIOD:
        send_notification(
            f"{name} 数据不足",
            f"历史数据不足 {MA_PERIOD} 条，无法计算均线。"
        )
        return

    ma250 = calc_ma(prices, MA_PERIOD)
    deviation = (current_price - ma250) / ma250 * 100   # 单位: %

    log(f"当前价格: {current_price:.4f}")
    log(f"MA250:    {ma250:.4f}")
    log(f"偏离度:   {deviation:+.4f}%")

    # ③ 判断 & 输出 - 无论正负都发送通知
    if deviation < 0:
        status = "✅ 低于均线 → 建议定投买入"
        hint = "当前价格低于250日均线，适合定投买入"
    else:
        status = "ℹ️ 高于/等于均线，暂不定投"
        hint = "价格位于均线上方，耐心等待回调"

    # ④ 发送通知
    title = f"📈 {name}[{code}] 定投提醒"
    body_lines = [
        f"📅 日期: {today}",
        f"💰 当前价格: ¥{current_price:.4f}",
        f"📊 250日均线: ¥{ma250:.4f}",
        f"📉 偏离度: {deviation:+.2f}%",
        "",
        f"💡 建议: {hint}",
        "",
        f"📌 {status}",
    ]
    body = "\n".join(body_lines)
    send_notification(title, body)
    log(f"提醒已推送: {status}")


# ── 主逻辑 ────────────────────────────────────────────────────────
def main():
    today = datetime.date.today()
    log(f"========== {today} 多ETF 定投监测开始 ==========")
    log(f"配置: PUSH_MODE={PUSH_MODE!r}, ETF_CODES={ETF_CODES}, Bark={'开' if ENABLE_BARK else '关'}, Telegram={'开' if ENABLE_TELEGRAM else '关'}")

    # 遍历配置的 ETF 列表，逐个处理
    for code in ETF_CODES:
        process_etf(code)

    # Digest 模式：在最后统一发送
    flush_digest()


if __name__ == "__main__":
    main()
