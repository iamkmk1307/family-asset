import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import json

# ---------------------------------------------------------
# 1. 웹 앱 기본 설정 및 새로고침 버튼
# ---------------------------------------------------------
st.set_page_config(page_title="우리가족 자산 대시보드", layout="wide")
st.title("👨‍👩‍👧 우리 가족 통합 자산 대시보드")

if st.button("🔄 데이터 불러오기"):
    st.cache_data.clear()
    st.rerun()

st.markdown("---")

# ---------------------------------------------------------
# 2. 구글 API 연결 및 데이터 수집
# ---------------------------------------------------------
@st.cache_data
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    secret_dict = json.loads(st.secrets["GCP_JSON"]) 
    credentials = Credentials.from_service_account_info(secret_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    
    sheet_url_key = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
    worksheet = gc.open_by_key(sheet_url_key).sheet1
    rows = worksheet.get_all_values()
    df = pd.DataFrame(rows[1:], columns=rows[0])

    # 구글 시트의 유령 빈칸 완벽 제거
    df = df[df['자산/종목명'].astype(str).str.strip() != '']

    cols_to_fix = ['보유수량', '매수단가', '투입원금(KRW)']
    for col in cols_to_fix:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',', '').str.replace('₩', '').str.replace('$', '').str.strip(), 
            errors='coerce'
        ).fillna(0)

    usd_krw_rate = yf.Ticker("KRW=X").history(period="1d")["Close"].iloc[-1]

    def get_current_price(ticker):
        if pd.isna(ticker) or str(ticker).strip() == '' or str(ticker).strip() == '-': return 0
        try: return yf.Ticker(str(ticker).strip()).history(period="1d")["Close"].iloc[-1]
        except: return 0

    df['현재가'] = df['티커(기호)'].apply(get_current_price)

    def adjust_gold_price(row):
        ticker = str(row['티커(기호)']).strip()
        if ticker == 'GC=F':
            return (row['현재가'] / 31.1034768) * usd_krw_rate
        return row['현재가']
        
    df['현재가'] = df.apply(adjust_gold_price, axis=1)

    def calculate_current_value(row):
        ticker = str(row['티커(기호)']).strip()
        if row['대분류'] == '현금성' and ticker == 'USD': return row['보유수량'] * 1.0 * usd_krw_rate  
        if pd.isna(row['티커(기호)']) or ticker == '' or ticker == '-': return row['투입원금(KRW)']
        if row['매수통화'] in ['USD', 'USDT']: return row['보유수량'] * row['현재가'] * usd_krw_rate
        else: return row['보유수량'] * row['현재가']

    df['현재평가금액(KRW)'] = df.apply(calculate_current_value, axis=1)
    df['수익금(KRW)'] = df['현재평가금액(KRW)'] - df['투입원금(KRW)']
    
    return df, usd_krw_rate

with st.spinner("🔄 데이터를 불러오는 중입니다..."):
    df, usd_krw_rate = load_data()

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
# 4. ⭐️ 종목별 상세 현황 (소유자 분리 & 스크롤 제거)
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")

# 압축 해제: 원본 데이터를 그대로 띄웁니다 (소유자 유지)
display_df = df[['소유자', '자산/종목명', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']].copy()
display_df['수익률(%)'] = np.where(display_df['투입원금(KRW)'] > 0, (display_df['수익금(KRW)'] / display_df['투입원금(KRW)']) * 100, 0)

if total_current > 0:
    display_df['자산비중(%)'] = (display_df['현재평가금액(KRW)'] / total_current) * 100
else:
    display_df['자산비중(%)'] = 0.0

# 덩어리(평가금액)가 큰 순서대로 내림차순 정렬
display_df = display_df.sort_values(by='현재평가금액(KRW)', ascending=False)

# 맨 아래 총합 행 추가
total_row = pd.DataFrame({
    '소유자': ['-'], '자산/종목명': ['🔥총합🔥'], '투입원금(KRW)': [total_principal], 
    '현재평가금액(KRW)': [total_current], '수익금(KRW)': [total_profit], '수익률(%)': [total_rate], '자산비중(%)': [100.0]
})
display_df = pd.concat([display_df, total_row], ignore_index=True)

# 화면에 예쁘게 출력하기 위한 포맷팅
for col in ['투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']:
    display_df[col] = display_df[col].map('{:,.0f}'.format)
display_df['수익률(%)'] = display_df['수익률(%)'].map('{:+.2f}%'.format)
display_df['자산비중(%)'] = display_df['자산비중(%)'].map('{:.1f}%'.format)

# ⭐️ [핵심] 표 세로 길이 동적 계산 (데이터 행 개수 * 줄 높이 36픽셀 + 여백 40픽셀)
dynamic_height = (len(display_df) * 36) + 40

# 웹에 표 출력 (height 옵션을 주어 스크롤바 삭제)
st.dataframe(display_df, use_container_width=True, hide_index=True, height=dynamic_height)
