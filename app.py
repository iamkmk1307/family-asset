import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------
# 0. 상수
# ---------------------------------------------------------
SHEET_KEY = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
KST = ZoneInfo("Asia/Seoul")
TROY_OUNCE_TO_GRAM = 31.1034768

# ---------------------------------------------------------
# 1. 웹 앱 기본 설정 및 새로고침 버튼
# ---------------------------------------------------------
st.set_page_config(page_title="우리가족 자산 대시보드", layout="wide")
st.title("👨‍👩‍👧 우리 가족 통합 자산 대시보드")

if st.button("🔄 최신 데이터 불러오기"):
    st.cache_data.clear()
    st.rerun()

st.markdown("---")

# ---------------------------------------------------------
# 2. 공통 함수: 숫자 정리 / Yahoo 시세 / 환율
# ---------------------------------------------------------
def to_number(value):
    if value is None:
        return 0.0
    s = str(value).strip()
    if s == "":
        return 0.0
    for ch in [",", "₩", "$", "¥", "€", "%"]:
        s = s.replace(ch, "")
    try:
        return float(s)
    except Exception:
        return 0.0


@st.cache_data(ttl=900, show_spinner=False)
def get_market_data(ticker: str):
    """
    반환값: (현재가, 시세통화, 상태)
    현재가는 Yahoo Finance가 제공하는 해당 종목의 '시세통화' 기준 가격.
    """
    ticker = str(ticker).strip()
    if ticker in ["", "-"]:
        return np.nan, "", "NO_TICKER"

    try:
        t = yf.Ticker(ticker)

        # 1차: fast_info의 최근 가격
        price = np.nan
        try:
            price = float(t.fast_info["last_price"])
        except Exception:
            pass

        # 2차 fallback: 최근 5거래일 종가
        if not np.isfinite(price) or price <= 0:
            hist = t.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                repair=True,
            )
            closes = hist["Close"].dropna() if "Close" in hist.columns else pd.Series(dtype=float)
            if not closes.empty:
                price = float(closes.iloc[-1])

        # 시세통화는 metadata에서 읽음
        currency = ""
        try:
            md = t.get_history_metadata()
            currency = str(md.get("currency", "")).upper().strip()
        except Exception:
            try:
                currency = str(t.fast_info["currency"]).upper().strip()
            except Exception:
                currency = ""

        if not np.isfinite(price) or price <= 0:
            return np.nan, currency, "PRICE_ERROR"

        # 금 선물 GC=F는 USD / troy ounce -> USD / gram으로 단위 변환
        if ticker == "GC=F":
            price = price / TROY_OUNCE_TO_GRAM
            return price, currency or "USD", "OK_GOLD_USD_PER_GRAM"

        return price, currency, "OK"

    except Exception as e:
        return np.nan, "", f"ERROR:{type(e).__name__}"


@st.cache_data(ttl=900, show_spinner=False)
def get_fx_to_krw(currency: str):
    """
    1 단위 외화가 몇 KRW인지 반환.
    KRW=1, USD는 Yahoo의 KRW=X(USD/KRW),
    기타 통화는 XXXKRW=X 직접환율 우선, 실패 시 USD 교차환율 사용.
    """
    ccy = str(currency).upper().strip()

    if ccy in ["", "KRW"]:
        return 1.0

    # USDT는 자산관리 목적상 USD와 동일 취급.
    # 거래소별 김치프리미엄/디페깅까지 반영하려면 별도 시세원을 붙이는 것이 좋음.
    if ccy == "USDT":
        ccy = "USD"

    if ccy == "USD":
        px, _, status = get_market_data("KRW=X")
        if status.startswith("OK") and np.isfinite(px):
            return float(px)
        return np.nan

    # 1차: 직접 환율 (예: JPYKRW=X, EURKRW=X)
    direct_ticker = f"{ccy}KRW=X"
    px, _, status = get_market_data(direct_ticker)
    if status.startswith("OK") and np.isfinite(px):
        return float(px)

    # 2차: USD 교차환율
    # Yahoo의 다수 통화는 XXX=X가 USD/XXX 형태 (예: JPY=X = USD/JPY)
    usd_krw = get_fx_to_krw("USD")
    usd_to_ccy_ticker = f"{ccy}=X"
    usd_to_ccy, _, status2 = get_market_data(usd_to_ccy_ticker)
    if (
        np.isfinite(usd_krw)
        and status2.startswith("OK")
        and np.isfinite(usd_to_ccy)
        and usd_to_ccy > 0
    ):
        return float(usd_krw / usd_to_ccy)

    # 3차: 일부 통화는 XXXUSD=X 형태 (예: GBPUSD=X)
    ccy_to_usd_ticker = f"{ccy}USD=X"
    ccy_to_usd, _, status3 = get_market_data(ccy_to_usd_ticker)
    if (
        np.isfinite(usd_krw)
        and status3.startswith("OK")
        and np.isfinite(ccy_to_usd)
        and ccy_to_usd > 0
    ):
        return float(ccy_to_usd * usd_krw)

    return np.nan


# ---------------------------------------------------------
# 3. 구글 API 연결 및 데이터 수집
# ---------------------------------------------------------
@st.cache_data(ttl=900)
def load_data():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    secret_dict = json.loads(st.secrets["GCP_JSON"])
    credentials = Credentials.from_service_account_info(secret_dict, scopes=scope)
    gc = gspread.authorize(credentials)

    spreadsheet = gc.open_by_key(SHEET_KEY)
    worksheet = spreadsheet.sheet1
    rows = worksheet.get_all_values()

    if not rows:
        raise ValueError("메인 시트에 데이터가 없습니다.")

    df = pd.DataFrame(rows[1:], columns=rows[0])

    required_cols = [
        "소유자",
        "대분류",
        "자산/종목명",
        "티커(기호)",
        "보유수량",
        "매수단가",
        "매수통화",
        "투입원금(KRW)",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"시트에 필요한 컬럼이 없습니다: {', '.join(missing)}")

    # 선택 컬럼: 있으면 수동 보정에 사용, 없어도 앱은 정상 작동
    if "시세통화" not in df.columns:
        df["시세통화"] = ""
    if "수동현재가" not in df.columns:
        df["수동현재가"] = ""

    # 빈 행 제거
    df = df[df["자산/종목명"].astype(str).str.strip() != ""].copy()

    # 숫자 정리
    for col in ["보유수량", "매수단가", "투입원금(KRW)", "수동현재가"]:
        df[col] = df[col].apply(to_number)

    # 문자열 정리
    for col in ["소유자", "대분류", "자산/종목명", "티커(기호)", "매수통화", "시세통화"]:
        df[col] = df[col].astype(str).str.strip()

    # 티커는 중복 호출하지 않고 1회씩만 조회
    unique_tickers = sorted({
        t for t in df["티커(기호)"].astype(str).str.strip().tolist()
        if t not in ["", "-"] and t.upper() not in [
            "KRW", "USD", "USDT", "JPY", "EUR", "CNY", "HKD", "GBP", "CAD", "AUD", "SGD", "CHF"
        ]
    })
    market_map = {ticker: get_market_data(ticker) for ticker in unique_tickers}

    prices = []
    quote_ccys = []
    fx_rates = []
    statuses = []
    current_values = []
    implied_buy_fx = []
    price_effects = []
    fx_effects = []

    cash_currency_codes = {
        "KRW", "USD", "USDT", "JPY", "EUR", "CNY", "HKD", "GBP", "CAD", "AUD", "SGD", "CHF"
    }

    for _, row in df.iterrows():
        ticker = str(row["티커(기호)"]).strip()
        manual_price = float(row["수동현재가"] or 0)
        manual_ccy = str(row["시세통화"]).upper().strip()
        buy_ccy = str(row["매수통화"]).upper().strip()
        qty = float(row["보유수량"] or 0)
        buy_price = float(row["매수단가"] or 0)
        principal = float(row["투입원금(KRW)"] or 0)
        category = str(row["대분류"]).strip()

        # 1) 현금성 자산: 티커에 통화코드를 직접 넣는 방식 지원
        if category == "현금성" and ticker.upper() in cash_currency_codes:
            quote_ccy = ticker.upper()
            if quote_ccy == "USDT":
                quote_ccy = "USDT"
            fx = get_fx_to_krw(quote_ccy)
            price = 1.0
            value = qty * fx if np.isfinite(fx) else np.nan
            status = "OK_CASH" if np.isfinite(value) else "FX_ERROR"

        # 2) 티커가 없는 자산: 투입원금을 현재가치로 유지
        elif ticker in ["", "-"]:
            price = np.nan
            quote_ccy = "KRW"
            fx = 1.0
            value = principal
            status = "NO_TICKER_USE_PRINCIPAL"

        # 3) 일반 종목/ETF/코인/금
        else:
            auto_price, auto_ccy, market_status = market_map.get(ticker, get_market_data(ticker))

            # 수동현재가가 있으면 가격만 수동값 우선
            if manual_price > 0:
                price = manual_price
                status = "OK_MANUAL_PRICE"
            else:
                price = auto_price
                status = market_status

            # 시세통화 수동지정 > Yahoo 자동감지 > 매수통화 fallback
            quote_ccy = manual_ccy or str(auto_ccy).upper().strip() or buy_ccy or "KRW"
            fx = get_fx_to_krw(quote_ccy)

            if np.isfinite(price) and price > 0 and np.isfinite(fx) and fx > 0:
                value = qty * price * fx
            else:
                value = np.nan
                if not np.isfinite(price) or price <= 0:
                    status = "PRICE_ERROR"
                elif not np.isfinite(fx) or fx <= 0:
                    status = "FX_ERROR"

        prices.append(price)
        quote_ccys.append(quote_ccy)
        fx_rates.append(fx)
        statuses.append(status)
        current_values.append(value)

        # 매수 당시의 암묵적 환율 추정:
        # 투입원금(KRW) / (보유수량 x 매수단가)
        # 수수료/부분매도/추가매수가 복잡하면 '참고값'으로 보는 것이 맞음.
        if qty > 0 and buy_price > 0 and principal > 0:
            buy_fx = principal / (qty * buy_price)
        else:
            buy_fx = np.nan
        implied_buy_fx.append(buy_fx)

        # 가격효과 / 환율효과 분리 (둘의 합 = 총 손익, 단 buy_fx 추정이 유효할 때)
        if (
            np.isfinite(value)
            and np.isfinite(price)
            and np.isfinite(fx)
            and np.isfinite(buy_fx)
            and qty > 0
            and buy_price > 0
        ):
            price_pnl = qty * (price - buy_price) * buy_fx
            fx_pnl = qty * price * (fx - buy_fx)
        else:
            price_pnl = np.nan
            fx_pnl = np.nan
        price_effects.append(price_pnl)
        fx_effects.append(fx_pnl)

    df["현재가"] = prices
    df["실제시세통화"] = quote_ccys
    df["적용환율(KRW)"] = fx_rates
    df["가격상태"] = statuses
    df["현재평가금액(KRW)"] = current_values

    # 시세 오류는 0으로 숨기지 않고 NaN으로 남긴 뒤 화면에서 경고
    df["수익금(KRW)"] = df["현재평가금액(KRW)"] - df["투입원금(KRW)"]
    df["추정매수환율"] = implied_buy_fx
    df["가격효과(KRW)"] = price_effects
    df["환율효과(KRW)"] = fx_effects

    # 합계에서는 시세 오류 종목의 NaN을 자동 제외하므로, 반드시 화면 경고를 함께 표시
    total_p = df["투입원금(KRW)"].sum()
    total_c = df["현재평가금액(KRW)"].sum(skipna=True)

    # -----------------------------------------------------
    # 월별 History: 한국시간 기준, 그 달 '첫 접속 시' 1회 기록
    # -----------------------------------------------------
    try:
        h_worksheet = spreadsheet.worksheet("History")
    except Exception:
        h_worksheet = spreadsheet.add_worksheet(title="History", rows="200", cols="5")
        h_worksheet.append_row(["날짜", "총 투입 원금", "현재 총 자산"])

    h_data = h_worksheet.get_all_records()
    h_df = pd.DataFrame(h_data)

    now = datetime.now(KST)
    current_month = now.strftime("%Y-%m")
    should_append = True

    if not h_df.empty and "날짜" in h_df.columns:
        parsed_dates = pd.to_datetime(h_df["날짜"], errors="coerce")
        existing_months = parsed_dates.dt.strftime("%Y-%m").dropna().tolist()
        should_append = current_month not in existing_months

    # 시세 오류가 있으면 잘못된 월 스냅샷을 남기지 않음
    has_price_error = df["현재평가금액(KRW)"].isna().any()
    if should_append and not has_price_error:
        h_worksheet.append_row([
            now.strftime("%Y-%m-%d"),
            int(round(total_p)),
            int(round(total_c)),
        ])
        h_df = pd.DataFrame(h_worksheet.get_all_records())

    usd_krw_rate = get_fx_to_krw("USD")
    return df, usd_krw_rate, h_df


with st.spinner("🔄 데이터를 불러오는 중입니다..."):
    df, usd_krw_rate, history_df = load_data()

# ---------------------------------------------------------
# 4. 데이터 오류 경고
# ---------------------------------------------------------
error_df = df[df["현재평가금액(KRW)"].isna()].copy()
if not error_df.empty:
    names = ", ".join(error_df["자산/종목명"].astype(str).tolist())
    st.error(
        "⚠️ 시세 또는 환율을 불러오지 못한 종목이 있어 총자산에서 제외되었습니다: "
        + names
        + "\n\n아래 '시세 진단' 표에서 티커/시세통화를 확인해주세요."
    )

# ---------------------------------------------------------
# 5. 화면 상단 요약 지표 및 히스토리
# ---------------------------------------------------------
total_principal = df["투입원금(KRW)"].sum()
total_current = df["현재평가금액(KRW)"].sum(skipna=True)
total_profit = total_current - total_principal
total_rate = (total_profit / total_principal) * 100 if total_principal > 0 else 0

col1, col2, col3 = st.columns([1, 1, 1.8])
col1.metric(label="💰 총 투입 원금", value=f"{total_principal:,.0f}원")
col2.metric(
    label="📈 현재 총 자산",
    value=f"{total_current:,.0f}원",
    delta=f"{total_profit:+,.0f}원 ({total_rate:+.2f}%)",
)

with col3:
    if not history_df.empty:
        latest_date = history_df["날짜"].iloc[-1]
        with st.expander(f"📜 월별 자산 성장 기록 (최근: {latest_date})", expanded=False):
            h_display = history_df.copy().sort_values(by="날짜", ascending=False)
            for col in ["총 투입 원금", "현재 총 자산"]:
                if col in h_display.columns:
                    h_display[col] = pd.to_numeric(h_display[col], errors="coerce").map("{:,.0f}원".format)
            st.dataframe(h_display, use_container_width=True, hide_index=True, height=180)
    else:
        st.info("아직 기록된 히스토리가 없습니다.")

if np.isfinite(usd_krw_rate):
    st.markdown(f"*🔎 적용 USD/KRW 환율: 1달러 = {usd_krw_rate:,.2f}원*")
else:
    st.markdown("*🔎 USD/KRW 환율 조회 실패*")
st.markdown("---")

# ---------------------------------------------------------
# 6. 종목별 상세 현황
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")

display_df = df[[
    "소유자",
    "자산/종목명",
    "투입원금(KRW)",
    "현재평가금액(KRW)",
    "수익금(KRW)",
]].copy()

display_df["수익률(%)"] = np.where(
    (display_df["투입원금(KRW)"] > 0) & display_df["현재평가금액(KRW)"].notna(),
    (display_df["수익금(KRW)"] / display_df["투입원금(KRW)"]) * 100,
    np.nan,
)
display_df["자산비중(%)"] = np.where(
    display_df["현재평가금액(KRW)"].notna() & (total_current > 0),
    (display_df["현재평가금액(KRW)"] / total_current) * 100,
    np.nan,
)
display_df = display_df.sort_values(by="현재평가금액(KRW)", ascending=False, na_position="last")

formatted_df = display_df.copy()
for col in ["투입원금(KRW)", "현재평가금액(KRW)", "수익금(KRW)"]:
    formatted_df[col] = formatted_df[col].apply(
        lambda x: "조회실패" if pd.isna(x) else f"{x:,.0f}"
    )
formatted_df["수익률(%)"] = formatted_df["수익률(%)"].apply(
    lambda x: "-" if pd.isna(x) else f"{x:+.2f}%"
)
formatted_df["자산비중(%)"] = formatted_df["자산비중(%)"].apply(
    lambda x: "-" if pd.isna(x) else f"{x:.1f}%"
)

total_row = pd.DataFrame({
    "소유자": ["-"],
    "자산/종목명": ["🔥총합🔥"],
    "투입원금(KRW)": [f"{total_principal:,.0f}"],
    "현재평가금액(KRW)": [f"{total_current:,.0f}"],
    "수익금(KRW)": [f"{total_profit:+,.0f}"],
    "수익률(%)": [f"{total_rate:+.2f}%"],
    "자산비중(%)": ["100.0%"],
})
formatted_df = pd.concat([formatted_df, total_row], ignore_index=True)

st.dataframe(
    formatted_df,
    use_container_width=True,
    hide_index=True,
    height=(len(display_df) * 36) + 80,
)
st.markdown("---")

# ---------------------------------------------------------
# 7. 대분류별 자산 비중
# ---------------------------------------------------------
st.subheader("📁 대분류별 자산 비중")

cat_df = df.groupby("대분류").agg({
    "현재평가금액(KRW)": "sum",
    "자산/종목명": lambda x: ", ".join(dict.fromkeys(x.astype(str).str.strip())),
}).reset_index()

cat_df["비중(%)"] = (
    (cat_df["현재평가금액(KRW)"] / total_current) * 100
    if total_current > 0
    else 0
)
cat_df = cat_df.sort_values(by="현재평가금액(KRW)", ascending=False)
cat_df = cat_df[["대분류", "자산/종목명", "현재평가금액(KRW)", "비중(%)"]]
cat_df.columns = ["대분류", "소분류 종류", "현재평가금액", "비중(%)"]
cat_df["현재평가금액"] = cat_df["현재평가금액"].map("{:,.0f}원".format)
cat_df["비중(%)"] = cat_df["비중(%)"].map("{:.1f}%".format)
st.dataframe(cat_df, use_container_width=True, hide_index=True)

st.markdown("---")

# ---------------------------------------------------------
# 8. 시세/환율 진단표
# ---------------------------------------------------------
with st.expander("🧪 시세 진단 (외국주식/환율이 이상할 때 여기부터 확인)", expanded=False):
    diag = df[[
        "자산/종목명",
        "티커(기호)",
        "매수통화",
        "실제시세통화",
        "현재가",
        "적용환율(KRW)",
        "가격상태",
        "현재평가금액(KRW)",
    ]].copy()

    diag["현재가"] = diag["현재가"].apply(
        lambda x: "-" if pd.isna(x) else f"{x:,.6f}".rstrip("0").rstrip(".")
    )
    diag["적용환율(KRW)"] = diag["적용환율(KRW)"].apply(
        lambda x: "조회실패" if pd.isna(x) else f"{x:,.4f}"
    )
    diag["현재평가금액(KRW)"] = diag["현재평가금액(KRW)"].apply(
        lambda x: "조회실패" if pd.isna(x) else f"{x:,.0f}원"
    )
    st.dataframe(diag, use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# 9. 외화자산 손익 분해 (참고용)
# ---------------------------------------------------------
with st.expander("💱 외화자산 손익 분해 (가격효과 vs 환율효과)", expanded=False):
    pnl = df[[
        "자산/종목명",
        "매수단가",
        "현재가",
        "추정매수환율",
        "적용환율(KRW)",
        "가격효과(KRW)",
        "환율효과(KRW)",
        "수익금(KRW)",
    ]].copy()
    pnl = pnl[pnl["추정매수환율"].notna()].copy()

    for col in ["추정매수환율", "적용환율(KRW)"]:
        pnl[col] = pnl[col].apply(lambda x: "-" if pd.isna(x) else f"{x:,.2f}")
    for col in ["가격효과(KRW)", "환율효과(KRW)", "수익금(KRW)"]:
        pnl[col] = pnl[col].apply(lambda x: "-" if pd.isna(x) else f"{x:+,.0f}원")

    st.caption("※ 추정매수환율 = 투입원금(KRW) ÷ (보유수량 × 매수단가). 수수료·부분매도·다회매수가 복잡하면 참고값입니다.")
    st.dataframe(pnl, use_container_width=True, hide_index=True)
