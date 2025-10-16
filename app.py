# AIMe BEAST COMMAND CENTER — Streamlit + Telegram
# Free, fast, and pretty. Auto-refresh + push-to-phone.
# Reads JSON from your Render bot (URLs or local files).
#
# Env vars (set in Streamlit Cloud or Render):
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#   AM_URL        (optional) e.g. https://your-render-app/.../am_runners.json
#   OPEN_URL      (optional) e.g. https://your-render-app/.../open_confirm.json
#   LUNCH_URL     (optional) e.g. https://your-render-app/.../lunch_patterns.json
#   POWER_URL     (optional) e.g. https://your-render-app/.../power_hour.json
#
# If URLs are not set, it will try local files:
#   am_runners.json, open_confirm.json, lunch_patterns.json, power_hour.json

import os, json, time, math, requests, datetime
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# THEME / CSS
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="AIMe BEAST Command Center", layout="wide")
st.markdown("""
<style>
/* Dark neon vibe */
:root { --aime-accent: #7CFF6B; --aime-glow: #FFB800; }
html, body, [data-testid="stAppViewContainer"] {
  background: radial-gradient(1200px 700px at 80% -10%, #151515 10%, #0f0f0f 55%, #0b0b0b 100%) !important;
  color: #EDEDED !important;
}
h1, h2, h3, .st-emotion-cache-10trblm, .st-emotion-cache-1v0mbdj, .stMarkdown {
  color: #EDEDED !important;
}
.block-title {
  font-size: 1.15rem; font-weight: 700; letter-spacing: .5px; margin-bottom: 8px;
  color: #EDEDED; text-transform: uppercase;
}
.badge {
  display:inline-block; padding:4px 10px; border-radius:999px; background:#151515; 
  border:1px solid #2b2b2b; font-size:.8rem; color:#aaa; margin-right:6px;
}
.header-card {
  border:1px solid #2b2b2b; border-radius:16px; padding:16px; background:rgba(18,18,18,.8);
  box-shadow:0 0 24px rgba(124,255,107,0.05), inset 0 0 0 1px rgba(255,255,255,.02);
}
.highlight {
  color: var(--aime-accent);
  text-shadow: 0 0 12px rgba(124,255,107,.35);
}
.btn-push {
  background: linear-gradient(90deg, #7CFF6B 0%, #FFB800 100%);
  color:#111; font-weight:800; padding:10px 16px; border-radius:12px; border:none;
}
.smallnote { color:#9a9a9a; font-size:.85rem; }
.dataframe td, .dataframe th { color:#EDEDED !important; border-color:#2b2b2b !important; }
a { color:#8bd5ff !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_json(source: str):
    """Load JSON from URL or local filepath. Returns list/dict or [] on failure."""
    if not source:
        return []
    try:
        if source.lower().startswith("http"):
            r = requests.get(source, timeout=12)
            if r.status_code != 200:
                return []
            return r.json()
        # local file
        if os.path.exists(source):
            with open(source, "r") as f:
                return json.load(f)
        return []
    except Exception:
        return []

def to_df(records, scan_tag):
    """Normalize records -> DataFrame with consistent columns."""
    if not records: 
        return pd.DataFrame(columns=["scan","symbol","score","type","price","pct","vol","dir","vwap","pos","momo"])
    rows = []
    for r in records:
        # Accept both your AM format and pattern/power formats gracefully
        rows.append({
            "scan": scan_tag,
            "symbol": r.get("symbol") or r.get("ticker") or "",
            "score": r.get("score"),
            "type": r.get("setup") or r.get("type") or scan_tag,
            "price": r.get("price") or r.get("current_price"),
            "pct": r.get("pct") or r.get("gain_pct"),
            "vol": r.get("vol") or r.get("latest_volume"),
            "dir": r.get("dir"),
            "vwap": r.get("vwap"),
            "pos": r.get("pos") or r.get("position"),
            "momo": r.get("mom_pct") or r.get("momo15")
        })
    df = pd.DataFrame(rows)
    # Sort by score desc, then pct, volume
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["pct"]   = pd.to_numeric(df["pct"], errors="coerce")
    df["vol"]   = pd.to_numeric(df["vol"], errors="coerce")
    df = df.sort_values(by=["score","pct","vol"], ascending=False, na_position="last")
    return df

def extract_options(records):
    """Look for option suggestion blocks from your bot (if you choose to save them)."""
    out = []
    for r in records or []:
        # If you saved options info inline per symbol
        rec = r.get("options") or None
        if rec and isinstance(rec, dict):
            out.append({
                "symbol": r.get("symbol",""),
                "type": rec.get("type"),
                "options_ticker": rec.get("options_ticker"),
                "strike": rec.get("strike"),
                "expiration": rec.get("expiration"),
                "bid": rec.get("bid"),
                "ask": rec.get("ask"),
                "mid": rec.get("mid"),
                "buy_min": rec.get("buy_min"),
                "buy_max": rec.get("buy_max"),
                "target": rec.get("target"),
                "stop": rec.get("stop"),
            })
    return pd.DataFrame(out)

def next_scan_times_et():
    tz = datetime.timezone(datetime.timedelta(hours=-4))  # naive ET approx; Streamlit Cloud often UTC; this is display-only
    now = datetime.datetime.now(tz)
    targets = [
        ("08:00", "Premarket"),
        ("10:00", "Open Confirm"),
        ("13:45", "Midday Pattern"),
        ("15:15", "Power Hour"),
    ]
    out = []
    for hhmm, label in targets:
        h, m = map(int, hhmm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t < now: t = t + datetime.timedelta(days=1)
        delta = t - now
        out.append((label, hhmm, str(delta).split(".")[0]))
    return out

def human_int(x):
    try:
        x = float(x)
        if x >= 1_000_000: return f"{x/1_000_000:.1f}M"
        if x >= 1_000:     return f"{x/1_000:.1f}k"
        return f"{int(x)}"
    except Exception:
        return str(x)

def open_tv_link(symbol: str):
    return f"https://www.tradingview.com/chart/?symbol={symbol.upper()}"

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR / CONFIG
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Config")
    AM_URL    = os.getenv("AM_URL", "am_runners.json")
    OPEN_URL  = os.getenv("OPEN_URL", "open_confirm.json")
    LUNCH_URL = os.getenv("LUNCH_URL", "lunch_patterns.json")
    POWER_URL = os.getenv("POWER_URL", "power_hour.json")

    AM_URL    = st.text_input("8:00 AM JSON", AM_URL)
    OPEN_URL  = st.text_input("10:00 AM JSON", OPEN_URL)
    LUNCH_URL = st.text_input("1:45 PM JSON", LUNCH_URL)
    POWER_URL = st.text_input("3:15 PM JSON", POWER_URL)

    st.markdown("---")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_BOT_TOKEN = st.text_input("Telegram Bot Token", TELEGRAM_BOT_TOKEN, type="password")
    TELEGRAM_CHAT_ID   = st.text_input("Telegram Chat ID", TELEGRAM_CHAT_ID)

    st.markdown("---")
    max_rows = st.slider("Rows to show (ranked)", 50, 300, 200, 10)
    auto_refresh = st.checkbox("Auto-refresh every 30 sec", value=True)

# ────────────────────────────────────────────────────────────────
# AUTO-REFRESH CONTROL (safe version)
# ────────────────────────────────────────────────────────────────
import time

# Manual refresh button — safe for Streamlit Cloud
if st.button("🔄 Refresh Dashboard"):
     st.query_params(ts=int(time.time()))  # refresh URL
     st.rerun()

# Optional: time-based auto-refresh every 5 minutes (adjust as needed)
refresh_rate = 300  # seconds (set to 0 to disable)
if refresh_rate > 0:
    st.caption(f"⏱ Auto-refreshing every {refresh_rate//60} min.")
    time.sleep(refresh_rate)
    st.query_params(ts=int(time.time()))
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h1>AIMe <span class='highlight'>BEAST</span> Command Center</h1>", unsafe_allow_html=True)

colA, colB, colC, colD = st.columns([2,2,2,2])
with colA:
    st.markdown("<div class='header-card'><div class='block-title'>Status</div>"
                "<span class='badge'>Render: ✅</span> <span class='badge'>Discord: ✅</span> <span class='badge'>Telegram: ✅</span>"
                "</div>", unsafe_allow_html=True)
with colB:
    times = next_scan_times_et()
    upcoming = "".join([f"<div class='badge'>{lbl}: {hhmm} (in {left})</div>" for (lbl,hhmm,left) in times])
    st.markdown(f"<div class='header-card'><div class='block-title'>Next Scans (ET)</div>{upcoming}</div>", unsafe_allow_html=True)
with colC:
    st.markdown("<div class='header-card'><div class='block-title'>Mode</div>"
                "<span class='badge'>8:00 Squeeze</span><span class='badge'>10:00 VWAP</span>"
                "<span class='badge'>13:45 Pattern</span><span class='badge'>15:15 Momentum</span></div>", unsafe_allow_html=True)
with colD:
    st.markdown("<div class='header-card'><div class='block-title'>Phone Push</div>"
                "<span class='smallnote'>Push top picks to Telegram</span></div>", unsafe_allow_html=True)

st.markdown("")

# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA (all scans)
# ─────────────────────────────────────────────────────────────────────────────
am    = fetch_json(AM_URL)
openC = fetch_json(OPEN_URL)
lunch = fetch_json(LUNCH_URL)
power = fetch_json(POWER_URL)

df_am    = to_df(am, "08:00 Squeeze")
df_open  = to_df(openC, "10:00 Confirm")
df_lunch = to_df(lunch, "13:45 Pattern")
df_power = to_df(power, "15:15 Power")

df_all = pd.concat([df_am, df_open, df_lunch, df_power], ignore_index=True)
df_all = df_all.head(max_rows)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN TABS
# ─────────────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🔥 Ranked Tickers", "🎰 Options Picks", "📰 Headlines (optional)"])

with tab1:
    st.markdown("<div class='block-title'>Top Ranked (across scans)</div>", unsafe_allow_html=True)
    if df_all.empty:
        st.info("No JSON found yet. Once your bot writes the files, tickers show up here automatically.")
    else:
        # Pretty display
        show = df_all.copy()
        show["symbol"] = show["symbol"].apply(lambda s: f"[{s}]({open_tv_link(s)})" if isinstance(s,str) else s)
        show["vol"]    = show["vol"].apply(human_int)
        show.rename(columns={"scan":"Scan","symbol":"Symbol","type":"Type","score":"Score",
                             "price":"Price","pct":"Δ%","vol":"Vol","dir":"Dir","vwap":"VWAP",
                             "pos":"Pos","momo":"Mom%"},
                    inplace=True)
        st.dataframe(show, use_container_width=True, height=540)

    # Telegram push
    st.markdown("### 🔔 Push Top N to Telegram")
    push_n = st.slider("How many? (top ranked from this table)", 5, 50, 20, 5)
    if st.button("Send to Telegram", help="Sends a compact list of top picks to your phone."):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            st.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        elif df_all.empty:
            st.warning("Nothing to send yet.")
        else:
            text_lines = []
            for _, r in df_all.head(push_n).iterrows():
                sym = r.get("symbol","")
                score = r.get("score","")
                price = r.get("price","")
                scan = r.get("scan","")
                d = r.get("dir","") or ""
                pct = r.get("pct","")
                text_lines.append(f"{sym} ${price} {('['+d+']') if d else ''}  Score:{score}  Δ%:{pct}  ({scan})")
            msg = "AIMe BEAST — Top Picks\n" + "\n".join(text_lines)
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                resp = requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
                if resp.status_code == 200:
                    st.success("Sent to Telegram ✅")
                else:
                    st.error(f"Telegram error: {resp.status_code} — {resp.text[:200]}")
            except Exception as e:
                st.error(f"Telegram send failed: {e}")

with tab2:
    st.markdown("<div class='block-title'>Options Picks (if saved by your bot)</div>", unsafe_allow_html=True)
    # If you later decide to save options inside each JSON record as r['options'] dict,
    # this will render them here nicely.
    options_frames = []
    for tag, recs in [("08:00", am), ("10:00", openC), ("13:45", lunch), ("15:15", power)]:
        df_opt = extract_options(recs)
        if not df_opt.empty:
            df_opt["scan"] = tag
            options_frames.append(df_opt)
    if options_frames:
        opts = pd.concat(options_frames, ignore_index=True)
        opts = opts[["scan","symbol","type","options_ticker","strike","expiration","mid","bid","ask","buy_min","buy_max","target","stop"]]
        st.dataframe(opts, use_container_width=True, height=540)
    else:
        st.info("Your bot hasn’t saved per-ticker options data into JSON yet. (Ticker alerts still go to Discord.)")

with tab3:
    st.markdown("<div class='block-title'>Headlines</div><div class='smallnote'>If you export Marketaux headlines to JSON, list them here for context.</div>", unsafe_allow_html=True)
    st.info("Optional: point a JSON of headlines per symbol to display here.")

st.markdown("<br>", unsafe_allow_html=True)
st.caption("AIMe • Neon Mode • Built for 24/7 desk presence.")
