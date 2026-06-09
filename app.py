import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import json
from datetime import datetime

# ---------------------------------------------------------
# 1. 웹 앱 기본 설정 및 새로고침 버튼
# ---------------------------------------------------------
st.set_page_config(page_title="우리가족 자산 대시보드", layout="wide")
st.title("👨‍👩‍👧 우리 가족 통합 자산 대시보드")

if st.button("🔄 최신 데이터 불러오기 (구글시트/실시간시세 반영)"):
    st.cache_data.clear()
    st.rerun()

st.markdown("---")

# ---------------------------------------------------------
# 2. 구글 API 연결 및 데이터 수집 (History 연동 포함)
# ---------------------------------------------------------
@st.cache_data
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    secret_dict = json.loads(st.secrets["GCP_JSON"]) 
    credentials = Credentials.from_service_account_info(secret_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    
    sheet_url_key = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
    spreadsheet = gc.open_by_key(sheet_url_key)
    worksheet = spreadsheet.sheet1
    rows = worksheet.get_all_values()
    df = pd.DataFrame(rows[1:], columns=rows[0])

    # 빈 칸 제거 및 데이터 클렌징
    df = df[df['자산/종목명'].astype(str).str.strip() != '']
    cols_to_fix = ['보유수량', '매수단가', '투입원금(KRW)']
    for col in cols_to_fix:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',', '').str.replace('₩', '').str.replace('$', '').str.strip(), 
            errors='coerce'
        ).fillna(0)

    # 시세 및 환율
    usd_krw_rate = yf.Ticker("KRW=X").history(period="1d")["Close"].iloc[-1]
    
    def get_current_price(ticker):
        if pd.isna(ticker) or str(ticker).strip() in ['', '-']: return 0
        try: return yf.Ticker(str(ticker).strip()).history(period="1d")["Close"].iloc[-1]
        except: return 0

    df['현재가'] = df['티커(기호)'].apply(get_current_price)

    # 금 시세 조정
    def adjust_gold_price(row):
        if str(row['티커(기호)']).strip() == 'GC=F':
            return (row['현재가'] / 31.1034768) * usd_krw_rate
        return row['현재가']
    df['현재가'] = df.apply(adjust_gold_price, axis=1)

    # 자산 가치 계산
    def calculate_current_value(row):
        ticker = str(row['티커(기호)']).strip()
        if row['대분류'] == '현금성' and ticker == 'USD': return row['보유수량'] * usd_krw_rate  
        if pd.isna(row['티커(기호)']) or ticker in ['', '-']: return row['투입원금(KRW)']
        if row['매수통화'] in ['USD', 'USDT']: return row['보유수량'] * row['현재가'] * usd_krw_rate
        return row['보유수량'] * row['현재가']

    df['현재평가금액(KRW)'] = df.apply(calculate_current_value, axis=1)
    df['수익금(KRW)'] = df['현재평가금액(KRW)'] - df['투입원금(KRW)']

    # --- 매월 1일 히스토리 기록 로직 ---
    total_p = df['투입원금(KRW)'].sum()
    total_c = df['현재평가금액(KRW)'].sum()
    
    try:
        h_worksheet = spreadsheet.worksheet("History")
    except:
        h_worksheet = spreadsheet.add_worksheet(title="History", rows="100", cols="5")
        h_worksheet.append_row(["날짜", "총 투입 원금", "현재 총 자산"])
    
    h_data = h_worksheet.get_all_records()
    h_df = pd.DataFrame(h_data)
    
    now = datetime.now()
    today_str = now.strftime("%Y-%m-01")
    if now.day == 1:
        if h_df.empty or today_str not in h_df['날짜'].astype(str).values:
            h_worksheet.append_row([today_str, int(total_p), int(total_c)])
            h_df = pd.DataFrame(h_worksheet.get_all_records()) # 갱신
    
    return df, usd_krw_rate, h_df

with st.spinner("🔄 데이터를 불러오고 히스토리를 체크하는 중..."):
    df, usd_krw_rate, history_df = load_data()

# ---------------------------------------------------------
# 3. 화면 상단 요약 지표
# ---------------------------------------------------------
total_principal = df['투입원금(KRW)'].sum()
total_current = df['현재평가금액(KRW)'].sum()
total_profit = df['수익금(KRW)'].sum()
total_rate = (total_profit / total_principal) * 100 if total_principal > 0 else 0

col1, col2 = st.columns(2)
col1.metric(label="💰 총 투입 원금", value=f"{total_principal:,.0f}원")
col2.metric(label="📈 현재 총 자산", value=f"{total_current:,.0f}원", delta=f"{total_profit:+,.0f}원 ({total_rate:+.2f}%)")
st.markdown(f"*🔎 적용 환율: 1달러 = {usd_krw_rate:,.2f}원*")
st.markdown("---")

# ---------------------------------------------------------
# 4. 종목별 상세 현황
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")
display_df = df[['소유자', '자산/종목명', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']].copy()
display_df['수익률(%)'] = np.where(display_df['투입원금(KRW)'] > 0, (display_df['수익금(KRW)'] / display_df['투입원금(KRW)']) * 100, 0)
display_df['자산비중(%)'] = (display_df['현재평가금액(KRW)'] / total_current) * 100 if total_current > 0 else 0
display_df = display_df.sort_values(by='현재평가금액(KRW)', ascending=False)

# 포맷팅
formatted_df = display_df.copy()
for col in ['투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']:
    formatted_df[col] = formatted_df[col].map('{:,.0f}'.format)
formatted_df['수익률(%)'] = formatted_df['수익률(%)'].map('{:+.2f}%'.format)
formatted_df['자산비중(%)'] = formatted_df['자산비중(%)'].map('{:.1f}%'.format)

st.dataframe(formatted_df, use_container_width=True, hide_index=True, height=(len(display_df)*36)+40)
st.markdown("---")

# ---------------------------------------------------------
# 5. ⭐️ [새 기능] 대분류별 요약 표
# ---------------------------------------------------------
st.subheader("📁 대분류별 자산 비중")
cat_df = df.groupby('대분류').agg({'현재평가금액(KRW)': 'sum'}).reset_index()
cat_df['비중(%)'] = (cat_df['현재평가금액(KRW)'] / total_current) * 100 if total_current > 0 else 0
cat_df = cat_df.sort_values(by='현재평가금액(KRW)', ascending=False)

# 포맷팅
cat_df['현재평가금액(KRW)'] = cat_df['현재평가금액(KRW)'].map('{:,.0f}원'.format)
cat_df['비중(%)'] = cat_df['비중(%)'].map('{:.1f}%'.format)

st.table(cat_df) # 요약표는 깔끔하게 static table로 표시
st.markdown("---")

# ---------------------------------------------------------
# 6. ⭐️ [새 기능] 월별 히스토리 기록 (Expander)
# ---------------------------------------------------------
if not history_df.empty:
    latest_date = history_df['날짜'].iloc[-1]
    with st.expander(f"📜 월별 자산 성장 기록 (최근 기록: {latest_date})"):
        st.write("매월 1일의 총 자산 상태가 자동으로 기록됩니다.")
        
        # 히스토리용 포맷팅
        h_display = history_df.copy().sort_values(by='날짜', ascending=False)
        for col in ['총 투입 원금', '현재 총 자산']:
            h_display[col] = h_display[col].map('{:,.0f}원'.format)
        
        st.dataframe(h_display, use_container_width=True, hide_index=True)
else:
    st.info("아직 기록된 히스토리가 없습니다. 매월 1일에 첫 기록이 시작됩니다.")
