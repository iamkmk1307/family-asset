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

if st.button("🔄 최신 데이터 불러오기"):
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

    # 시세 및 환율 수집
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
            h_df = pd.DataFrame(h_worksheet.get_all_records())
    
    return df, usd_krw_rate, h_df

with st.spinner("🔄 데이터를 불러오는 중입니다..."):
    df, usd_krw_rate, history_df = load_data()

# ---------------------------------------------------------
# 3. ⭐️ [변경점 2] 화면 상단 요약 지표 및 히스토리 우측 배치
# ---------------------------------------------------------
total_principal = df['투입원금(KRW)'].sum()
total_current = df['현재평가금액(KRW)'].sum()
total_profit = df['수익금(KRW)'].sum()
total_rate = (total_profit / total_principal) * 100 if total_principal > 0 else 0

# 상단 공간을 3개 컬럼 레이아웃으로 분할
col1, col2, col3 = st.columns([1, 1, 1.8])

# 왼쪽과 중앙에 지표 배치
col1.metric(label="💰 총 투입 원금", value=f"{total_principal:,.0f}원")
col2.metric(label="📈 현재 총 자산", value=f"{total_current:,.0f}원", delta=f"{total_profit:+,.0f}원 ({total_rate:+.2f}%)")

# 오른쪽 남는 공간(col3)에 접이식 히스토리 배치
with col3:
    if not history_df.empty:
        latest_date = history_df['날짜'].iloc[-1]
        with st.expander(f"📜 월별 자산 성장 기록 (최근: {latest_date})", expanded=False):
            h_display = history_df.copy().sort_values(by='날짜', ascending=False)
            for col in ['총 투입 원금', '현재 총 자산']:
                h_display[col] = h_display[col].map('{:,.0f}원'.format)
            st.dataframe(h_display, use_container_width=True, hide_index=True, height=160)
    else:
        st.info("아직 기록된 히스토리가 없습니다.")

st.markdown(f"*🔎 적용 환율: 1달러 = {usd_krw_rate:,.2f}원*")
st.markdown("---")

# ---------------------------------------------------------
# 4. 종목별 상세 현황 (스크롤 없이 확장 완료)
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

# 맨 아래 총합 행 추가
total_row = pd.DataFrame({
    '소유자': ['-'], '자산/종목명': ['🔥총합🔥'], '투입원금(KRW)': [f"{total_principal:,.0f}"], 
    '현재평가금액(KRW)': [f"{total_current:,.0f}"], '수익금(KRW)': [f"{total_profit:+,.0f}"], 
    '수익률(%)': [f"{total_rate:+.2f}%"], '자산비중(%)': ['100.0%']
})
formatted_df = pd.concat([formatted_df, total_row], ignore_index=True)

st.dataframe(formatted_df, use_container_width=True, hide_index=True, height=(len(display_df)*36)+80)
st.markdown("---")

# ---------------------------------------------------------
# 5. ⭐️ [변경점 1] 대분류별 요약 표 (대분류 전면 배치, 소분류 종류 매핑)
# ---------------------------------------------------------
st.subheader("📁 대분류별 자산 비중")

# 대분류 기준으로 묶되, 속한 종목명들을 중복 없이 결합하여 소분류 칸 생성
cat_df = df.groupby('대분류').agg({
    '현재평가금액(KRW)': 'sum',
    '자산/종목명': lambda x: ", ".join(dict.fromkeys(x.astype(str).str.strip()))
}).reset_index()

cat_df['비중(%)'] = (cat_df['현재평가금액(KRW)'] / total_current) * 100 if total_current > 0 else 0
cat_df = cat_df.sort_values(by='현재평가금액(KRW)', ascending=False)

# 컬럼 순서 재배치 (대분류 맨 앞으로) 및 이름 매핑
cat_df = cat_df[['대분류', '자산/종목명', '현재평가금액(KRW)', '비중(%)']]
cat_df.columns = ['대분류', '소분류 종류', '현재평가금액', '비중(%)']

# 출력용 포맷팅
cat_df['현재평가금액'] = cat_df['현재평가금액'].map('{:,.0f}원'.format)
cat_df['비중(%)'] = cat_df['비중(%)'].map('{:.1f}%'.format)

# hide_index=True 옵션으로 앞쪽 정렬용 인덱스 숫자 완벽 제거
st.dataframe(cat_df, use_container_width=True, hide_index=True)
