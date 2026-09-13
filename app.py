import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# =========================================================
# 0. 기본 설정
# =========================================================
SHEET_KEY = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
ASSET_SHEET_NAME = "자산관리(코딩용)"
HISTORY_SHEET_NAME = "History"

KST = ZoneInfo("Asia/Seoul")
TROY_OUNCE_TO_GRAM = 31.1034768

SUPPORTED_CASH_CURRENCIES = {
    "KRW", "USD", "USDT", "JPY", "EUR", "CNY", "HKD",
    "GBP", "CAD", "AUD", "SGD", "CHF"
}

st.set_page_config(
    page_title="우리가족 자산 대시보드",
    page_icon="👨‍👩‍👧",
    layout="wide",
)

st.title("👨‍👩‍👧 우리 가족 통합 자산 대시보드")

top_left, top_right = st.columns([1, 5])
with top_left:
    if st.button("🔄 최신 데이터"):
        st.cache_data.clear()
        st.rerun()

st.caption(
    "외화자산은 원통화 기준 원가를 보존하고, 현재 평가액만 최신 환율로 KRW 환산합니다. "
    "`실제투입원금(KRW)`은 환율이 바뀌어도 변하지 않는 고정 원금으로 사용합니다. "
    "`배당수익(USD)`은 누적 세후 달러 배당을 원본 통화 그대로 기록하며 현재 총 자산과 중복 합산하지 않습니다."
)
st.divider()


# =========================================================
# 1. 공통 유틸
# =========================================================
def to_number(value, default=0.0):
    if value is None:
        return default

    s = str(value).strip()
    if s == "":
        return default

    for ch in [",", "₩", "$", "¥", "€", "%"]:
        s = s.replace(ch, "")

    try:
        return float(s)
    except Exception:
        return default


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def is_valid_number(value):
    try:
        return np.isfinite(float(value))
    except Exception:
        return False


def format_won(value):
    if not is_valid_number(value):
        return "-"
    return f"{float(value):,.0f}원"


def format_money(value, currency):
    if not is_valid_number(value):
        return "-"

    currency = clean_text(currency).upper()
    value = float(value)

    if currency == "KRW":
        return f"{value:,.0f}원"
    if currency in {"USD", "USDT"}:
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


# =========================================================
# 2. Yahoo Finance 시세 / 환율
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def get_market_data(ticker):
    """
    반환:
        price: Yahoo 시세통화 기준 현재가(최근가)
        currency: Yahoo가 보고한 시세통화
        status: 조회 상태
    """
    ticker = clean_text(ticker)

    if ticker in {"", "-"}:
        return np.nan, "", "NO_TICKER"

    try:
        obj = yf.Ticker(ticker)

        price = np.nan

        # 1차: 가장 최근 가격
        try:
            fast = obj.fast_info
            p = fast.get("last_price")
            if p is not None and is_valid_number(p) and float(p) > 0:
                price = float(p)
        except Exception:
            pass

        # 2차: 최근 거래일 종가
        if not is_valid_number(price) or price <= 0:
            hist = obj.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                repair=True,
            )
            if not hist.empty and "Close" in hist.columns:
                closes = hist["Close"].dropna()
                if not closes.empty:
                    price = float(closes.iloc[-1])

        # history(repair=True) 뒤 metadata의 currency 사용
        currency = ""
        try:
            metadata = obj.get_history_metadata()
            currency = clean_text(metadata.get("currency", "")).upper()
        except Exception:
            pass

        # fast_info의 currency fallback
        if not currency:
            try:
                currency = clean_text(obj.fast_info.get("currency", "")).upper()
            except Exception:
                pass

        if not is_valid_number(price) or price <= 0:
            return np.nan, currency, "PRICE_ERROR"

        return float(price), currency, "OK"

    except Exception as e:
        return np.nan, "", f"ERROR:{type(e).__name__}"


@st.cache_data(ttl=600, show_spinner=False)
def get_fx_to_krw(currency):
    """
    1 단위 외화가 몇 KRW인지 반환합니다.
    예:
      USD -> 약 1,4xx KRW
      JPY -> 약 9~10 KRW
    """
    ccy = clean_text(currency).upper()

    if ccy in {"", "KRW"}:
        return 1.0

    if ccy == "USDT":
        ccy = "USD"

    # USD/KRW
    if ccy == "USD":
        px, _, status = get_market_data("KRW=X")
        if status == "OK" and is_valid_number(px):
            return float(px)
        return np.nan

    # 직접 환율: JPYKRW=X, EURKRW=X 등
    direct_px, _, direct_status = get_market_data(f"{ccy}KRW=X")
    if direct_status == "OK" and is_valid_number(direct_px) and direct_px > 0:
        return float(direct_px)

    usd_krw = get_fx_to_krw("USD")
    if not is_valid_number(usd_krw):
        return np.nan

    # USD/통화 형태: JPY=X, CNY=X 등
    cross_px, _, cross_status = get_market_data(f"{ccy}=X")
    if cross_status == "OK" and is_valid_number(cross_px) and cross_px > 0:
        return float(usd_krw / cross_px)

    # 통화/USD 형태: EURUSD=X, GBPUSD=X 등
    cross_px2, _, cross_status2 = get_market_data(f"{ccy}USD=X")
    if cross_status2 == "OK" and is_valid_number(cross_px2) and cross_px2 > 0:
        return float(cross_px2 * usd_krw)

    return np.nan


# =========================================================
# 3. Google Sheets 연결
# =========================================================
def get_spreadsheet():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    secret_dict = json.loads(st.secrets["GCP_JSON"])
    credentials = Credentials.from_service_account_info(
        secret_dict,
        scopes=scope,
    )

    gc = gspread.authorize(credentials)
    return gc.open_by_key(SHEET_KEY)


@st.cache_data(ttl=600, show_spinner=False)
def load_asset_sheet():
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(ASSET_SHEET_NAME)

    rows = worksheet.get_all_values()

    if not rows:
        raise ValueError(f"`{ASSET_SHEET_NAME}` 시트에 데이터가 없습니다.")

    df = pd.DataFrame(rows[1:], columns=rows[0])

    # 과거 컬럼명도 호환
    if "실제투입원금(KRW)" not in df.columns and "투입원금(KRW)" in df.columns:
        df["실제투입원금(KRW)"] = df["투입원금(KRW)"]

    required = [
        "소유자",
        "대분류",
        "소분류",
        "자산/종목명",
        "금융사/거래소",
        "티커(기호)",
        "매수통화",
        "보유수량",
        "매수단가",
        "실제투입원금(KRW)",
    ]

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            "시트에 필요한 컬럼이 없습니다: " + ", ".join(missing)
        )

    if "취득원가" not in df.columns:
        df["취득원가"] = ""

    # 선택 컬럼: J열을 나중에 평균취득환율로 바꿔도 호환
    if "평균취득환율" not in df.columns:
        if "평균취득환율(KRW/통화)" in df.columns:
            df["평균취득환율"] = df["평균취득환율(KRW/통화)"]
        else:
            df["평균취득환율"] = ""

    # 선택 컬럼: 누적 세후 달러 배당수익(USD)
    # 과거 헤더 `배당금`도 자동 호환
    if "배당수익(USD)" not in df.columns:
        if "배당금" in df.columns:
            df["배당수익(USD)"] = df["배당금"]
        else:
            df["배당수익(USD)"] = 0

    # 빈 행 제거
    df = df[
        df["자산/종목명"].astype(str).str.strip() != ""
    ].copy()

    # 문자열 정리
    text_cols = [
        "소유자", "대분류", "소분류", "자산/종목명",
        "금융사/거래소", "티커(기호)", "매수통화"
    ]
    for col in text_cols:
        df[col] = df[col].map(clean_text)

    # 숫자 정리
    numeric_cols = [
        "보유수량", "매수단가", "취득원가",
        "평균취득환율", "실제투입원금(KRW)", "배당수익(USD)"
    ]
    for col in numeric_cols:
        df[col] = df[col].map(to_number)

    return df


@st.cache_data(ttl=600, show_spinner=False)
def load_history():
    spreadsheet = get_spreadsheet()

    try:
        worksheet = spreadsheet.worksheet(HISTORY_SHEET_NAME)
    except Exception:
        return pd.DataFrame(
            columns=["날짜", "총 투입 원금", "현재 총 자산", "누적 배당수익(USD)"]
        )

    rows = worksheet.get_all_values()
    if not rows:
        return pd.DataFrame(
            columns=["날짜", "총 투입 원금", "현재 총 자산", "누적 배당수익(USD)"]
        )

    header = rows[0]
    width = len(header)
    normalized_rows = [
        (r + [""] * width)[:width]
        for r in rows[1:]
    ]
    return pd.DataFrame(normalized_rows, columns=header)


def save_monthly_history(total_principal, total_current, total_dividend_usd):
    """
    해당 월의 기록이 없을 때만 1회 저장.
    날짜는 월 구분을 위해 YYYY-MM-01 형태로 저장합니다.
    """
    try:
        spreadsheet = get_spreadsheet()

        try:
            worksheet = spreadsheet.worksheet(HISTORY_SHEET_NAME)
        except Exception:
            worksheet = spreadsheet.add_worksheet(
                title=HISTORY_SHEET_NAME,
                rows=200,
                cols=5,
            )
            worksheet.append_row(
                ["날짜", "총 투입 원금", "현재 총 자산", "누적 배당수익(USD)"]
            )

        # 기존 History 시트도 배당수익 열을 자동 확장
        header = worksheet.row_values(1)
        if "누적 배당수익(USD)" not in header:
            worksheet.update_cell(1, 4, "누적 배당수익(USD)")

        rows = worksheet.get_all_values()

        now = datetime.now(KST)
        month_key = now.strftime("%Y-%m")
        month_label = now.strftime("%Y-%m-01")

        existing_dates = []
        if len(rows) >= 2:
            existing_dates = [
                clean_text(r[0])
                for r in rows[1:]
                if len(r) > 0
            ]

        already_exists = any(
            d.startswith(month_key)
            for d in existing_dates
        )

        if not already_exists:
            worksheet.append_row([
                month_label,
                int(round(total_principal)),
                int(round(total_current)),
                round(float(total_dividend_usd), 2),
            ])

        return True

    except Exception:
        # History 저장 실패가 대시보드 전체를 막지 않도록 함
        return False


# =========================================================
# 4. 자산 평가 엔진
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def evaluate_assets(raw_df):
    df = raw_df.copy()

    unique_tickers = sorted({
        clean_text(t)
        for t in df["티커(기호)"].tolist()
        if clean_text(t) not in {"", "-"}
    })

    market_map = {
        ticker: get_market_data(ticker)
        for ticker in unique_tickers
    }

    evaluated_rows = []

    for _, row in df.iterrows():
        owner = clean_text(row["소유자"])
        major = clean_text(row["대분류"])
        minor = clean_text(row["소분류"])
        name = clean_text(row["자산/종목명"])
        institution = clean_text(row["금융사/거래소"])
        ticker = clean_text(row["티커(기호)"])
        buy_ccy = clean_text(row["매수통화"]).upper()

        qty = to_number(row["보유수량"])
        avg_buy_price = to_number(row["매수단가"])
        fixed_principal = to_number(row["실제투입원금(KRW)"])
        dividend_usd = to_number(row.get("배당수익(USD)", 0))
        stored_buy_fx = to_number(row.get("평균취득환율", 0))

        current_price = np.nan
        quote_ccy = buy_ccy
        fx_rate = 1.0
        current_native_value = np.nan
        current_krw_value = np.nan
        status = "OK"
        valuation_type = ""

        # -------------------------------------------------
        # A. 티커가 있는 시장성 자산
        # -------------------------------------------------
        if ticker not in {"", "-"}:
            market_price, market_ccy, market_status = market_map.get(
                ticker,
                (np.nan, "", "PRICE_ERROR"),
            )

            if market_status != "OK":
                status = market_status
            else:
                current_price = market_price
                quote_ccy = market_ccy or buy_ccy

                # 금 선물은 USD/troy oz -> USD/gram
                if ticker == "GC=F":
                    current_price = current_price / TROY_OUNCE_TO_GRAM
                    quote_ccy = "USD"
                    valuation_type = "GOLD_GRAM"
                else:
                    valuation_type = "MARKET"

                fx_rate = get_fx_to_krw(quote_ccy)

                if not is_valid_number(fx_rate):
                    status = "FX_ERROR"
                else:
                    current_native_value = qty * current_price
                    current_krw_value = current_native_value * fx_rate

        # -------------------------------------------------
        # B. 티커 없는 외화 현금 / RP
        #     보유수량을 외화 잔액으로 해석
        # -------------------------------------------------
        elif buy_ccy in SUPPORTED_CASH_CURRENCIES and buy_ccy != "KRW":
            valuation_type = "FOREIGN_CASH"
            quote_ccy = buy_ccy
            current_price = 1.0
            fx_rate = get_fx_to_krw(quote_ccy)

            if not is_valid_number(fx_rate):
                status = "FX_ERROR"
            else:
                current_native_value = qty
                current_krw_value = qty * fx_rate

        # -------------------------------------------------
        # C. 티커 없는 KRW 자산
        #     수량 × 매수단가(또는 현재 잔액)를 현재가치로 사용
        # -------------------------------------------------
        else:
            valuation_type = "MANUAL_KRW"
            quote_ccy = "KRW"
            fx_rate = 1.0
            current_price = avg_buy_price
            current_native_value = qty * avg_buy_price
            current_krw_value = current_native_value

        # -------------------------------------------------
        # 원가 / 수익 분석
        # -------------------------------------------------
        native_cost = np.nan
        implied_buy_fx = np.nan
        local_profit = np.nan
        local_return = np.nan
        price_effect_krw = 0.0
        fx_effect_krw = 0.0

        # 시장성 외화자산
        if (
            valuation_type == "MARKET"
            and quote_ccy not in {"", "KRW"}
            and qty > 0
            and avg_buy_price > 0
        ):
            native_cost = qty * avg_buy_price

            if stored_buy_fx > 0:
                implied_buy_fx = stored_buy_fx
            elif fixed_principal > 0 and native_cost > 0:
                implied_buy_fx = fixed_principal / native_cost

            if is_valid_number(current_native_value):
                local_profit = current_native_value - native_cost
                local_return = (
                    local_profit / native_cost * 100
                    if native_cost > 0 else np.nan
                )

            if (
                is_valid_number(implied_buy_fx)
                and is_valid_number(current_price)
                and is_valid_number(fx_rate)
            ):
                price_effect_krw = (
                    (current_price - avg_buy_price)
                    * qty
                    * implied_buy_fx
                )
                fx_effect_krw = (
                    current_price
                    * qty
                    * (fx_rate - implied_buy_fx)
                )

        # 외화 현금/RP: 원화 손익은 환율+잔액 변화 효과로 표시
        elif valuation_type == "FOREIGN_CASH":
            native_cost = qty

            if stored_buy_fx > 0:
                implied_buy_fx = stored_buy_fx
            elif fixed_principal > 0 and qty > 0:
                implied_buy_fx = fixed_principal / qty

            if (
                is_valid_number(current_krw_value)
                and fixed_principal > 0
            ):
                fx_effect_krw = current_krw_value - fixed_principal

        # KRW / 금 / 국내자산은 환율효과를 별도 분리하지 않음
        else:
            if qty > 0 and avg_buy_price > 0:
                native_cost = qty * avg_buy_price

            if (
                is_valid_number(current_krw_value)
                and fixed_principal > 0
            ):
                price_effect_krw = current_krw_value - fixed_principal

        profit_krw = (
            current_krw_value - fixed_principal
            if is_valid_number(current_krw_value)
            else np.nan
        )

        return_pct = (
            profit_krw / fixed_principal * 100
            if fixed_principal > 0 and is_valid_number(profit_krw)
            else np.nan
        )

        evaluated_rows.append({
            "소유자": owner,
            "대분류": major,
            "소분류": minor,
            "자산/종목명": name,
            "금융사/거래소": institution,
            "티커(기호)": ticker,
            "매수통화": buy_ccy,
            "시세통화": quote_ccy,
            "보유수량": qty,
            "매수단가": avg_buy_price,
            "원통화취득원가": native_cost,
            "실제투입원금(KRW)": fixed_principal,
            "배당수익(USD)": dividend_usd,
            "현재가": current_price,
            "현재환율": fx_rate,
            "현재평가액(원통화)": current_native_value,
            "현재평가금액(KRW)": current_krw_value,
            "수익금(KRW)": profit_krw,
            "수익률(%)": return_pct,
            "원통화수익": local_profit,
            "원통화수익률(%)": local_return,
            "입력취득환율": stored_buy_fx if stored_buy_fx > 0 else np.nan,
            "추정취득환율": implied_buy_fx,
            "가격효과(KRW)": price_effect_krw,
            "환율효과(KRW)": fx_effect_krw,
            "평가방식": valuation_type,
            "상태": status,
        })

    result = pd.DataFrame(evaluated_rows)

    total_current = result["현재평가금액(KRW)"].sum(
        min_count=1
    )

    if is_valid_number(total_current) and total_current > 0:
        result["자산비중(%)"] = (
            result["현재평가금액(KRW)"] / total_current * 100
        )
    else:
        result["자산비중(%)"] = 0.0

    return result


# =========================================================
# 5. 데이터 로딩
# =========================================================
try:
    with st.spinner("📡 구글시트·시세·환율 데이터를 불러오는 중입니다..."):
        raw_df = load_asset_sheet()
        df = evaluate_assets(raw_df)

except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()


# =========================================================
# 6. 전체 요약
# =========================================================
total_principal = df["실제투입원금(KRW)"].sum()
total_current = df["현재평가금액(KRW)"].sum(min_count=1)
total_dividend_usd = df["배당수익(USD)"].sum()
total_profit = total_current - total_principal
total_return = (
    total_profit / total_principal * 100
    if total_principal > 0
    else 0
)

usd_krw = get_fx_to_krw("USD")

# 해당 월 첫 접속 시 History 기록
history_saved = save_monthly_history(
    total_principal,
    total_current,
    total_dividend_usd,
)

# 저장 후 History 로딩
history_df = load_history()

m1, m2, m3, m4, m5 = st.columns(5)

m1.metric(
    "💰 실제 투입 원금",
    f"{total_principal:,.0f}원",
)

m2.metric(
    "📈 현재 총 자산",
    f"{total_current:,.0f}원",
    delta=f"{total_profit:+,.0f}원",
)

m3.metric(
    "📊 전체 수익률",
    f"{total_return:+.2f}%",
)

m4.metric(
    "💵 누적 배당수익",
    f"${total_dividend_usd:,.2f}",
    delta=(
        f"약 {total_dividend_usd * usd_krw:,.0f}원"
        if is_valid_number(usd_krw) else None
    ),
)

m5.metric(
    "💱 USD/KRW",
    f"{usd_krw:,.2f}원" if is_valid_number(usd_krw) else "조회 실패",
)

st.divider()


# =========================================================
# 7. 소유자별 요약
# =========================================================
st.subheader("👥 소유자별 자산")

owner_summary = (
    df.groupby("소유자", as_index=False)
    .agg({
        "실제투입원금(KRW)": "sum",
        "현재평가금액(KRW)": "sum",
        "배당수익(USD)": "sum",
    })
)

owner_summary["수익금(KRW)"] = (
    owner_summary["현재평가금액(KRW)"]
    - owner_summary["실제투입원금(KRW)"]
)

owner_summary["수익률(%)"] = np.where(
    owner_summary["실제투입원금(KRW)"] > 0,
    owner_summary["수익금(KRW)"]
    / owner_summary["실제투입원금(KRW)"] * 100,
    0,
)

owner_display = owner_summary.copy()

for col in [
    "실제투입원금(KRW)",
    "현재평가금액(KRW)",
    "수익금(KRW)",
]:
    owner_display[col] = owner_display[col].map(
        lambda x: f"{x:,.0f}원"
    )

owner_display["배당수익(USD)"] = owner_display["배당수익(USD)"].map(
    lambda x: f"${x:,.2f}"
)

owner_display["수익률(%)"] = owner_display["수익률(%)"].map(
    lambda x: f"{x:+.2f}%"
)

st.dataframe(
    owner_display,
    use_container_width=True,
    hide_index=True,
)

st.divider()


# =========================================================
# 8. 자산 분류별 비중
# =========================================================
left, right = st.columns(2)

with left:
    st.subheader("📁 대분류별")
    category_summary = (
        df.groupby("대분류", as_index=False)
        .agg({"현재평가금액(KRW)": "sum"})
        .sort_values("현재평가금액(KRW)", ascending=False)
    )

    category_summary["비중(%)"] = (
        category_summary["현재평가금액(KRW)"]
        / total_current * 100
        if total_current > 0
        else 0
    )

    cat_show = category_summary.copy()
    cat_show["현재평가금액(KRW)"] = cat_show[
        "현재평가금액(KRW)"
    ].map(lambda x: f"{x:,.0f}원")
    cat_show["비중(%)"] = cat_show["비중(%)"].map(
        lambda x: f"{x:.1f}%"
    )

    st.dataframe(
        cat_show,
        use_container_width=True,
        hide_index=True,
    )

with right:
    st.subheader("🧩 자산유형별")
    minor_summary = (
        df.groupby("소분류", as_index=False)
        .agg({"현재평가금액(KRW)": "sum"})
        .sort_values("현재평가금액(KRW)", ascending=False)
    )

    minor_summary["비중(%)"] = (
        minor_summary["현재평가금액(KRW)"]
        / total_current * 100
        if total_current > 0
        else 0
    )

    minor_show = minor_summary.copy()
    minor_show["현재평가금액(KRW)"] = minor_show[
        "현재평가금액(KRW)"
    ].map(lambda x: f"{x:,.0f}원")
    minor_show["비중(%)"] = minor_show["비중(%)"].map(
        lambda x: f"{x:.1f}%"
    )

    st.dataframe(
        minor_show,
        use_container_width=True,
        hide_index=True,
    )

st.divider()


# =========================================================
# 9. 종목별 상세 현황
# =========================================================
st.subheader("📋 종목별 상세 현황")

display_df = df[[
    "소유자",
    "자산/종목명",
    "금융사/거래소",
    "매수통화",
    "실제투입원금(KRW)",
    "현재평가금액(KRW)",
    "수익금(KRW)",
    "배당수익(USD)",
    "수익률(%)",
    "자산비중(%)",
    "상태",
]].copy()

display_df = display_df.sort_values(
    "현재평가금액(KRW)",
    ascending=False,
)

for col in [
    "실제투입원금(KRW)",
    "현재평가금액(KRW)",
    "수익금(KRW)",
]:
    display_df[col] = display_df[col].map(
        lambda x: f"{x:,.0f}" if is_valid_number(x) else "-"
    )

display_df["배당수익(USD)"] = display_df["배당수익(USD)"].map(
    lambda x: f"${x:,.2f}" if is_valid_number(x) else "-"
)

display_df["수익률(%)"] = display_df["수익률(%)"].map(
    lambda x: f"{x:+.2f}%" if is_valid_number(x) else "-"
)

display_df["자산비중(%)"] = display_df["자산비중(%)"].map(
    lambda x: f"{x:.1f}%" if is_valid_number(x) else "-"
)

total_row = pd.DataFrame([{
    "소유자": "-",
    "자산/종목명": "🔥 총합",
    "금융사/거래소": "-",
    "매수통화": "-",
    "실제투입원금(KRW)": f"{total_principal:,.0f}",
    "현재평가금액(KRW)": f"{total_current:,.0f}",
    "수익금(KRW)": f"{total_profit:+,.0f}",
    "배당수익(USD)": f"${total_dividend_usd:,.2f}",
    "수익률(%)": f"{total_return:+.2f}%",
    "자산비중(%)": "100.0%",
    "상태": "-",
}])

display_df = pd.concat(
    [display_df, total_row],
    ignore_index=True,
)

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    height=min(900, 80 + len(display_df) * 35),
)


# =========================================================
# 10. 외화자산 상세 분석
# =========================================================
foreign_df = df[
    (df["매수통화"].isin(["USD", "USDT", "JPY", "EUR", "CNY", "HKD", "GBP", "CAD", "AUD", "SGD", "CHF"]))
].copy()

with st.expander("💱 외화자산 원통화·환율 분석", expanded=False):
    if foreign_df.empty:
        st.info("외화자산이 없습니다.")
    else:
        foreign_show = foreign_df[[
            "소유자",
            "자산/종목명",
            "티커(기호)",
            "시세통화",
            "보유수량",
            "매수단가",
            "현재가",
            "추정취득환율",
            "현재환율",
            "가격효과(KRW)",
            "환율효과(KRW)",
            "수익금(KRW)",
            "배당수익(USD)",
        ]].copy()

        def fmt_num(x, digits=2):
            if not is_valid_number(x):
                return "-"
            return f"{float(x):,.{digits}f}"

        foreign_show["보유수량"] = foreign_show["보유수량"].map(
            lambda x: fmt_num(x, 8).rstrip("0").rstrip(".")
        )
        foreign_show["매수단가"] = foreign_show["매수단가"].map(
            lambda x: fmt_num(x, 4)
        )
        foreign_show["현재가"] = foreign_show["현재가"].map(
            lambda x: fmt_num(x, 4)
        )
        foreign_show["추정취득환율"] = foreign_show[
            "추정취득환율"
        ].map(
            lambda x: f"{x:,.2f}" if is_valid_number(x) else "-"
        )
        foreign_show["현재환율"] = foreign_show[
            "현재환율"
        ].map(
            lambda x: f"{x:,.2f}" if is_valid_number(x) else "-"
        )

        for col in [
            "가격효과(KRW)",
            "환율효과(KRW)",
            "수익금(KRW)",
        ]:
            foreign_show[col] = foreign_show[col].map(
                lambda x: f"{x:+,.0f}" if is_valid_number(x) else "-"
            )

        foreign_show["배당수익(USD)"] = foreign_show["배당수익(USD)"].map(
            lambda x: f"${x:,.2f}" if is_valid_number(x) else "-"
        )

        st.dataframe(
            foreign_show,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "※ 시트에 `평균취득환율`이 있으면 그 값을 우선 사용합니다. "
            "비어 있으면 `실제투입원금(KRW) ÷ 원통화 취득원가`로 추정합니다. "
            "수수료가 실제투입원금에 포함돼 있으면 추정 환율에 일부 섞일 수 있습니다."
        )


# =========================================================
# 11. 월별 History
# =========================================================
with st.expander("📜 월별 자산 성장 기록", expanded=False):
    if history_df.empty:
        st.info("아직 History 기록이 없습니다.")
    else:
        h = history_df.copy()

        for col in ["총 투입 원금", "현재 총 자산", "누적 배당수익(USD)"]:
            if col in h.columns:
                h[col] = h[col].map(to_number)

        if (
            "총 투입 원금" in h.columns
            and "현재 총 자산" in h.columns
        ):
            h["손익"] = (
                h["현재 총 자산"]
                - h["총 투입 원금"]
            )

        h = h.sort_values("날짜", ascending=False)

        h_show = h.copy()
        for col in ["총 투입 원금", "현재 총 자산", "손익"]:
            if col in h_show.columns:
                h_show[col] = h_show[col].map(
                    lambda x: f"{x:,.0f}원"
                )

        if "누적 배당수익(USD)" in h_show.columns:
            h_show["누적 배당수익(USD)"] = h_show["누적 배당수익(USD)"].map(
                lambda x: f"${x:,.2f}"
            )

        st.dataframe(
            h_show,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "History는 해당 월에 앱을 처음 연 시점의 평가액을 월별 1회 기록합니다."
        )


# =========================================================
# 12. 데이터 품질 / 시세 진단
# =========================================================
with st.expander("🧪 데이터·시세 진단", expanded=False):
    problems = df[
        (df["상태"] != "OK")
        | (df["실제투입원금(KRW)"] <= 0)
    ].copy()

    if problems.empty:
        st.success("현재 시트에서 치명적인 누락이나 시세 조회 오류가 발견되지 않았습니다.")
    else:
        st.warning(
            f"확인이 필요한 항목이 {len(problems)}개 있습니다."
        )

        diag = problems[[
            "소유자",
            "자산/종목명",
            "티커(기호)",
            "매수통화",
            "평가방식",
            "실제투입원금(KRW)",
            "상태",
        ]].copy()

        diag["실제투입원금(KRW)"] = diag[
            "실제투입원금(KRW)"
        ].map(lambda x: f"{x:,.0f}")

        st.dataframe(
            diag,
            use_container_width=True,
            hide_index=True,
        )

    st.markdown(
        """
**현재 시트 입력 규칙**

- `실제투입원금(KRW)` : 매수 당시 실제로 들어간 원화. **현재 환율로 다시 계산하지 않음**
- `배당수익(USD)` : 해당 자산에서 지금까지 받은 **세후 누적 달러 배당액**을 USD 그대로 입력
- 배당을 재투자해도 `실제투입원금(KRW)`에는 더하지 않음
- 대시보드에서는 USD 배당을 그대로 표시하고, 참고용으로 현재 USD/KRW 환율 기준 원화 환산액도 함께 표시
- 선택사항: J열을 `평균취득환율`로 바꾸면 외화자산의 실제 평균 환율을 직접 입력 가능
- 미국주식/코인 : `매수단가`는 USD 기준 평균매수가
- 국내주식/ETF : `매수단가`는 KRW 기준 평균매수가
- 외화 현금/RP : `보유수량`에 실제 USD 등 외화 잔액 입력
- 티커 없는 KRW 자산 : `보유수량 × 매수단가`를 현재가치로 사용
- 금(`GC=F`) : `보유수량`은 g 단위로 입력
        """
    )
