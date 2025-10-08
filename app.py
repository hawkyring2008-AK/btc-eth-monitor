# app.py
"""
BTC/ETH 市场情绪监控器（简洁版）
- 自动检测频率固定为 180 分钟（3 小时）
- 邮件标题保持简洁，邮件内容详细（价格、关键链上指标、评分与建议）
- 通知：Gmail SMTP + Server酱 (ServerChan)
- 链上/机构指标：可选使用 Glassnode（将 GLASSNODE_API_KEY 放入 Secrets）
"""

import os
import json
import time
import statistics
import atexit
import requests
from datetime import datetime, timezone
from typing import Dict, Any, List

import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler
from email.mime.text import MIMEText
from email.header import Header
import smtplib

# --------------------
# 配置与 Secrets（请在 Streamlit Secrets 或环境变量中配置）
# --------------------
def get_secret(key, default=None):
    try:
        v = st.secrets.get(key)
        if v is not None:
            return v
    except Exception:
        pass
    return os.getenv(key, default)

# 必要项（不写到代码中）
GMAIL_USER = get_secret("GMAIL_USER")            # 发件 Gmail（例如 your@gmail.com）
GMAIL_APP_PASS = get_secret("GMAIL_APP_PASS")    # Gmail App Password (建议使用 App Password)
ALERT_EMAIL_TO = get_secret("ALERT_EMAIL_TO")    # 收件邮箱（例如 alert@you.com）
SERVERCHAN_SENDKEY = get_secret("SERVERCHAN_SENDKEY")  # Server酱 SendKey (SCTxxxx)

# 可选：Glassnode（提高链上指标质量）
GLASSNODE_API_KEY = get_secret("GLASSNODE_API_KEY", None)

# 固定的检测间隔（分钟）——3 小时
FIXED_INTERVAL_MIN = 180

# 评分阈值（可在 Secrets 中覆盖）
OVERHEAT_THRESHOLD = float(get_secret("OVERHEAT_THRESHOLD", 60.0))
OVERSOLD_THRESHOLD = float(get_secret("OVERSOLD_THRESHOLD", 30.0))

# 监控资产
ASSETS = {"BTC": "bitcoin", "ETH": "ethereum"}

# 本地历史缓存目录（Streamlit 环境为临时盘）
HIST_DIR = ".hist_cache_overheat"
os.makedirs(HIST_DIR, exist_ok=True)

# --------------------
# 工具函数
# --------------------
def now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def http_get_json(url, params=None, timeout=10):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.warning(f"HTTP 请求失败: {e}")
        return None

# --------------------
# 数据抓取（CoinGecko + optional Glassnode）
# --------------------
def fetch_price_coingecko(asset_id: str) -> Dict[str, Any]:
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "ids": asset_id, "price_change_percentage": "24h"}
    j = http_get_json(url, params=params)
    if not j:
        return {"price": None, "price_change_24h_pct": None}
    d = j[0]
    return {"price": d.get("current_price"), "price_change_24h_pct": d.get("price_change_percentage_24h")}

GLASSNODE_BASE = "https://api.glassnode.com/v1"
def glassnode_try(metric, asset_symbol):
    """
    轻量尝试调用 Glassnode 指标（返回最新点值）。若未配置 API key 则返回 None。
    若需要更复杂的历史序列计算，可后续扩展。
    """
    if not GLASSNODE_API_KEY:
        return None
    url = f"{GLASSNODE_BASE}/metrics/{metric}"
    params = {"a": asset_symbol, "api_key": GLASSNODE_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return data[-1].get("v")
        return data
    except Exception:
        return None

def fetch_exchange_flow(sym: str):
    v = glassnode_try("transactions/TransfersVolumeToExchangesSum", sym)
    return float(v) if v is not None else 0.0

def fetch_etf_netflow(sym: str):
    v = glassnode_try("institutions/UsSpotEtfFlowsNet", sym)
    return float(v) if v is not None else 0.0

def fetch_funding_rate(sym: str):
    v = glassnode_try("derivatives/FuturesFundingRatePerpetual", sym)
    return float(v) if v is not None else 0.0

def fetch_oi_change_pct(sym: str):
    # 简化：返回 0.0（可扩展为拉 7d/30d 序列并计算 pct change）
    return 0.0

def fetch_reserve_change_pct(sym: str):
    # 交易所储备变化 pct（简化实现）
    return 0.0

def fetch_whale_count(sym: str, threshold_amount: float):
    # 简化占位，返回 0；可改为调用 Kaiko/Glassnode 的 whale metrics
    return 0

# --------------------
# 评分逻辑（z-score -> 0-100 映射）
# --------------------
def compute_zscore(value, hist: List[float]):
    if not hist or len(hist) < 2:
        return 0.0
    mu = statistics.mean(hist)
    sigma = statistics.pstdev(hist)
    if sigma == 0:
        return 0.0
    return (value - mu) / sigma

def normalize_score(raw, minv=-3, maxv=3):
    clipped = max(min(raw, maxv), minv)
    return (clipped - minv) / (maxv - minv) * 100

def compute_overheat_score(metrics: Dict[str, float], hist_stats: Dict[str, List[float]]):
    # 各指标 z-score
    z = {}
    for k, v in metrics.items():
        z[k] = compute_zscore(v, hist_stats.get(k, []))
    # 权重示例（可后续回测调整）
    raw = 0.0
    raw += z.get("etf_netflow", 0.0) * 0.30
    raw += z.get("exchange_netflow", 0.0) * 0.15
    raw += z.get("oi_change_pct", 0.0) * 0.15
    raw += z.get("funding_rate", 0.0) * 0.10
    raw += z.get("whale_count", 0.0) * 0.10
    raw += (-z.get("reserve_change_pct", 0.0)) * 0.20
    score = normalize_score(raw, -3, 3)
    return score, z

# --------------------
# 本地历史存储（简单 JSON 文件）
# --------------------
def hist_path(asset, metric):
    return os.path.join(HIST_DIR, f"{asset}__{metric}.json")

def load_hist(asset, metric, days=90):
    p = hist_path(asset, metric)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r") as f:
            arr = json.load(f)
        return arr[-days:]
    except:
        return []

def append_hist(asset, metric, value):
    p = hist_path(asset, metric)
    arr = []
    if os.path.exists(p):
        try:
            with open(p, "r") as f:
                arr = json.load(f)
        except:
            arr = []
    arr.append(value)
    arr = arr[-180:]
    with open(p, "w") as f:
        json.dump(arr, f)

# --------------------
# 通知：Gmail SMTP + Server酱（邮件标题简洁，邮件正文详细）
# --------------------
def send_email_gmail_shorttitle(subject_short, detailed_body):
    """
    subject_short: 简洁标题（如 "⚠️ BTC 过热警报"）
    detailed_body: 邮件正文（多行详细信息）
    """
    if not (GMAIL_USER and GMAIL_APP_PASS and ALERT_EMAIL_TO):
        st.warning("邮件未配置完整（GMAIL_USER/GMAIL_APP_PASS/ALERT_EMAIL_TO），将跳过邮件发送。")
        return False
    try:
        # 邮件正文使用详细文本
        msg = MIMEText(detailed_body, "plain", "utf-8")
        msg["From"] = GMAIL_USER
        msg["To"] = ALERT_EMAIL_TO
        msg["Subject"] = Header(subject_short, "utf-8")
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, [e.strip() for e in ALERT_EMAIL_TO.split(",")], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"邮件发送失败: {e}")
        return False

def send_serverchan(title, content_md):
    if not SERVERCHAN_SENDKEY:
        st.warning("ServerChan SendKey 未配置，跳过微信推送。")
        return False
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    try:
        r = requests.post(url, data={"title": title, "desp": content_md}, timeout=10)
        if r.status_code == 200:
            return True
        else:
            st.warning(f"ServerChan 返回: {r.status_code} {r.text}")
            return False
    except Exception as e:
        st.warning(f"ServerChan 推送异常: {e}")
        return False

# --------------------
# 单次检测流程（返回检测记录）
# --------------------
def single_check():
    t = now_utc_str()
    alerts = []
    results = []
    for sym, cg_id in ASSETS.items():
        price_info = fetch_price_coingecko(cg_id)
        metrics = {
            "etf_netflow": fetch_etf_netflow(sym),
            "exchange_netflow": fetch_exchange_flow(sym),
            "oi_change_pct": fetch_oi_change_pct(sym),
            "funding_rate": fetch_funding_rate(sym),
            "whale_count": fetch_whale_count(sym, threshold_amount=100 if sym == "BTC" else 1000),
            "reserve_change_pct": fetch_reserve_change_pct(sym)
        }
        # 历史载入并附加当前值（用于 z-score）
        hist_stats = {}
        for k, v in metrics.items():
            h = load_hist(cg_id, k, days=90)
            hist_stats[k] = h
            append_hist(cg_id, k, v)
        score, z = compute_overheat_score(metrics, hist_stats)
        rec = {"time": t, "symbol": sym, "price": price_info.get("price"), "price_change_24h_pct": price_info.get("price_change_24h_pct"), "score": score, "metrics": metrics, "z": z}
        results.append(rec)
        if score >= OVERHEAT_THRESHOLD:
            alerts.append(("OVERHEAT", rec))
        elif score <= OVERSOLD_THRESHOLD:
            alerts.append(("OVERSOLD", rec))
    # 若存在告警，发送通知
    if alerts:
        # 针对每个告警形成简洁标题 + 详细正文
        for tag, rec in alerts:
            sym = rec["symbol"]
            score = rec["score"]
            # 简洁标题（中文）
            title = f"{'⚠️' if tag=='OVERHEAT' else '🔔'} {sym} {'过热' if tag=='OVERHEAT' else '超跌'} 警报"
            # 详细正文（多行）
            lines = []
            lines.append(f"{title}")
            lines.append(f"时间: {rec['time']}")
            lines.append(f"资产: {sym}")
            lines.append(f"当前价格（USD）: {rec['price']}")
            lines.append(f"24h 价格变动 (%): {rec.get('price_change_24h_pct')}")
            lines.append(f"Overheat Score: {score:.1f} (阈值: >={OVERHEAT_THRESHOLD} 为过热； <={OVERSOLD_THRESHOLD} 为超跌)")
            lines.append("")
            lines.append("主要指标（原始值）:")
            for k, v in rec["metrics"].items():
                lines.append(f"  - {k}: {v}")
            lines.append("")
            lines.append("贡献度（z-score）:")
            for k, zval in rec["z"].items():
                lines.append(f"  - {k}: {zval:+.3f}")
            lines.append("")
            # 简短建议（基于标签）
            if tag == "OVERHEAT":
                lines.append("简短建议：市场可能过热，短期回撤风险增加。可考虑审慎减仓或设置止盈/风险限额。")
            else:
                lines.append("简短建议：市场情绪偏弱/超跌，若基于长期投资，可考虑分批布局；短期波动风险较高。")
            detailed_body = "\n".join(lines)
            # 发送邮件（简短标题 + 详细正文）
            send_email_gmail_shorttitle(title, detailed_body)
            # 发送 ServerChan（正文用代码块包裹，便于微信阅读）
            send_serverchan(title, "```\n" + detailed_body + "\n```")
    # 保存上次运行记录供 UI 展示
    with open(os.path.join(HIST_DIR, "last_run.json"), "w") as f:
        json.dump({"time": t, "results": results, "alerts": [a[1] for a in alerts]}, f)
    return {"time": t, "results": results, "alerts": alerts}

# --------------------
# 调度器（固定每 180 分钟）
# --------------------
scheduler = BackgroundScheduler()
atexit.register(lambda: scheduler.shutdown(wait=False) if scheduler.running else None)

def start_scheduler():
    try:
        scheduler.remove_all_jobs()
    except:
        pass
    # 使用固定 180 分钟（3 小时）
    scheduler.add_job(single_check, 'interval', minutes=FIXED_INTERVAL_MIN, id="crypto_overheat_job", next_run_time=None)
    scheduler.start()

# --------------------
# Streamlit UI（简洁中文）
# --------------------
st.set_page_config(page_title="BTC/ETH 市场情绪监控器", layout="wide")
st.title("BTC/ETH 市场情绪监控器")
st.markdown("固定每 **3 小时** 自动检测（若需立刻检测请使用“手动检测”按钮）。<br>当检测到**过热**或**超跌**时，将同时发送 **简洁邮件标题 + 详细邮件正文**，并通过 Server酱 推送微信。", unsafe_allow_html=True)

col_left, col_right = st.columns([3,1])

with col_right:
    st.subheader("运行信息")
    st.markdown(f"- 检测间隔（固定）： **{FIXED_INTERVAL_MIN} 分钟（3 小时）**")
    st.markdown(f"- 当前过热阈值： **{OVERHEAT_THRESHOLD}**")
    st.markdown(f"- 当前超跌阈值： **{OVERSOLD_THRESHOLD}**")
    st.markdown("---")
    st.subheader("通知配置")
    st.write("- 发件 Gmail（请在 Secrets 填写）")
    st.write("- 收件邮箱（ALERT_EMAIL_TO）")
    st.write("- ServerChan SendKey（SERVERCHAN_SENDKEY）")
    st.write("- 可选：GLASSNODE_API_KEY（启用更丰富链上指标）")
    st.markdown("---")
    if st.button("手动检测一次（立即）"):
        res = single_check()
        st.success("手动检测已完成")
        st.json(res)

with col_left:
    st.subheader("最近检测摘要")
    # 读取上次运行记录
    lr_path = os.path.join(HIST_DIR, "last_run.json")
    if os.path.exists(lr_path):
        try:
            with open(lr_path, "r") as f:
                last = json.load(f)
            st.markdown(f"**上次运行时间：** {last.get('time')}")
            for rec in last.get("results", []):
                st.metric(label=f"{rec['symbol']} Overheat Score", value=f"{rec['score']:.1f}")
                st.write(f"- 价格: {rec.get('price')} USD")
                st.write(f"- 24h 变动: {rec.get('price_change_24h_pct')}")
                st.write(f"- 关键指标快照: {rec.get('metrics')}")
                st.markdown("---")
            if last.get("alerts"):
                st.warning("上次检测触发了告警（详情已通过邮件/微信发送）。")
        except Exception as e:
            st.error(f"读取上次记录失败: {e}")
    else:
        st.info("尚未有检测记录。请点击右侧「手动检测一次（立即）」或等待定时器首次运行（3 小时内）。")

st.caption("提示：若未配置 GLASSNODE_API_KEY，则链上/ETF/衍生品相关指标将退化为占位值 0，建议配置以获得完整信号。")

# 启动调度器（仅在 app 首次加载或重启时启动一次）
if "scheduler_started" not in st.session_state:
    try:
        start_scheduler()
        st.session_state["scheduler_started"] = True
        st.success(f"监控已启动（每 {FIXED_INTERVAL_MIN} 分钟检测一次）。")
    except Exception as e:
        st.error(f"调度器启动失败：{e}")
