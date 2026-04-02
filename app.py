import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import json

# ---------------------------------------------------------
# 1. 웹 앱 기본 설정
# ---------------------------------------------------------
st.set_page_config(page_title="우리가족 자산 대시보드", layout="wide")
st.title("👨‍👩‍👧 우리 가족 통합 자산 대시보드")
st.markdown("---")

# ---------------------------------------------------------
# 2. 구글 API 연결 (Streamlit 보안 비밀고 사용)
# ---------------------------------------------------------
# 주의: 로컬 코랩과 달리, 웹에서는 st.secrets 기능을 통해 안전하게 JSON 키를 읽어옵니다.
@st.cache_resource
def get_google_sheet():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    # 스트림릿 비밀금고에서 JSON 텍스트를 읽어와서 파이썬 딕셔너리로 변환합니다.
    secret_dict = json.loads(st.secrets["GCP_JSON"]) 
    credentials = Credentials.from_service_account_info(secret_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    
    # 트레이더님의 구글 시트 고유 키
    sheet_url_key = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w" 
    
    worksheet = gc.open_by_key(sheet_url_key).sheet1
    rows = worksheet.get_all_values()
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df

with st.spinner("🔄 실시간 시세 및 환율을 불러오는 중입니다..."):
    df = get_google_sheet()

    # 데이터 청소
    cols_to_fix = ['보유수량', '매수단가', '투입원금(KRW)']
    for col in cols_to_fix:
        df[col] = pd.to_numeric(df[col].str.replace(',', '').str.strip(), errors='coerce').fillna(0)

    # 환율 및 실시간 시세 수집
    usd_krw_rate = yf.Ticker("KRW=X").history(period="1d")["Close"].iloc[-1]

    def get_current_price(ticker):
        if pd.isna(ticker) or str(ticker).strip() == '' or str(ticker).strip() == '-': return 0
        try: return yf.Ticker(str(ticker).strip()).history(period="1d")["Close"].iloc[-1]
        except: return 0

    df['현재가'] = df['티커(기호)'].apply(get_current_price)

    # 자산 가치 평가
    def calculate_current_value(row):
        ticker = str(row['티커(기호)']).strip()
        if row['대분류'] == '현금성' and ticker == 'USD': return row['보유수량'] * 1.0 * usd_krw_rate  
        if ticker == 'GC=F': return row['보유수량'] * (row['현재가'] / 31.1034768) * usd_krw_rate
        if pd.isna(row['티커(기호)']) or ticker == '' or ticker == '-': return row['투입원금(KRW)']
        if row['매수통화'] in ['USD', 'USDT']: return row['보유수량'] * row['현재가'] * usd_krw_rate
        else: return row['보유수량'] * row['현재가']

    df['현재평가금액(KRW)'] = df.apply(calculate_current_value, axis=1)
    df['수익금(KRW)'] = df['현재평가금액(KRW)'] - df['투입원금(KRW)']

# ---------------------------------------------------------
# 3. 화면 상단 요약 지표 (대시보드)
# ---------------------------------------------------------
total_principal = df['투입원금(KRW)'].sum()
total_current = df['현재평가금액(KRW)'].sum()
total_profit = df['수익금(KRW)'].sum()
total_rate = (total_profit / total_principal) * 100 if total_principal > 0 else 0

target_amount = 600000000
achievement_rate = (total_current / target_amount) * 100

# st.columns로 화면을 3칸으로 나누어 깔끔하게 배치합니다.
col1, col2, col3 = st.columns(3)
col1.metric(label="💰 총 투입 원금", value=f"{total_principal:,.0f}원")
col2.metric(label="📈 현재 총 자산", value=f"{total_current:,.0f}원", delta=f"{total_profit:+,.0f}원 ({total_rate:+.2f}%)")
col3.metric(label="🎯 6억 목표 달성률", value=f"{achievement_rate:.1f}%", delta=f"남은 금액: {target_amount - total_current:,.0f}원", delta_color="off")

st.markdown(f"*🔎 적용 환율: 1달러 = {usd_krw_rate:,.2f}원*")
st.markdown("---")

# ---------------------------------------------------------
# 4. 자산 비중 그래프 (단일 도넛 차트)
# ---------------------------------------------------------
st.subheader("📊 자산 카테고리 비중")
family_portfolio = df.groupby('대분류')['현재평가금액(KRW)'].sum().reset_index().sort_values(by='현재평가금액(KRW)', ascending=False)

fig = go.Figure(data=[go.Pie(
    labels=family_portfolio['대분류'], values=family_portfolio['현재평가금액(KRW)'], 
    hole=0.4, textinfo='percent+label', textfont=dict(size=15),
    marker=dict(colors=['#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', '#19D3F3'])
)])
fig.update_layout(hoverlabel=dict(bgcolor="white", font_size=16), showlegend=True)
fig.update_traces(hovertemplate='<b>%{label}</b><br>평가금액: %{value:,.0f}원<br>비중: %{percent}')

# 스트림릿 전용 그래프 출력 명령어
st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------
# 5. 인터랙티브 5억 달성 시뮬레이터
# ---------------------------------------------------------
st.subheader("🔮 2027년 내 집 마련 시뮬레이터 (마우스로 조절해보세요!)")

# 화면을 두 칸으로 나누어 왼쪽은 슬라이더, 오른쪽은 결과 표를 보여줍니다.
sim_col1, sim_col2 = st.columns([1, 2])

with sim_col1:
    st.write("**예상 연평균 수익률 조정**")
    rate_qqq = st.slider("ProShares QQQ 2