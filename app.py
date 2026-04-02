import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import json

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
# 2. 구글 API 연결 및 데이터 수집 (버튼 누를 때만 업데이트됨!)
# ---------------------------------------------------------
@st.cache_data
def load_data():
    # 1. 구글 시트 연결
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    secret_dict = json.loads(st.secrets["GCP_JSON"]) 
    credentials = Credentials.from_service_account_info(secret_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    sheet_url_key = "12hQFqNwUUqPr1Fhlqp5hT0nwhGKLI3mfGM0qBr0NM_w"
    worksheet = gc.open_by_key(sheet_url_key).sheet1
    rows = worksheet.get_all_values()
    df = pd.DataFrame(rows[1:], columns=rows[0])

    # 2. 데이터 청소
    cols_to_fix = ['보유수량', '매수단가', '투입원금(KRW)']
    for col in cols_to_fix:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)

    # 3. 실시간 시세 및 환율 수집 (여기로 들어왔습니다!)
    usd_krw_rate = yf.Ticker("KRW=X").history(period="1d")["Close"].iloc[-1]

    def get_current_price(ticker):
        if pd.isna(ticker) or str(ticker).strip() == '' or str(ticker).strip() == '-': return 0
        try: return yf.Ticker(str(ticker).strip()).history(period="1d")["Close"].iloc[-1]
        except: return 0

    df['현재가'] = df['티커(기호)'].apply(get_current_price)

    # 4. 자산 가치 평가
    def calculate_current_value(row):
        ticker = str(row['티커(기호)']).strip()
        if row['대분류'] == '현금성' and ticker == 'USD': return row['보유수량'] * 1.0 * usd_krw_rate  
        if ticker == 'GC=F': return row['보유수량'] * (row['현재가'] / 31.1034768) * usd_krw_rate
        if pd.isna(row['티커(기호)']) or ticker == '' or ticker == '-': return row['투입원금(KRW)']
        if row['매수통화'] in ['USD', 'USDT']: return row['보유수량'] * row['현재가'] * usd_krw_rate
        else: return row['보유수량'] * row['현재가']

    df['현재평가금액(KRW)'] = df.apply(calculate_current_value, axis=1)
    df['수익금(KRW)'] = df['현재평가금액(KRW)'] - df['투입원금(KRW)']
    
    return df, usd_krw_rate

with st.spinner("🔄 데이터를 불러오는 중입니다..."):
    # 캐시된(얼어있는) 데이터와 환율을 꺼내옵니다.
    df, usd_krw_rate = load_data()

# ---------------------------------------------------------
# 3. 화면 상단 요약 지표 (대시보드)
# ---------------------------------------------------------
total_principal = df['투입원금(KRW)'].sum()
total_current = df['현재평가금액(KRW)'].sum()
total_profit = df['수익금(KRW)'].sum()
total_rate = (total_profit / total_principal) * 100 if total_principal > 0 else 0

target_amount = 600000000
achievement_rate = (total_current / target_amount) * 100

col1, col2, col3 = st.columns(3)
col1.metric(label="💰 총 투입 원금", value=f"{total_principal:,.0f}원")
col2.metric(label="📈 현재 총 자산", value=f"{total_current:,.0f}원", delta=f"{total_profit:+,.0f}원 ({total_rate:+.2f}%)")
col3.metric(label="🎯 6억 목표 달성률 (2027년 연말)", value=f"{achievement_rate:.1f}%", delta=f"남은 금액: {target_amount - total_current:,.0f}원", delta_color="off")
st.markdown(f"*🔎 적용 환율: 1달러 = {usd_krw_rate:,.2f}원*")
st.markdown("---")

# ---------------------------------------------------------
# 4. ⭐️ 요청하신 '한눈에 보는 상세 표' (총합 포함)
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")

# 출력용 데이터프레임 조립
display_df = df[['소유자', '자산/종목명', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']].copy()
display_df['수익률(%)'] = np.where(display_df['투입원금(KRW)'] > 0, (display_df['수익금(KRW)'] / display_df['투입원금(KRW)']) * 100, 0)

# 총합 행 생성 및 병합
total_row = pd.DataFrame({
    '소유자': ['🔥총합🔥'], '자산/종목명': ['전체 자산'], '투입원금(KRW)': [total_principal], 
    '현재평가금액(KRW)': [total_current], '수익금(KRW)': [total_profit], '수익률(%)': [total_rate]
})
display_df = pd.concat([display_df, total_row], ignore_index=True)

# 금액 포맷팅 (웹에 예쁘게 보이도록)
for col in ['투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']:
    display_df[col] = display_df[col].map('{:,.0f}'.format)
display_df['수익률(%)'] = display_df['수익률(%)'].map('{:+.2f}%'.format)

# 웹에 표 출력
st.dataframe(display_df, use_container_width=True, hide_index=True)
st.markdown("---")

# ---------------------------------------------------------
# 5. 🔮 인터랙티브 복리 시뮬레이터 (직접 입력 방식)
# ---------------------------------------------------------
st.subheader("🔮 2027년 내 집 마련 시뮬레이터")
st.write("💡 **아래 표의 '월 적립금'과 '연 수익률' 칸을 더블클릭해서 자유롭게 숫자를 바꿔보세요!** 21개월(2027년 연말) 후의 최종 자산이 실시간으로 계산됩니다.")

# 현재 보유 자산을 종목명 기준으로 합산
current_assets = df.groupby('자산/종목명')['현재평가금액(KRW)'].sum()

# 초기 세팅값 (트레이더님의 평소 투자 계획)
default_pmt = {'ProShares QQQ 2X': 1000000, '비트코인': 600000, '이더리움': 400000, 'TIGER 미국배당다우존스': 1000000, '오클로': 180000, '프리포트 맥모란': 300000, 'Uranium ETF': 250000, '금': 450000}
default_rate = {'ProShares QQQ 2X': 15.0, '비트코인': 30.0, '이더리움': 30.0, 'TIGER 미국배당다우존스': 8.0, '오클로': 20.0, '프리포트 맥모란': 12.0, 'Uranium ETF': 15.0, '금': 5.0}

# 시뮬레이션 입력 표 만들기
sim_data = []
for asset, val in current_assets.items():
    sim_data.append({
        '자산/종목명': asset,
        '현재 자산(원)': val,
        '월 적립금(수정가능)': default_pmt.get(asset, 0),
        '연 수익률(%)(수정가능)': default_rate.get(asset, 0.0)
    })
sim_input_df = pd.DataFrame(sim_data)

# ⭐️ 사용자가 웹에서 직접 표의 숫자를 고칠 수 있게 해주는 마법의 기능!
edited_df = st.data_editor(
    sim_input_df, 
    disabled=['자산/종목명', '현재 자산(원)'], # 이 두 칸은 수정 불가
    use_container_width=True, hide_index=True,
    key="simulator_table_key"
)

# 수정된 숫자를 바탕으로 복리 재계산
total_future_value = 0
result_data = []

for idx, row in edited_df.iterrows():
    asset = row['자산/종목명']
    start_val = row['현재 자산(원)']
    pmt = row['월 적립금(수정가능)']
    annual_rate = row['연 수익률(%)(수정가능)'] / 100
    monthly_rate = annual_rate / 12
    
    # 21개월 복리 계산
    if monthly_rate > 0:
        fv_start = start_val * ((1 + monthly_rate) ** 21)
        fv_pmt = pmt * (((1 + monthly_rate) ** 21 - 1) / monthly_rate) * (1 + monthly_rate)
        fv_total = fv_start + fv_pmt
    else:
        fv_total = start_val + (pmt * 21)
        
    total_future_value += fv_total
    
    if start_val > 0 or pmt > 0:
        result_data.append({
            '자산/종목명': asset,
            '21개월간 투자할 총 원금': pmt * 21,
            '2027년 최종 예상금액': fv_total
        })

# 시뮬레이션 결과 요약
st.markdown("### 📊 시뮬레이션 결과")
if total_future_value >= target_amount:
    st.success(f"🎉 축하합니다! 이 계획대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 6억 목표를 달성합니다!")
else:
    st.warning(f"⚠️ 이 계획대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 목표까지 **{target_amount - total_future_value:,.0f}원**이 더 필요합니다.")

# 시뮬레이션 상세 결과 표
result_df = pd.DataFrame(result_data).sort_values(by='2027년 최종 예상금액', ascending=False)
for col in ['21개월간 투자할 총 원금', '2027년 최종 예상금액']:
    result_df[col] = result_df[col].map('{:,.0f}원'.format)
st.dataframe(result_df, use_container_width=True, hide_index=True)
