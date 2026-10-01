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
# 0. 기본 설정
# =========================================================
SHEET_KEY = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
TRADE_SHEET = "거래내역"
FLOW_SHEET = "자금흐름"
KST = ZoneInfo("Asia/Seoul")
TROY_OUNCE_TO_GRAM = 31.1034768
EPS = 1e-10

st.set_page_config(
    page_title="우리가족 자산 대시보드",
    page_icon="👨‍👩‍👧",
    layout="wide",
)

st.title("👨‍👩‍👧 우리가족 자산 대시보드")

c1, c2 = st.columns([1, 6])
with c1:
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.rerun()
with c2:
    st.caption(
        "거래내역은 장부로만 사용합니다. 기준잔액·매수·매도 행을 시간순으로 읽어 "
        "현재 보유수량, 평균원가, 미실현손익, 실현손익을 앱에서 계산합니다."
    )

st.divider()

# =========================================================
# 1. 공통 유틸
# =========================================================
def clean_text(v):
    if v is None:
        return ""
    return str(v).strip()


def to_num(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (int, float, np.number)):
        try:
            f = float(v)
            return f if np.isfinite(f) else default
        except Exception:
            return default
    s = str(v).strip()
    if not s:
        return default
    for ch in [",", "₩", "$", "¥", "€", "%"]:
        s = s.replace(ch, "")
    try:
        f = float(s)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def is_num(v):
    try:
        return np.isfinite(float(v))
    except Exception:
        return False


def won(v):
    return "-" if not is_num(v) else f"{float(v):,.0f}원"


def money(v, ccy):
    if not is_num(v):
        return "-"
    ccy = clean_text(ccy).upper()
    v = float(v)
    if ccy == "KRW":
        return f"{v:,.0f}원"
    if ccy in {"USD", "USDT"}:
        return f"${v:,.2f}"
    return f"{v:,.4f} {ccy}"


def pct(v):
    return "-" if not is_num(v) else f"{float(v):+.2f}%"


def parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return datetime.now(KST).date()


def is_cash_like(name, ticker):
    """예수금/CMA는 거래 후 현금증감을 반영하기 위한 현금 계정으로 처리."""
    name = clean_text(name)
    ticker = clean_text(ticker)
    if ticker:
        return False
    return any(k in name for k in ["예수금", "CMA"])


def asset_key(row):
    ticker = clean_text(row.get("티커", ""))
    name = clean_text(row.get("자산/종목명", ""))
    code = ticker if ticker else name
    return (
        clean_text(row.get("소유자", "")),
        clean_text(row.get("금융사/거래소", "")),
        clean_text(row.get("계좌구분", "")),
        code,
        clean_text(row.get("거래통화", "")).upper(),
    )


def cash_key(owner, institution, account, currency):
    return (
        clean_text(owner),
        clean_text(institution),
        clean_text(account) or "일반",
        clean_text(currency).upper() or "KRW",
    )


# =========================================================
# 2. Google Sheets
# =========================================================
def get_spreadsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    secret = json.loads(st.secrets["GCP_JSON"])
    creds = Credentials.from_service_account_info(secret, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_KEY)


@st.cache_data(ttl=300, show_spinner=False)
def load_sheet(sheet_name):
    ws = get_spreadsheet().worksheet(sheet_name)
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame()
    headers = rows[0]
    body = rows[1:]
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in body]
    return pd.DataFrame(body, columns=headers)


def load_trades():
    df = load_sheet(TRADE_SHEET).copy()
    required = [
        "거래일", "소유자", "금융사/거래소", "계좌구분", "거래유형",
        "자산/종목명", "티커", "거래통화", "수량", "체결단가",
        "거래금액", "수수료", "세금", "적용환율(자동)",
        "실제원화금액(KRW)", "현금증감(거래통화)",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("거래내역 시트에 필요한 열이 없습니다: " + ", ".join(missing))

    df = df[df["거래유형"].astype(str).str.strip() != ""].copy()
    df["_row"] = np.arange(2, len(df) + 2)
    df["거래일_dt"] = pd.to_datetime(df["거래일"], errors="coerce")
    df["거래일_dt"] = df["거래일_dt"].fillna(pd.Timestamp("2099-12-31"))
    df = df.sort_values(["거래일_dt", "_row"], kind="stable").reset_index(drop=True)

    for c in ["수량", "체결단가", "거래금액", "수수료", "세금", "적용환율(자동)", "실제원화금액(KRW)", "현금증감(거래통화)"]:
        df[c] = df[c].map(to_num)
    for c in ["소유자", "금융사/거래소", "계좌구분", "거래유형", "자산/종목명", "티커", "거래통화"]:
        df[c] = df[c].map(clean_text)
    return df


def load_flows():
    df = load_sheet(FLOW_SHEET).copy()
    if df.empty:
        return pd.DataFrame(columns=["날짜", "소유자", "금융사/계좌", "구분", "금액(KRW)"])
    required = ["날짜", "소유자", "금융사/계좌", "구분", "금액(KRW)"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("자금흐름 시트에 필요한 열이 없습니다: " + ", ".join(missing))
    df = df[df["구분"].astype(str).str.strip() != ""].copy()
    df["금액(KRW)"] = df["금액(KRW)"].map(to_num)
    for c in ["소유자", "금융사/계좌", "구분"]:
        df[c] = df[c].map(clean_text)
    return df


# =========================================================
# 3. 시세 / 환율
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def market_quote(ticker):
    ticker = clean_text(ticker)
    if not ticker:
        return np.nan, "", "NO_TICKER"

    try:
        obj = yf.Ticker(ticker)
        price = np.nan
        currency = ""

        try:
            fi = obj.fast_info
            p = getattr(fi, "last_price", None)
            if p is None:
                try:
                    p = fi.get("last_price")
                except Exception:
                    p = None
            if is_num(p) and float(p) > 0:
                price = float(p)

            c = getattr(fi, "currency", None)
            if c is None:
                try:
                    c = fi.get("currency")
                except Exception:
                    c = None
            currency = clean_text(c).upper()
        except Exception:
            pass

        if not is_num(price) or price <= 0:
            hist = obj.history(period="5d", interval="1d", auto_adjust=False)
            if not hist.empty and "Close" in hist.columns:
                closes = hist["Close"].dropna()
                if not closes.empty:
                    price = float(closes.iloc[-1])

        if not currency:
            try:
                md = obj.get_history_metadata()
                currency = clean_text(md.get("currency", "")).upper()
            except Exception:
                pass

        if not is_num(price) or price <= 0:
            return np.nan, currency, "PRICE_ERROR"
        return float(price), currency, "OK"
    except Exception as e:
        return np.nan, "", f"ERROR:{type(e).__name__}"


@st.cache_data(ttl=600, show_spinner=False)
def current_fx_to_krw(currency):
    ccy = clean_text(currency).upper()
    if ccy in {"", "KRW"}:
        return 1.0
    if ccy == "USDT":
        ccy = "USD"

    if ccy == "USD":
        p, _, s = market_quote("KRW=X")
        return float(p) if s == "OK" and is_num(p) else np.nan

    p, _, s = market_quote(f"{ccy}KRW=X")
    if s == "OK" and is_num(p) and p > 0:
        return float(p)

    usdkrw = current_fx_to_krw("USD")
    p2, _, s2 = market_quote(f"{ccy}USD=X")
    if is_num(usdkrw) and s2 == "OK" and is_num(p2) and p2 > 0:
        return float(p2) * float(usdkrw)
    return np.nan


@st.cache_data(ttl=3600, show_spinner=False)
def historical_fx_to_krw(currency, date_text):
    ccy = clean_text(currency).upper()
    if ccy in {"", "KRW"}:
        return 1.0
    if ccy == "USDT":
        ccy = "USD"

    d = parse_date(date_text)
    start = d - timedelta(days=5)
    end = d + timedelta(days=5)

    try:
        if ccy == "USD":
            ticker = "KRW=X"
            hist = yf.Ticker(ticker).history(
                start=start.isoformat(), end=end.isoformat(), auto_adjust=False
            )
            if not hist.empty:
                before = hist[hist.index.date <= d]
                src = before if not before.empty else hist
                return float(src["Close"].dropna().iloc[-1])

        ticker = f"{ccy}KRW=X"
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=False
        )
        if not hist.empty:
            before = hist[hist.index.date <= d]
            src = before if not before.empty else hist
            return float(src["Close"].dropna().iloc[-1])
    except Exception:
        pass

    return current_fx_to_krw(ccy)


# =========================================================
# 4. 자금흐름 → 성과 기준 / 현금 보정
# =========================================================
def benchmark_from_flows(flows):
    if flows.empty:
        return 0.0, 0.0, 0.0
    base = flows.loc[flows["구분"] == "성과기준자산", "금액(KRW)"].sum()
    deposits = flows.loc[flows["구분"] == "외부입금", "금액(KRW)"].sum()
    withdrawals = flows.loc[flows["구분"] == "외부출금", "금액(KRW)"].sum()
    return float(base), float(deposits), float(withdrawals)


def parse_flow_target(text):
    s = clean_text(text)
    if not s or s == "전체자산":
        return "", ""
    account = "일반"
    if "ISA" in s.upper():
        account = "ISA"
    elif "연금" in s:
        account = "연금저축"
    elif "IRP" in s.upper():
        account = "IRP"

    institution = s
    for token in [" 일반", " ISA", " 연금저축", " 연금", " IRP"]:
        institution = institution.replace(token, "")
    return institution.strip(), account


# =========================================================
# 5. 거래 원장 계산 엔진 (이동평균)
# =========================================================
def calculate_portfolio(trades, flows):
    states = {}
    cash = {}
    realized_rows = []
    income_rows = []
    warnings = []

    # 5-1. 거래 처리
    for _, r in trades.iterrows():
        tx_type = clean_text(r["거래유형"])
        owner = clean_text(r["소유자"])
        inst = clean_text(r["금융사/거래소"])
        account = clean_text(r["계좌구분"]) or "일반"
        name = clean_text(r["자산/종목명"])
        ticker = clean_text(r["티커"])
        ccy = clean_text(r["거래통화"]).upper() or "KRW"
        qty = to_num(r["수량"])
        px = to_num(r["체결단가"])
        trade_amt = to_num(r["거래금액"])
        if trade_amt <= 0 and qty > 0 and px > 0:
            trade_amt = qty * px
        fee = to_num(r["수수료"])
        tax = to_num(r["세금"])
        fx = to_num(r["적용환율(자동)"])
        actual_krw = to_num(r["실제원화금액(KRW)"])
        tx_date = r["거래일"]

        if fx <= 0:
            fx = 1.0 if ccy == "KRW" else historical_fx_to_krw(ccy, tx_date)
        if not is_num(fx) or fx <= 0:
            fx = 1.0 if ccy == "KRW" else np.nan

        # 실제원화금액은 '거래금액 자체의 원화환산액(수수료/세금 제외)'으로 사용
        if actual_krw <= 0:
            if ccy == "KRW":
                actual_krw = trade_amt
            elif is_num(fx):
                actual_krw = trade_amt * fx

        k = asset_key(r)

        # 현금성 기준잔액: 예수금/CMA는 별도 현금 풀로 시작
        if is_cash_like(name, ticker):
            ck = cash_key(owner, inst, account, ccy)
            if tx_type == "기준잔액":
                if ccy == "KRW":
                    balance = actual_krw if actual_krw > 0 else trade_amt
                else:
                    balance = trade_amt
                cash[ck] = cash.get(ck, 0.0) + balance
            elif tx_type in {"매수", "입고"}:
                cash[ck] = cash.get(ck, 0.0) + trade_amt
            elif tx_type in {"매도", "출고"}:
                cash[ck] = cash.get(ck, 0.0) - trade_amt
            continue

        if k not in states:
            states[k] = {
                "owner": owner,
                "institution": inst,
                "account": account,
                "name": name,
                "ticker": ticker,
                "ccy": ccy,
                "qty": 0.0,
                "native_cost": 0.0,
                "krw_cost": 0.0,
                "realized_krw": 0.0,
            }
        s = states[k]

        fee_tax_native = fee + tax
        fee_tax_krw = fee_tax_native * fx if is_num(fx) else 0.0

        if tx_type in {"기준잔액", "매수", "입고"}:
            if qty <= 0:
                continue

            # 기준잔액은 이미 O열에 실제 역사적 원화원가가 들어 있음.
            # 일반 매수는 O열 거래원화금액 + 수수료/세금을 취득원가에 포함.
            native_add = trade_amt + fee_tax_native
            krw_add = actual_krw + fee_tax_krw

            s["qty"] += qty
            s["native_cost"] += native_add
            s["krw_cost"] += krw_add

            # 기준잔액은 과거 거래이므로 현금 증감 없음
            if tx_type != "기준잔액":
                if ccy == "KRW":
                    ck = cash_key(owner, inst, account, "KRW")
                    cash[ck] = cash.get(ck, 0.0) - krw_add
                elif to_num(r["실제원화금액(KRW)"]) > 0:
                    # 실제 원화금액을 넣었다면 원화주문/자동환전으로 간주
                    ck = cash_key(owner, inst, account, "KRW")
                    cash[ck] = cash.get(ck, 0.0) - krw_add
                else:
                    ck = cash_key(owner, inst, account, ccy)
                    cash[ck] = cash.get(ck, 0.0) - native_add

        elif tx_type in {"매도", "출고"}:
            if qty <= 0:
                continue
            if s["qty"] <= EPS:
                warnings.append(f"{owner} {name}: 매도할 보유수량이 없습니다. (시트 행 {int(r['_row'])})")
                continue

            sell_qty = qty
            if sell_qty > s["qty"] + 1e-8:
                warnings.append(
                    f"{owner} {name}: 매도수량 {sell_qty:g}이 계산상 보유수량 {s['qty']:g}보다 큽니다. "
                    f"(시트 행 {int(r['_row'])})"
                )
                sell_qty = s["qty"]

            avg_native_cost = s["native_cost"] / s["qty"] if s["qty"] > EPS else 0.0
            avg_krw_cost = s["krw_cost"] / s["qty"] if s["qty"] > EPS else 0.0
            sold_native_cost = avg_native_cost * sell_qty
            sold_krw_cost = avg_krw_cost * sell_qty

            gross_krw = actual_krw if actual_krw > 0 else (trade_amt * fx if is_num(fx) else 0.0)
            net_krw = gross_krw - fee_tax_krw
            realized = net_krw - sold_krw_cost

            s["qty"] -= sell_qty
            s["native_cost"] -= sold_native_cost
            s["krw_cost"] -= sold_krw_cost
            s["realized_krw"] += realized

            if abs(s["qty"]) < EPS:
                s["qty"] = 0.0
                s["native_cost"] = 0.0
                s["krw_cost"] = 0.0

            realized_rows.append({
                "거래일": clean_text(tx_date),
                "소유자": owner,
                "자산": name,
                "매도수량": sell_qty,
                "매도단가": px,
                "매도금액(원통화)": trade_amt,
                "매도분취득원가(KRW)": sold_krw_cost,
                "순매도대금(KRW)": net_krw,
                "실현손익(KRW)": realized,
            })

            if ccy == "KRW":
                ck = cash_key(owner, inst, account, "KRW")
                cash[ck] = cash.get(ck, 0.0) + net_krw
            elif to_num(r["실제원화금액(KRW)"]) > 0:
                ck = cash_key(owner, inst, account, "KRW")
                cash[ck] = cash.get(ck, 0.0) + net_krw
            else:
                ck = cash_key(owner, inst, account, ccy)
                cash[ck] = cash.get(ck, 0.0) + max(trade_amt - fee_tax_native, 0.0)

        elif tx_type in {"배당", "이자"}:
            gross_krw = actual_krw if actual_krw > 0 else (trade_amt * fx if is_num(fx) else 0.0)
            net_krw = gross_krw - fee_tax_krw
            income_rows.append({
                "거래일": clean_text(tx_date),
                "소유자": owner,
                "자산": name,
                "유형": tx_type,
                "금액(KRW)": net_krw,
            })
            if ccy == "KRW" or to_num(r["실제원화금액(KRW)"]) > 0:
                ck = cash_key(owner, inst, account, "KRW")
                cash[ck] = cash.get(ck, 0.0) + net_krw
            else:
                ck = cash_key(owner, inst, account, ccy)
                cash[ck] = cash.get(ck, 0.0) + max(trade_amt - fee_tax_native, 0.0)

    # 5-2. 외부 입출금은 현금에 반영
    if not flows.empty:
        for _, r in flows.iterrows():
            typ = clean_text(r["구분"])
            if typ not in {"외부입금", "외부출금"}:
                continue
            owner = clean_text(r["소유자"])
            amount = to_num(r["금액(KRW)"])
            inst, account = parse_flow_target(r["금융사/계좌"])
            if not inst:
                # 대상 계좌가 명확하지 않으면 자산 총액에는 넣되 '미배정 KRW'로 표시
                inst = "미배정 외부자금"
                account = "일반"
            ck = cash_key(owner, inst, account, "KRW")
            delta = amount if typ == "외부입금" else -amount
            cash[ck] = cash.get(ck, 0.0) + delta

    return states, cash, pd.DataFrame(realized_rows), pd.DataFrame(income_rows), warnings


# =========================================================
# 6. 현재 평가
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def evaluate_current(states_serialized, cash_serialized):
    states = states_serialized
    cash = cash_serialized
    out = []
    quote_errors = []

    for _, s in states.items():
        qty = float(s["qty"])
        if qty <= EPS:
            continue

        owner = s["owner"]
        inst = s["institution"]
        account = s["account"]
        name = s["name"]
        ticker = s["ticker"]
        trade_ccy = s["ccy"]
        native_cost = float(s["native_cost"])
        krw_cost = float(s["krw_cost"])
        avg_native = native_cost / qty if qty > EPS else np.nan
        avg_fx = krw_cost / native_cost if native_cost > EPS and trade_ccy != "KRW" else 1.0

        current_price = np.nan
        current_krw = np.nan
        quote_ccy = trade_ccy
        status = "OK"

        if ticker:
            p, qccy, status = market_quote(ticker)
            if status == "OK":
                if ticker == "GC=F":
                    # 보유수량은 gram, Yahoo GC=F는 USD/troy oz
                    current_price = p / TROY_OUNCE_TO_GRAM
                    quote_ccy = "USD"
                else:
                    current_price = p
                    quote_ccy = qccy or trade_ccy

                fx_now = current_fx_to_krw(quote_ccy)
                if is_num(fx_now):
                    current_krw = qty * current_price * fx_now
                else:
                    status = "FX_ERROR"
        else:
            # 티커 없는 자산: 원화 고정자산은 역사원가를 현재가로, 외화 RP 등은 수량×현재환율
            if trade_ccy == "KRW":
                current_price = avg_native
                current_krw = krw_cost
                quote_ccy = "KRW"
            else:
                current_price = 1.0
                quote_ccy = trade_ccy
                fx_now = current_fx_to_krw(trade_ccy)
                if is_num(fx_now):
                    current_krw = qty * fx_now
                else:
                    status = "FX_ERROR"

        if not is_num(current_krw):
            quote_errors.append(f"{owner} / {name}: {status}")
            current_krw = 0.0

        unrealized = current_krw - krw_cost
        ret = unrealized / krw_cost * 100 if krw_cost > EPS else np.nan

        out.append({
            "소유자": owner,
            "금융사": inst,
            "계좌": account,
            "자산": name,
            "티커": ticker,
            "거래통화": trade_ccy,
            "보유수량": qty,
            "평균매수가": avg_native,
            "평균취득환율": avg_fx,
            "현재가": current_price,
            "시세통화": quote_ccy,
            "취득원가(KRW)": krw_cost,
            "현재평가액(KRW)": current_krw,
            "미실현손익(KRW)": unrealized,
            "수익률(%)": ret,
            "상태": status,
        })

    # 현금 풀
    for ck, balance in cash.items():
        owner, inst, account, ccy = ck
        if abs(balance) < 0.5:
            continue
        fx = current_fx_to_krw(ccy)
        current_krw = balance * fx if is_num(fx) else 0.0
        out.append({
            "소유자": owner,
            "금융사": inst,
            "계좌": account,
            "자산": f"현금/예수금 ({ccy})",
            "티커": "",
            "거래통화": ccy,
            "보유수량": balance,
            "평균매수가": 1.0,
            "평균취득환율": fx if ccy != "KRW" else 1.0,
            "현재가": 1.0,
            "시세통화": ccy,
            "취득원가(KRW)": current_krw,
            "현재평가액(KRW)": current_krw,
            "미실현손익(KRW)": 0.0,
            "수익률(%)": 0.0,
            "상태": "CASH",
        })

    return pd.DataFrame(out), quote_errors


# =========================================================
# 7. 실행
# =========================================================
try:
    trades = load_trades()
    flows = load_flows()
    states, cash, realized_df, income_df, calc_warnings = calculate_portfolio(trades, flows)

    # cache-friendly plain dict
    holdings_df, quote_errors = evaluate_current(states, cash)
except Exception as e:
    st.error(f"데이터를 불러오거나 계산하는 중 오류가 발생했습니다: {type(e).__name__}: {e}")
    st.stop()

if holdings_df.empty:
    st.warning("현재 계산되는 보유자산이 없습니다.")
    st.stop()

# =========================================================
# 8. 상단 지표
# =========================================================
base, deposits, withdrawals = benchmark_from_flows(flows)
benchmark = base + deposits - withdrawals
current_total = float(holdings_df["현재평가액(KRW)"].sum())
current_cost = float(holdings_df.loc[holdings_df["상태"] != "CASH", "취득원가(KRW)"].sum())
unrealized_total = float(holdings_df["미실현손익(KRW)"].sum())
realized_total = float(realized_df["실현손익(KRW)"].sum()) if not realized_df.empty else 0.0
income_total = float(income_df["금액(KRW)"].sum()) if not income_df.empty else 0.0
since_start = current_total - benchmark if benchmark > 0 else np.nan
since_start_pct = since_start / benchmark * 100 if benchmark > 0 else np.nan

m1, m2, m3, m4 = st.columns(4)
m1.metric("현재 총자산", won(current_total))
m2.metric("현재 보유분 취득원가", won(current_cost))
m3.metric("미실현손익", won(unrealized_total), pct(unrealized_total / current_cost * 100) if current_cost > 0 else None)
m4.metric("누적 실현손익", won(realized_total))

m5, m6, m7, m8 = st.columns(4)
m5.metric("성과기준자산", won(base))
m6.metric("이후 순외부투입", won(deposits - withdrawals))
m7.metric("기준일 이후 총성과", won(since_start), pct(since_start_pct) if is_num(since_start_pct) else None)
m8.metric("배당·이자", won(income_total))

st.caption(
    "※ '기준일 이후 총성과'는 자금흐름 탭의 성과기준자산 + 이후 외부입출금과 현재 총자산을 비교합니다. "
    "외부입금이 아직 특정 계좌에 배정되지 않았다면 앱에서는 '미배정 외부자금' 현금으로 표시합니다."
)

if calc_warnings or quote_errors:
    with st.expander("⚠️ 확인이 필요한 항목", expanded=False):
        for msg in calc_warnings + quote_errors:
            st.write("-", msg)

st.divider()

# =========================================================
# 9. 소유자 요약
# =========================================================
st.subheader("👥 소유자별 요약")
owner_summary = (
    holdings_df.groupby("소유자", dropna=False)
    .agg(
        현재자산=("현재평가액(KRW)", "sum"),
        취득원가=("취득원가(KRW)", "sum"),
        미실현손익=("미실현손익(KRW)", "sum"),
    )
    .reset_index()
)
owner_realized = realized_df.groupby("소유자")["실현손익(KRW)"].sum() if not realized_df.empty else pd.Series(dtype=float)
owner_summary["실현손익"] = owner_summary["소유자"].map(owner_realized).fillna(0.0)
owner_summary["미실현수익률"] = np.where(
    owner_summary["취득원가"] > 0,
    owner_summary["미실현손익"] / owner_summary["취득원가"] * 100,
    np.nan,
)
owner_show = owner_summary.copy()
for c in ["현재자산", "취득원가", "미실현손익", "실현손익"]:
    owner_show[c] = owner_show[c].map(lambda x: f"{x:,.0f}")
owner_show["미실현수익률"] = owner_summary["미실현수익률"].map(lambda x: f"{x:+.2f}%" if is_num(x) else "-")
st.dataframe(owner_show, use_container_width=True, hide_index=True)

st.divider()

# =========================================================
# 10. 현재 보유자산
# =========================================================
st.subheader("📊 현재 보유자산")
owners = ["전체"] + sorted([x for x in holdings_df["소유자"].dropna().unique().tolist() if x])
owner_filter = st.selectbox("소유자", owners, index=0)
view = holdings_df.copy()
if owner_filter != "전체":
    view = view[view["소유자"] == owner_filter].copy()
view = view.sort_values("현재평가액(KRW)", ascending=False)

show = view[[
    "소유자", "금융사", "계좌", "자산", "보유수량", "평균매수가", "평균취득환율",
    "현재가", "취득원가(KRW)", "현재평가액(KRW)", "미실현손익(KRW)", "수익률(%)", "상태"
]].copy()

show["보유수량"] = show["보유수량"].map(lambda x: f"{x:,.8f}".rstrip("0").rstrip("."))
show["평균매수가"] = show["평균매수가"].map(lambda x: f"{x:,.4f}" if is_num(x) else "-")
show["평균취득환율"] = show["평균취득환율"].map(lambda x: f"{x:,.2f}" if is_num(x) else "-")
show["현재가"] = show["현재가"].map(lambda x: f"{x:,.4f}" if is_num(x) else "-")
for c in ["취득원가(KRW)", "현재평가액(KRW)", "미실현손익(KRW)"]:
    show[c] = show[c].map(lambda x: f"{x:,.0f}")
show["수익률(%)"] = show["수익률(%)"].map(lambda x: f"{x:+.2f}%" if is_num(x) else "-")
st.dataframe(show, use_container_width=True, hide_index=True, height=650)

st.divider()

# =========================================================
# 11. 실현손익 / 배당이자
# =========================================================
left, right = st.columns(2)
with left:
    st.subheader("💰 매도 실현손익")
    if realized_df.empty:
        st.info("아직 기준일 이후 매도 기록이 없습니다.")
    else:
        rshow = realized_df.copy().sort_values("거래일", ascending=False)
        for c in ["매도분취득원가(KRW)", "순매도대금(KRW)", "실현손익(KRW)"]:
            rshow[c] = rshow[c].map(lambda x: f"{x:,.0f}")
        st.dataframe(rshow, use_container_width=True, hide_index=True)

with right:
    st.subheader("💵 배당·이자")
    if income_df.empty:
        st.info("아직 배당·이자 거래 기록이 없습니다.")
    else:
        ishow = income_df.copy().sort_values("거래일", ascending=False)
        ishow["금액(KRW)"] = ishow["금액(KRW)"].map(lambda x: f"{x:,.0f}")
        st.dataframe(ishow, use_container_width=True, hide_index=True)

st.divider()

# =========================================================
# 12. 자동매수 월정산 도우미
# =========================================================
st.subheader("🗓️ 주식모으기 월정산 도우미")
st.caption(
    "오클로·프리포트맥모란·URA처럼 매일 자동매수하는 종목은 매일 기록하지 않아도 됩니다. "
    "매도가 없었던 달이라면 월말에 한 번, 그 달의 추가매수분을 합쳐서 거래내역에 1행으로 기록하면 됩니다."
)

market_holdings = holdings_df[(holdings_df["티커"] != "") & (holdings_df["상태"] != "CASH")].copy()
if not market_holdings.empty:
    labels = []
    mapping = {}
    for idx, r in market_holdings.iterrows():
        label = f"{r['소유자']} | {r['자산']} | {r['금융사']} {r['계좌']}"
        labels.append(label)
        mapping[label] = r

    selected = st.selectbox("자동매수 종목", labels)
    base_row = mapping[selected]

    h1, h2, h3 = st.columns(3)
    new_total_qty = h1.number_input(
        "증권사 현재 총 보유수량",
        min_value=0.0,
        value=float(base_row["보유수량"]),
        format="%.8f",
    )
    broker_avg = h2.number_input(
        "증권사 현재 평균매수가(원통화)",
        min_value=0.0,
        value=float(base_row["평균매수가"]),
        format="%.8f",
    )
    month_krw = h3.number_input(
        "이번 달 실제 원화 매수총액",
        min_value=0.0,
        value=0.0,
        step=10000.0,
        format="%.0f",
    )

    old_qty = float(base_row["보유수량"])
    old_native_cost = float(base_row["평균매수가"]) * old_qty
    new_native_cost = new_total_qty * broker_avg
    add_qty = new_total_qty - old_qty
    add_native = new_native_cost - old_native_cost
    add_avg = add_native / add_qty if add_qty > EPS and add_native > 0 else np.nan
    implied_fx = month_krw / add_native if month_krw > 0 and add_native > EPS else np.nan

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("추가수량", f"{add_qty:,.8f}".rstrip("0").rstrip("."))
    q2.metric("이번 달 매수금액", money(add_native, base_row["거래통화"]) if add_native > 0 else "-")
    q3.metric("월 가중평균 매수가", f"{add_avg:,.4f}" if is_num(add_avg) else "-")
    q4.metric("월 평균 취득환율", f"{implied_fx:,.2f}원" if is_num(implied_fx) else "-")

    if add_qty > EPS and is_num(add_avg):
        st.success(
            "거래내역에 월말 기준으로 `매수` 1행만 추가하세요: "
            f"수량 {add_qty:.8f}, 체결단가 {add_avg:.6f}, 실제원화금액 {month_krw:,.0f}원. "
            "거래금액과 적용환율은 시트에서 자동 계산됩니다."
        )
        st.caption(
            "※ 이 계산은 해당 월에 매도가 없었다는 전제입니다. 같은 달에 일부매도가 있었다면 "
            "월말 보유수량 차이만으로는 매수량을 알 수 없으므로 미래에셋 거래내역의 매수 체결내역을 합산해야 합니다."
        )

st.divider()

# =========================================================
# 13. 원장 확인
# =========================================================
with st.expander("📒 거래원장 보기", expanded=False):
    ledger_cols = [
        "거래일", "소유자", "금융사/거래소", "계좌구분", "거래유형", "자산/종목명", "티커",
        "거래통화", "수량", "체결단가", "거래금액", "수수료", "세금", "적용환율(자동)",
        "실제원화금액(KRW)", "현금증감(거래통화)"
    ]
    st.dataframe(trades[ledger_cols], use_container_width=True, hide_index=True, height=500)

with st.expander("🧾 입력 규칙", expanded=False):
    st.markdown(
        """
- **기존 행은 수정하지 않습니다.** 매수·매도할 때마다 맨 아래에 새 행을 추가합니다.
- 미국주식 매수 시 보통 **수량 + 체결단가 + 실제원화금액**만 입력하면 됩니다. 거래금액과 적용환율은 시트 수식이 계산합니다.
- 미국주식을 팔고 달러가 그대로 남는 경우에는 `실제원화금액(KRW)`을 비워도 됩니다. 앱은 매도일 환율로 원화 실현손익을 추정하고 USD 현금으로 남깁니다.
- 매도대금이 실제로 원화로 환전/정산됐다면 `실제원화금액(KRW)`을 입력하세요.
- 월급 등 **새로운 외부자금**은 `자금흐름`에 외부입금으로 기록합니다. 종목을 사고파는 내부 이동은 자금흐름에 기록하지 않습니다.
- 수수료/세금 칸을 입력하면 앱이 취득원가 또는 순매도대금에 반영합니다. 증권사의 `실제원화금액`이 이미 수수료까지 포함한 숫자라면 수수료 칸은 0으로 두어 이중계산을 피하세요.
        """
    )
