import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from google.oauth2.service_account import Credentials

# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(page_title="가족 자산 대시보드", page_icon="📊", layout="wide")

SHEET_KEY = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
TRADE_SHEET = "거래내역_최종"
FLOW_SHEET = "자금흐름_최종"
MANUAL_SHEET = "수동자산_최종"
HISTORY_SHEET = "History"
KST = ZoneInfo("Asia/Seoul")
GOLD_OZ_TO_G = 31.1034768

# =========================================================
# 공통 함수
# =========================================================
def n(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace(",", ""))
    except Exception:
        return default


def won(v):
    return f"{v:,.0f}원"


def pct(v):
    return f"{v:+.2f}%"


def qty_fmt(v):
    if abs(v) >= 1000:
        return f"{v:,.4f}"
    return f"{v:,.8f}".rstrip("0").rstrip(".")


@st.cache_resource
def get_spreadsheet():
    raw = st.secrets["GCP_JSON"]
    info = json.loads(raw) if isinstance(raw, str) else dict(raw)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_KEY)


@st.cache_data(ttl=120)
def load_sheet_records(sheet_name):
    sh = get_spreadsheet()
    ws = sh.worksheet(sheet_name)
    rows = ws.get_all_records(default_blank="")
    return pd.DataFrame(rows)


@st.cache_data(ttl=900)
def current_usdkrw():
    try:
        t = yf.Ticker("KRW=X")
        fi = t.fast_info
        p = getattr(fi, "last_price", None)
        if p and float(p) > 0:
            return float(p)
    except Exception:
        pass
    try:
        h = yf.Ticker("KRW=X").history(period="5d", auto_adjust=False)
        if not h.empty:
            return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return np.nan


@st.cache_data(ttl=900)
def current_fx(currency):
    c = str(currency or "KRW").upper().strip()
    if c == "KRW":
        return 1.0
    if c in ("USD", "USDT"):
        return current_usdkrw()
    try:
        h = yf.Ticker(f"{c}KRW=X").history(period="5d", auto_adjust=False)
        if not h.empty:
            return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return np.nan


@st.cache_data(ttl=3600)
def historical_fx(currency, date_value):
    c = str(currency or "KRW").upper().strip()
    if c == "KRW":
        return 1.0
    ticker = "KRW=X" if c in ("USD", "USDT") else f"{c}KRW=X"
    try:
        d = pd.to_datetime(date_value).date()
        start = d - timedelta(days=4)
        end = d + timedelta(days=4)
        h = yf.Ticker(ticker).history(start=str(start), end=str(end), auto_adjust=False)
        if not h.empty:
            h = h[h.index.date <= d]
            if h.empty:
                h = yf.Ticker(ticker).history(start=str(d), end=str(end), auto_adjust=False)
            if not h.empty:
                return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return current_fx(c)


@st.cache_data(ttl=900)
def quote(ticker):
    ticker = str(ticker or "").strip()
    if not ticker:
        return np.nan, "", "NO_TICKER"
    try:
        obj = yf.Ticker(ticker)
        try:
            fi = obj.fast_info
            p = getattr(fi, "last_price", None)
            c = getattr(fi, "currency", None)
            if p and float(p) > 0:
                return float(p), str(c or ""), "OK"
        except Exception:
            pass
        h = obj.history(period="5d", auto_adjust=False)
        if not h.empty:
            p = float(h["Close"].dropna().iloc[-1])
            c = ""
            try:
                c = str(obj.info.get("currency", ""))
            except Exception:
                pass
            return p, c, "OK"
    except Exception as e:
        return np.nan, "", f"ERROR:{type(e).__name__}"
    return np.nan, "", "NO_PRICE"


def normalize_date_col(df, col):
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# =========================================================
# 거래내역 -> 현재 보유분 / 실현손익 계산
# =========================================================
def build_positions(trades):
    if trades.empty:
        return pd.DataFrame(), 0.0, []

    df = trades.copy()
    df = normalize_date_col(df, "날짜")
    for col in ["수량", "체결단가", "실제원화금액(KRW)"]:
        if col in df.columns:
            df[col] = df[col].apply(n)
    df = df.sort_values(["날짜"], kind="stable")

    states = {}
    realized_total = 0.0
    realized_rows = []

    for _, r in df.iterrows():
        owner = str(r.get("소유자", "")).strip()
        account = str(r.get("계좌", "")).strip()
        asset = str(r.get("자산", "")).strip()
        ticker = str(r.get("티커", "")).strip()
        ccy = str(r.get("통화", "KRW")).strip().upper()
        typ = str(r.get("구분", "")).strip()
        q = n(r.get("수량"))
        p = n(r.get("체결단가"))
        krw = n(r.get("실제원화금액(KRW)"))
        datev = r.get("날짜")

        if not owner or not asset or q <= 0:
            continue

        key = (owner, account, asset, ticker, ccy)
        s = states.setdefault(key, {
            "소유자": owner,
            "계좌": account,
            "자산": asset,
            "티커": ticker,
            "통화": ccy,
            "보유수량": 0.0,
            "원통화취득원가": 0.0,
            "취득원가(KRW)": 0.0,
            "실현손익(KRW)": 0.0,
            "마지막반영일": pd.NaT,
        })

        native_amount = q * p
        if typ in ("기준잔액", "매수"):
            if krw <= 0:
                if ccy == "KRW":
                    krw = native_amount
                else:
                    fx = historical_fx(ccy, datev)
                    krw = native_amount * fx if np.isfinite(fx) else 0.0
            s["보유수량"] += q
            s["원통화취득원가"] += native_amount
            s["취득원가(KRW)"] += krw
            s["마지막반영일"] = datev

        elif typ == "매도":
            if s["보유수량"] <= 0:
                continue
            sell_q = min(q, s["보유수량"])
            cost_native_per = s["원통화취득원가"] / s["보유수량"] if s["보유수량"] else 0
            cost_krw_per = s["취득원가(KRW)"] / s["보유수량"] if s["보유수량"] else 0
            sold_native_cost = cost_native_per * sell_q
            sold_krw_cost = cost_krw_per * sell_q

            if krw <= 0:
                proceeds_native = sell_q * p
                if ccy == "KRW":
                    krw = proceeds_native
                else:
                    fx = historical_fx(ccy, datev)
                    krw = proceeds_native * fx if np.isfinite(fx) else 0.0

            pnl = krw - sold_krw_cost
            realized_total += pnl
            s["실현손익(KRW)"] += pnl
            s["보유수량"] -= sell_q
            s["원통화취득원가"] -= sold_native_cost
            s["취득원가(KRW)"] -= sold_krw_cost
            s["마지막반영일"] = datev
            realized_rows.append({
                "날짜": datev,
                "소유자": owner,
                "계좌": account,
                "자산": asset,
                "매도수량": sell_q,
                "매도대금(KRW)": krw,
                "매도분취득원가(KRW)": sold_krw_cost,
                "실현손익(KRW)": pnl,
            })

            if abs(s["보유수량"]) < 1e-10:
                s["보유수량"] = 0.0
                s["원통화취득원가"] = 0.0
                s["취득원가(KRW)"] = 0.0

    out = []
    for s in states.values():
        if s["보유수량"] <= 1e-10:
            continue
        qty = s["보유수량"]
        native_cost = s["원통화취득원가"]
        krw_cost = s["취득원가(KRW)"]
        avg_price = native_cost / qty if qty else 0.0
        avg_fx = krw_cost / native_cost if native_cost > 0 and s["통화"] in ("USD", "USDT") else 1.0

        ticker = s["티커"]
        ccy = s["통화"]
        status = "OK"
        current_price = avg_price
        current_fx_rate = current_fx(ccy)

        if ticker == "GC=F":
            # 거래내역의 금 수량은 g, 평균매수가는 KRW/g
            gold_usd_oz, _, qstatus = quote(ticker)
            if np.isfinite(gold_usd_oz) and np.isfinite(current_usdkrw()):
                current_price = gold_usd_oz / GOLD_OZ_TO_G * current_usdkrw()  # KRW/g
                current_fx_rate = 1.0
                current_value = qty * current_price
                price_pnl = current_value - krw_cost
                fx_pnl = 0.0
            else:
                current_value = krw_cost
                price_pnl = 0.0
                fx_pnl = 0.0
                status = qstatus
        elif ticker:
            qp, quote_ccy, qstatus = quote(ticker)
            if np.isfinite(qp):
                current_price = qp
                if ccy in ("USD", "USDT"):
                    current_fx_rate = current_fx(ccy)
                    current_value = qty * current_price * current_fx_rate
                    price_pnl = (qty * current_price - native_cost) * avg_fx
                    fx_pnl = qty * current_price * (current_fx_rate - avg_fx)
                else:
                    current_fx_rate = 1.0
                    current_value = qty * current_price
                    price_pnl = current_value - krw_cost
                    fx_pnl = 0.0
            else:
                current_value = krw_cost
                price_pnl = 0.0
                fx_pnl = 0.0
                status = qstatus
        else:
            # 투자 장부에 티커 없는 자산은 원가 기준 표시
            if ccy in ("USD", "USDT"):
                current_value = native_cost * current_fx_rate
                price_pnl = 0.0
                fx_pnl = current_value - krw_cost
            else:
                current_value = krw_cost
                price_pnl = 0.0
                fx_pnl = 0.0
                current_fx_rate = 1.0

        total_pnl = current_value - krw_cost
        ret = total_pnl / krw_cost * 100 if krw_cost else 0.0
        out.append({
            **s,
            "평균매수가": avg_price,
            "평균취득환율": avg_fx,
            "현재가": current_price,
            "현재환율": current_fx_rate,
            "현재평가액(KRW)": current_value,
            "가격손익(KRW)": price_pnl,
            "환차손익(KRW)": fx_pnl,
            "총미실현손익(KRW)": total_pnl,
            "수익률(%)": ret,
            "상태": status,
        })

    return pd.DataFrame(out), realized_total, realized_rows


# =========================================================
# 수동자산 계산
# =========================================================
def build_manual(manual):
    if manual.empty:
        return pd.DataFrame()
    rows = []
    for _, r in manual.iterrows():
        owner = str(r.get("소유자", "")).strip()
        account = str(r.get("금융사/계좌", "")).strip()
        asset = str(r.get("자산", "")).strip()
        ccy = str(r.get("통화", "KRW")).strip().upper()
        bal = n(r.get("현재잔액(원통화)"))
        cost = n(r.get("취득원가(KRW,선택)"))
        if not owner or not asset:
            continue
        fx = current_fx(ccy)
        if not np.isfinite(fx):
            fx = 1.0 if ccy == "KRW" else np.nan
        value = bal if ccy == "KRW" else bal * fx
        pnl = value - cost if cost > 0 else 0.0
        rows.append({
            "소유자": owner,
            "금융사/계좌": account,
            "자산": asset,
            "통화": ccy,
            "현재잔액(원통화)": bal,
            "현재환율": fx,
            "현재평가액(KRW)": value,
            "취득원가(KRW)": cost,
            "평가손익(KRW)": pnl,
        })
    return pd.DataFrame(rows)


# =========================================================
# 데이터 로드
# =========================================================
st.title("📊 가족 자산 대시보드")
st.caption("입력은 3개 탭만: 거래내역_최종 · 자금흐름_최종 · 수동자산_최종")

try:
    trades = load_sheet_records(TRADE_SHEET)
    flows = load_sheet_records(FLOW_SHEET)
    manual = load_sheet_records(MANUAL_SHEET)
except Exception as e:
    st.error(f"구글시트 연결 오류: {e}")
    st.stop()

positions, realized_total, realized_rows = build_positions(trades)
manual_df = build_manual(manual)

# 자금흐름
if not flows.empty:
    flows = normalize_date_col(flows.copy(), "날짜")
    flows["금액(KRW)"] = flows["금액(KRW)"].apply(n)
else:
    flows = pd.DataFrame(columns=["날짜", "소유자", "구분", "금액(KRW)"])

start_asset = flows.loc[flows["구분"] == "시작자산", "금액(KRW)"].sum()
saved = flows.loc[flows["구분"] == "저축", "금액(KRW)"].sum()
withdrawn = flows.loc[flows["구분"] == "인출", "금액(KRW)"].sum()
net_external = saved - withdrawn

investment_value = positions["현재평가액(KRW)"].sum() if not positions.empty else 0.0
investment_cost = positions["취득원가(KRW)"].sum() if not positions.empty else 0.0
price_pnl = positions["가격손익(KRW)"].sum() if not positions.empty else 0.0
fx_pnl = positions["환차손익(KRW)"].sum() if not positions.empty else 0.0
unrealized = positions["총미실현손익(KRW)"].sum() if not positions.empty else 0.0
manual_value = manual_df["현재평가액(KRW)"].sum() if not manual_df.empty else 0.0
total_assets = investment_value + manual_value
performance_since_start = total_assets - (start_asset + net_external)

# =========================================================
# 상단 핵심 요약 — 메인 화면에는 꼭 필요한 숫자만
# =========================================================
summary = st.columns(4)
summary[0].metric("현재 총자산", won(total_assets))
summary[1].metric(
    "10/1 이후 순수익",
    won(performance_since_start),
    delta=pct(performance_since_start / (start_asset + net_external) * 100)
    if (start_asset + net_external) else None,
    help="현재 총자산 - (10/1 시작자산 + 이후 저축 - 이후 인출)",
)
summary[2].metric(
    "현재 보유 미실현손익",
    won(unrealized),
    delta=pct(unrealized / investment_cost * 100) if investment_cost else None,
    help="현재 보유 투자자산의 평가액 - 취득원가",
)
summary[3].metric(
    "현재 보유 환차손익",
    won(fx_pnl),
    help="USD/USDT 직접투자 자산에서 환율 변화로 발생한 미실현손익",
)

st.caption(
    f"10/1 시작자산 {won(start_asset)} · 이후 순저축 {won(net_external)} · "
    f"현재 보유 가격손익 {won(price_pnl)}"
)

with st.expander("계산 기준 보기", expanded=False):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("10/1 시작자산", won(start_asset))
    c2.metric("이후 순저축", won(net_external))
    c3.metric("새 장부 이후 실현손익", won(realized_total))
    usdkrw_now = current_usdkrw()
    c4.metric("현재 USD/KRW", f"{usdkrw_now:,.2f}" if np.isfinite(usdkrw_now) else "조회 실패")

st.divider()

# =========================================================
# 탭 — 핵심 화면만 남김
# =========================================================
t1, t2, t3, t4 = st.tabs(["현재 보유자산", "수동자산", "자금흐름", "진단"])

with t1:
    st.caption("투자자산의 현재 보유현황입니다. 가격손익과 환차손익을 분리해서 봅니다.")
    if positions.empty:
        st.info("거래내역_최종에 자산을 입력하세요.")
    else:
        raw = positions.copy().sort_values(["소유자", "현재평가액(KRW)"], ascending=[True, False])
        show = raw.copy()
        show["보유수량"] = show["보유수량"].map(qty_fmt)
        for c in ["평균매수가", "현재가", "평균취득환율"]:
            show[c] = show[c].map(lambda x: f"{x:,.4f}" if np.isfinite(x) else "-")
        for c in ["취득원가(KRW)", "현재평가액(KRW)", "가격손익(KRW)", "환차손익(KRW)", "총미실현손익(KRW)"]:
            show[c] = show[c].map(lambda x: f"{x:,.0f}")
        show["수익률(%)"] = show["수익률(%)"].map(lambda x: f"{x:+.2f}%")

        cols = [
            "소유자", "계좌", "자산", "통화", "보유수량",
            "평균매수가", "현재가", "평균취득환율",
            "취득원가(KRW)", "현재평가액(KRW)",
            "가격손익(KRW)", "환차손익(KRW)",
            "총미실현손익(KRW)", "수익률(%)",
        ]

        # 전체 합계 행 — 수량/평균값처럼 합계가 의미 없는 항목은 비움
        total_row = {c: "" for c in cols}
        total_row.update({
            "소유자": "전체",
            "자산": "합계",
            "취득원가(KRW)": f"{investment_cost:,.0f}",
            "현재평가액(KRW)": f"{investment_value:,.0f}",
            "가격손익(KRW)": f"{price_pnl:,.0f}",
            "환차손익(KRW)": f"{fx_pnl:,.0f}",
            "총미실현손익(KRW)": f"{unrealized:,.0f}",
            "수익률(%)": f"{(unrealized / investment_cost * 100 if investment_cost else 0):+.2f}%",
        })
        show_with_total = pd.concat([show[cols], pd.DataFrame([total_row])], ignore_index=True)

        # 모든 행이 가급적 한 번에 보이도록 충분한 높이 확보
        table_height = min(max(120 + len(show_with_total) * 35, 520), 1500)
        st.dataframe(
            show_with_total,
            use_container_width=True,
            hide_index=True,
            height=table_height,
        )

with t2:
    st.caption("CMA·예수금·청약·전세·RP 등은 거래장부가 아니라 '현재 잔액표'입니다. 잔액이 바뀌면 기존 행의 현재잔액만 수정합니다.")
    if manual_df.empty:
        st.info("수동자산_최종이 비어 있습니다.")
    else:
        raw_m = manual_df.copy().sort_values(["소유자", "현재평가액(KRW)"], ascending=[True, False])
        m = raw_m.copy()
        m["현재잔액(원통화)"] = m["현재잔액(원통화)"].map(qty_fmt)
        for c in ["현재평가액(KRW)", "취득원가(KRW)", "평가손익(KRW)"]:
            m[c] = m[c].map(lambda x: f"{x:,.0f}" if abs(x) > 1e-12 else "-")

        manual_cols = [
            "소유자", "금융사/계좌", "자산", "통화",
            "현재잔액(원통화)", "현재평가액(KRW)",
            "취득원가(KRW)", "평가손익(KRW)",
        ]
        manual_cost = raw_m["취득원가(KRW)"].sum()
        manual_pnl = raw_m["평가손익(KRW)"].sum()
        manual_total = {c: "" for c in manual_cols}
        manual_total.update({
            "소유자": "전체",
            "자산": "합계",
            "현재평가액(KRW)": f"{manual_value:,.0f}",
            "취득원가(KRW)": f"{manual_cost:,.0f}" if manual_cost else "-",
            "평가손익(KRW)": f"{manual_pnl:,.0f}" if manual_cost else "-",
        })
        m_with_total = pd.concat([m[manual_cols], pd.DataFrame([manual_total])], ignore_index=True)

        manual_height = min(max(120 + len(m_with_total) * 35, 480), 1300)
        st.dataframe(
            m_with_total,
            use_container_width=True,
            hide_index=True,
            height=manual_height,
        )

with t3:
    st.caption("월급에서 새로 저축한 돈은 '저축', 실제로 자산 밖으로 뺀 돈만 '인출'로 기록합니다. 매수·매도는 여기 기록하지 않습니다.")
    if flows.empty:
        st.info("자금흐름_최종이 비어 있습니다.")
    else:
        f = flows.copy()
        f["날짜"] = f["날짜"].dt.strftime("%Y-%m-%d")
        f["금액(KRW)"] = f["금액(KRW)"].map(lambda x: f"{x:,.0f}")
        flow_height = min(max(120 + len(f) * 35, 300), 900)
        st.dataframe(f, use_container_width=True, hide_index=True, height=flow_height)

    if realized_rows:
        st.subheader("새 장부 이후 실현손익")
        rr = pd.DataFrame(realized_rows)
        rr["날짜"] = pd.to_datetime(rr["날짜"]).dt.strftime("%Y-%m-%d")
        for c in ["매도대금(KRW)", "매도분취득원가(KRW)", "실현손익(KRW)"]:
            rr[c] = rr[c].map(lambda x: f"{x:,.0f}")
        realized_height = min(max(120 + len(rr) * 35, 250), 800)
        st.dataframe(rr, use_container_width=True, hide_index=True, height=realized_height)

with t4:
    st.subheader("데이터 진단")
    st.write("새 구조에서는 현금증감·연결ID·환전 원장·주식모으기 도우미를 사용하지 않습니다.")
    st.write("- 거래내역_최종: 투자자산 기준잔액 / 매수 / 매도")
    st.write("- 자금흐름_최종: 시작자산 / 저축 / 인출")
    st.write("- 수동자산_최종: CMA·예수금·청약·전세·RP 등 현재 잔액")
    st.info("자동매수 종목을 장기간 미반영하면 현재 총자산과 순수익이 실제와 달라집니다. 필요할 때 누적 추가수량·평균단가·실제 원화투입액을 계산해 매수 1행으로 합산 반영하세요.")

    if not positions.empty:
        errors = positions[positions["상태"] != "OK"]
        if not errors.empty:
            st.warning("시세 조회 실패 종목이 있습니다. 해당 종목은 취득원가 기준으로 임시 표시됩니다.")
            st.dataframe(errors[["소유자","자산","티커","상태"]], use_container_width=True, hide_index=True)
        else:
            st.success("시세 조회 상태 정상")

    with st.expander("원본 입력 데이터 보기"):
        st.write("거래내역_최종")
        st.dataframe(trades, use_container_width=True, hide_index=True)
        st.write("자금흐름_최종")
        st.dataframe(flows, use_container_width=True, hide_index=True)
        st.write("수동자산_최종")
        st.dataframe(manual, use_container_width=True, hide_index=True)
