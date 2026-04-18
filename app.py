import streamlit as st
import requests
import pandas as pd
import time
import threading
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ======================================================
# 🔑 CONFIG — Update via st.secrets or manually here
# ======================================================
ACCESS_TOKEN = st.secrets.get("ACCESS_TOKEN", "")
EXPIRY_DATE  = st.secrets.get("EXPIRY_DATE", "2026-04-21")
TG_BOT_TOKEN = st.secrets.get("TG_BOT_TOKEN", "")
TG_CHAT_ID   = st.secrets.get("TG_CHAT_ID", "")
REFRESH_RATE = 5  # seconds

# ======================================================
st.set_page_config(
    page_title="Nifty Intelligence Terminal v9.1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── Dark theme override ──────────────────────────────
st.markdown("""
<style>
  /* Base */
  html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #111111 !important;
    color: #e0e0e0 !important;
  }
  /* Metric cards */
  [data-testid="stMetric"] {
    background: #1e1e1e;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px 16px !important;
  }
  [data-testid="stMetricLabel"]  { color: #888 !important; font-size: 0.75rem !important; }
  [data-testid="stMetricValue"]  { color: #f0f0f0 !important; font-size: 1.4rem !important; font-weight: 700 !important; }
  [data-testid="stMetricDelta"]  { font-size: 0.85rem !important; }
  /* Progress bar */
  [data-testid="stProgress"] > div { background: #2a2a2a !important; border-radius: 4px; }
  /* Alert banners */
  .alert-box {
    border-radius: 8px;
    padding: 14px 20px;
    font-size: 1.1rem;
    font-weight: 700;
    margin-bottom: 6px;
    text-align: center;
  }
  /* Sidebar */
  [data-testid="stSidebar"] { background: #151515 !important; }
  /* Divider */
  hr { border-color: #2a2a2a !important; }
  /* Mini card */
  .mini-card {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px;
    text-align: center;
    margin-bottom: 8px;
  }
  .mini-card .title { font-size: 0.7rem; color: #888; text-transform: uppercase; letter-spacing: 0.06em; }
  .mini-card .value { font-size: 1.5rem; font-weight: 800; color: #fff; margin-top: 4px; }
  /* Bull/Bear bar label */
  .prob-label { font-size: 1.1rem; font-weight: 700; color: #fff; }
  /* Token input */
  [data-testid="stTextInput"] input {
    background: #1a1a1a !important;
    color: #e0e0e0 !important;
    border: 1px solid #333 !important;
  }
  /* Buttons */
  [data-testid="stButton"] > button {
    background: #27ae60 !important;
    color: white !important;
    border: none !important;
    border-radius: 6px !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
  }
  [data-testid="stButton"] > button:hover { background: #1e8449 !important; }
  /* Hide Streamlit branding */
  #MainMenu, footer, header { visibility: hidden !important; }
  .block-container { padding-top: 1rem !important; padding-bottom: 1rem !important; }
  /* Reduce gap */
  .stVerticalBlock { gap: 0.4rem !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state init ───────────────────────────────
def init_state():
    defaults = {
        "running": False,
        "spot": None, "vix": None, "pcr": None,
        "prev_vix": 0.0, "prev_spot": 0.0,
        "pcr_history": [], "vix_history": [],
        "alert_msg": "SYSTEM READY — CLICK START",
        "alert_color": "#1e2d3d",
        "active_res": "--", "active_sup": "--", "battle": "--",
        "bull_prob": 50.0,
        "oi_data": None,
        "last_minute": -1,
        "error": None,
        "last_refresh": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ── Helpers ──────────────────────────────────────────
def send_telegram(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.get(url, params={"chat_id": TG_CHAT_ID, "text": msg}, timeout=5)
    except:
        pass

def get_market_data(token):
    url = ("https://api.upstox.com/v2/market-quote/quotes"
           "?instrument_key=NSE_INDEX|Nifty 50,NSE_INDEX|India VIX")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 401:
            st.session_state["error"] = "❌ INVALID TOKEN — Please update in Settings"
            return None, None
        data = r.json().get("data")
        if not data:
            return None, None
        spot = float(data["NSE_INDEX:Nifty 50"]["last_price"])
        vix  = float(data["NSE_INDEX:India VIX"]["last_price"])
        st.session_state["error"] = None
        return spot, vix
    except Exception as e:
        st.session_state["error"] = f"❌ CONNECTION ERROR: {str(e)[:40]}"
        return None, None

def get_option_chain(token, expiry):
    url = (f"https://api.upstox.com/v2/option/chain"
           f"?instrument_key=NSE_INDEX|Nifty 50&expiry_date={expiry}")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        resp = r.json()
        if "data" in resp:
            return resp["data"]
        else:
            st.session_state["error"] = "❌ EXPIRY DATE NOT FOUND"
            return None
    except Exception as e:
        st.session_state["error"] = f"❌ CHAIN FETCH ERROR: {str(e)[:40]}"
        return None

def analyze(data, spot, vix, token):
    try:
        df = pd.json_normalize(data).sort_values("strike_price")
        df["call_chg_oi"] = df["call_options.market_data.oi"] - df["call_options.market_data.prev_oi"]
        df["put_chg_oi"]  = df["put_options.market_data.oi"]  - df["put_options.market_data.prev_oi"]
        total_put_oi  = df["put_options.market_data.oi"].sum()
        total_call_oi = df["call_options.market_data.oi"].sum()
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0.0

        df["dist"] = (df["strike_price"] - spot).abs()
        subset_idx = df["dist"].idxmin()
        subset = df.iloc[max(0, subset_idx - 2): min(len(df), subset_idx + 3)]

        active_res = int(subset.loc[subset["call_chg_oi"].idxmax(), "strike_price"])
        active_sup = int(subset.loc[subset["put_chg_oi"].idxmax(), "strike_price"])
        battle     = int(subset.loc[subset["call_chg_oi"].abs().idxmax(), "strike_price"])

        prev_vix  = st.session_state["prev_vix"]
        vix_chg   = ((vix - prev_vix) / prev_vix * 100) if prev_vix != 0 else 0.0
        bull_prob = max(5, min(95, 50 + (pcr - 1.0) * 40 + (vix_chg * -2)))

        curr_time = datetime.now().strftime("%H:%M:%S")
        h_pcr = st.session_state["pcr_history"]
        h_vix = st.session_state["vix_history"]
        if not h_pcr or h_pcr[-1][1] != pcr:
            h_pcr.append((curr_time, pcr))
            if len(h_pcr) > 100: h_pcr.pop(0)
        if not h_vix or h_vix[-1][1] != vix:
            h_vix.append((curr_time, vix))
            if len(h_vix) > 100: h_vix.pop(0)

        # Alert
        if spot > active_res:
            alert_msg, alert_color = "🚀 STRONG BREAKOUT", "#1d4d2b"
        elif spot < active_sup:
            alert_msg, alert_color = "📉 STRONG BREAKDOWN", "#5c1d1d"
        else:
            alert_msg, alert_color = "⚖️ SIDEWAYS", "#2c3e50"

        # Telegram every minute
        now = datetime.now()
        if now.minute != st.session_state["last_minute"]:
            direction = "BULLISH" if bull_prob >= 50 else "BEARISH"
            msg = (f"🧠 AI PREDICTION: {bull_prob:.0f}% {direction}\n"
                   f"Spot: {spot:.1f}\nPCR: {pcr:.2f}\nVIX: {vix:.2f}\n"
                   f"Active Res: {active_res}\nActive Sup: {active_sup}\n"
                   f"Status: {alert_msg}")
            threading.Thread(target=send_telegram, args=(msg,), daemon=True).start()
            st.session_state["last_minute"] = now.minute

        # Save to session
        st.session_state.update({
            "spot": spot, "vix": vix, "pcr": pcr,
            "prev_vix": vix, "prev_spot": spot,
            "active_res": active_res, "active_sup": active_sup, "battle": battle,
            "bull_prob": bull_prob, "vix_chg": vix_chg,
            "alert_msg": alert_msg, "alert_color": alert_color,
            "oi_data": subset,
            "pcr_history": h_pcr, "vix_history": h_vix,
        })
    except Exception as e:
        st.session_state["error"] = f"❌ ANALYSIS ERROR: {str(e)[:50]}"

def make_dark_fig(figsize=(5.5, 2.8)):
    fig, ax = plt.subplots(figsize=figsize, dpi=100)
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#111111")
    ax.tick_params(colors="#888", labelsize=7)
    ax.spines[:].set_color("#2a2a2a")
    ax.grid(True, alpha=0.12, color="#444")
    return fig, ax

# ── UI: Top bar ──────────────────────────────────────
title_col, token_col, btn_col = st.columns([3, 5, 2])
with title_col:
    st.markdown("### 📡 Nifty Intelligence Terminal v9.1")

with token_col:
    token_input = st.text_input(
        "Access Token",
        value=ACCESS_TOKEN,
        type="password",
        label_visibility="collapsed",
        placeholder="🔑 Paste Upstox Access Token here...",
        key="token_input"
    )

with btn_col:
    if st.session_state["running"]:
        if st.button("⏹ STOP SCANNER", use_container_width=True):
            st.session_state["running"] = False
            st.rerun()
    else:
        if st.button("▶ START SCANNER", use_container_width=True):
            if not token_input:
                st.warning("Please enter your Upstox Access Token above.")
            else:
                st.session_state["running"] = True
                st.rerun()

st.divider()

# ── Auto-refresh while running ───────────────────────
if st.session_state["running"]:
    # Fetch data
    token = token_input or ACCESS_TOKEN
    spot, vix = get_market_data(token)
    if spot:
        chain = get_option_chain(token, EXPIRY_DATE)
        if chain:
            analyze(chain, spot, vix, token)
    # Schedule next refresh
    time.sleep(REFRESH_RATE)
    st.rerun()

# ── Error Banner ─────────────────────────────────────
if st.session_state.get("error"):
    st.markdown(
        f'''<div class="alert-box" style="background:#5c1d1d; color:#ff8a8a;">
        {st.session_state["error"]}</div>''',
        unsafe_allow_html=True
    )

# ── Alert box ────────────────────────────────────────
st.markdown(
    f'''<div class="alert-box" style="background:{st.session_state["alert_color"]}; color:#ecf0f1;">
    {st.session_state["alert_msg"]}</div>''',
    unsafe_allow_html=True
)

# ── Header metrics ───────────────────────────────────
m1, m2, m3, m4, m5 = st.columns(5)
spot_val = f"{st.session_state['spot']:,.2f}" if st.session_state["spot"] else "--"
pcr_val  = f"{st.session_state['pcr']:.2f}"  if st.session_state["pcr"]  else "--"
vix_val  = f"{st.session_state['vix']:.2f}"  if st.session_state["vix"]  else "--"
vix_delta = f"{st.session_state.get('vix_chg', 0.0):+.2f}%" if st.session_state["vix"] else None
bull_p    = st.session_state["bull_prob"]
status_icon = "🟢" if st.session_state["running"] else "🔴"

m1.metric("📊 NIFTY SPOT",  spot_val)
m2.metric("📐 PCR",         pcr_val)
m3.metric("⚡ VIX",          vix_val, delta=vix_delta)
m4.metric("🎯 BULL PROB",   f"{bull_p:.0f}%")
m5.metric("🔄 STATUS",      "LIVE" if st.session_state["running"] else "IDLE")

# ── Progress / Bull-Bear Bar ──────────────────────────
bp = bull_p / 100
st.markdown(
    f'''<div style="display:flex;align-items:center;gap:12px;margin:4px 0;">
    <span class="prob-label" style="color:{"#2ecc71" if bull_p>=50 else "#e74c3c"};">
      {"🐂" if bull_p>=50 else "🐻"} {bull_p:.0f}% {"BULL" if bull_p>=50 else "BEAR"}
    </span></div>''',
    unsafe_allow_html=True
)
st.progress(bp)

# ── OI Charts + Wall Sidebar ─────────────────────────
oi_col, wall_col = st.columns([4, 1])

with oi_col:
    subset = st.session_state.get("oi_data")
    if subset is not None:
        fig_oi, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.0), dpi=100)
        fig_oi.patch.set_facecolor("#111111")
        for ax in [ax1, ax2]:
            ax.set_facecolor("#111111")
            ax.tick_params(colors="#888", labelsize=7)
            ax.spines[:].set_color("#2a2a2a")
            ax.grid(True, alpha=0.12, color="#444")

        # OI chart
        ax1.bar(subset["strike_price"] - 12, subset["call_options.market_data.oi"], width=24, color="#ff4d4d", label="Call OI")
        ax1.bar(subset["strike_price"] + 12, subset["put_options.market_data.oi"],  width=24, color="#00ff88", label="Put OI")
        ax1.set_title("Open Interest", color="#aaa", fontsize=8)
        ax1.legend(fontsize=6, facecolor="#1a1a1a", labelcolor="#ccc", framealpha=0.8)

        # Change OI
        ax2.bar(subset["strike_price"] - 12, subset["call_chg_oi"], width=24, color="#ff4d4d", label="Call ΔOI")
        ax2.bar(subset["strike_price"] + 12, subset["put_chg_oi"],  width=24, color="#00ff88", label="Put ΔOI")
        ax2.set_title("Change in OI", color="#aaa", fontsize=8)
        ax2.legend(fontsize=6, facecolor="#1a1a1a", labelcolor="#ccc", framealpha=0.8)

        fig_oi.tight_layout(pad=1.0)
        st.pyplot(fig_oi, use_container_width=True)
        plt.close(fig_oi)
    else:
        st.markdown(
            '''<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;
            height:180px;display:flex;align-items:center;justify-content:center;
            color:#555;font-size:0.85rem;">
            📊 OI Charts will appear after scanner starts
            </div>''',
            unsafe_allow_html=True
        )

with wall_col:
    def mini_card(title, value, color):
        st.markdown(
            f'''<div class="mini-card">
            <div class="title" style="color:{color};">{title}</div>
            <div class="value">{value}</div>
            </div>''',
            unsafe_allow_html=True
        )

    mini_card("🔴 ACTIVE RES", str(st.session_state["active_res"]), "#e74c3c")
    mini_card("🟢 ACTIVE SUP", str(st.session_state["active_sup"]), "#2ecc71")
    mini_card("⚔️ BATTLEGROUND", str(st.session_state["battle"]),   "#e67e22")

# ── Trend Charts ─────────────────────────────────────
tc1, tc2 = st.columns(2)

def plot_trend(ax, history, title, invert_color=False):
    ax.set_facecolor("#111111")
    ax.tick_params(colors="#888", labelsize=7)
    ax.spines[:].set_color("#2a2a2a")
    ax.grid(True, alpha=0.12, color="#444")
    if history and len(history) >= 2:
        t = [x[0] for x in history]
        v = [x[1] for x in history]
        up   = v[-1] >= v[0]
        col  = ("#e74c3c" if up else "#2ecc71") if invert_color else ("#2ecc71" if up else "#e74c3c")
        ax.plot(t, v, marker="o", color=col, linewidth=2, markersize=3)
        ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
        ax.set_title(title, color="#aaa", fontsize=8)
    else:
        ax.set_title(f"{title} — awaiting data", color="#444", fontsize=8)

with tc1:
    fig_pcr, ax_pcr = plt.subplots(figsize=(5.5, 2.8), dpi=100)
    fig_pcr.patch.set_facecolor("#111111")
    plot_trend(ax_pcr, st.session_state["pcr_history"], "PCR TREND")
    fig_pcr.tight_layout()
    st.pyplot(fig_pcr, use_container_width=True)
    plt.close(fig_pcr)

with tc2:
    fig_vix, ax_vix = plt.subplots(figsize=(5.5, 2.8), dpi=100)
    fig_vix.patch.set_facecolor("#111111")
    plot_trend(ax_vix, st.session_state["vix_history"], "VIX TREND", invert_color=True)
    fig_vix.tight_layout()
    st.pyplot(fig_vix, use_container_width=True)
    plt.close(fig_vix)

# ── AI Advisor Row ────────────────────────────────────
direction = "BULLISH 🐂" if bull_p >= 50 else "BEARISH 🐻"
advisor_col  = "#27ae60" if bull_p >= 50 else "#c0392b"

st.markdown(
    f'''<div style="background:#0a0a0a;border:2px solid #3498db;border-radius:10px;
    padding:14px 24px;display:flex;align-items:center;gap:24px;margin-top:6px;">
    <span style="color:#3498db;font-size:1.2rem;font-weight:800;">🤖 AI ADVISOR</span>
    <span style="color:{advisor_col};font-size:1.1rem;font-weight:700;">
        MARKET OUTLOOK: {direction} — {bull_p:.0f}%
    </span>
    <span style="color:#aaa;font-size:0.85rem;font-style:italic;">
        PCR {st.session_state["pcr"] or "--"} | 
        Res {st.session_state["active_res"]} | 
        Sup {st.session_state["active_sup"]}
    </span>
    </div>''',
    unsafe_allow_html=True
)

# ── Settings sidebar ──────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")
    st.markdown("### Expiry Date")
    exp = st.text_input("Expiry Date (YYYY-MM-DD)", value=EXPIRY_DATE)
    if exp != EXPIRY_DATE:
        EXPIRY_DATE = exp
    st.markdown("---")
    st.markdown("### Telegram")
    st.text_input("Bot Token",  value=TG_BOT_TOKEN, type="password", key="tg_token")
    st.text_input("Chat ID",    value=TG_CHAT_ID,   key="tg_chat")
    st.markdown("---")
    st.markdown("### Refresh Rate")
    REFRESH_RATE = st.slider("Seconds", 3, 30, REFRESH_RATE)
    st.markdown("---")
    st.caption("Nifty Intelligence Terminal v9.1")
    st.caption("Built for Streamlit Cloud / VPS deployment")
