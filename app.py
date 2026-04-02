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
# 2. 구글 API 연결 및 데이터 수집 (시세/환율 포함 캐시 처리)
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

    # 2. 데이터 청소 (₩, $ 기호 완벽 제거 업데이트 완료)
    cols_to_fix = ['보유수량', '매수단가', '투입원금(KRW)']
    for col in cols_to_fix:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',', '').str.replace('₩', '').str.replace('$', '').str.strip(), 
            errors='coerce'
        ).fillna(0)

    # 3. 실시간 시세 및 환율 수집
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
# 4. ⭐️ [업데이트] 자산/종목명 단위 도넛 차트 (시뮬레이터 위쪽 배치)
# ---------------------------------------------------------
st.subheader("📊 종목별 자산 비중")

# 자산/종목명 기준으로 데이터 통합 (이름이 같은 종목은 합칩니다)
item_portfolio = df.groupby('자산/종목명')['현재평가금액(KRW)'].sum().reset_index().sort_values(by='현재평가금액(KRW)', ascending=False)

# 크고 깔끔한 단일 그래프 생성
fig = go.Figure(data=[go.Pie(
    labels=item_portfolio['자산/종목명'], 
    values=item_portfolio['현재평가금액(KRW)'], 
    hole=0.4, # 도넛 모양
    textinfo='percent+label', # 화면에 이름과 % 표시
    textfont=dict(size=14), # 글자 크기 조정
    textposition='inside', # 텍스트 위치를 파이 안쪽으로
    insidetextorientation='radial' # 텍스트가 조각 모양을 따라 퍼지게
)])

fig.update_layout(
    hoverlabel=dict(bgcolor="white", font_size=16),
    showlegend=False # 종목이 많아 범례는 숨기고 직접 차트에 표시
)

# 마우스를 올렸을 때 정확한 금액과 비중 표시
fig.update_traces(hovertemplate='<b>%{label}</b><br>평가금액: %{value:,.0f}원<br>비중: %{percent}')

# 그래프 출력!
st.plotly_chart(fig, use_container_width=True)
st.markdown("---")

# ---------------------------------------------------------
# 5. [기존 유지] 종목별 상세 현황 표
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")

display_df = df[['소유자', '자산/종목명', '티커(기호)', '보유수량', '현재가', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']].copy()
display_df['수익률(%)'] = np.where(display_df['투입원금(KRW)'] > 0, (display_df['수익금(KRW)'] / display_df['투입원금(KRW)']) * 100, 0)

# 총합 행 생성 및 병합
total_row = pd.DataFrame({
    '소유자': ['🔥총합🔥'], '자산/종목명': ['전체 자산'], '투입원금(KRW)': [total_principal], 
    '현재평가금액(KRW)': [total_current], '수익금(KRW)': [total_profit], '수익률(%)': [total_rate]
})
display_df = pd.concat([display_df, total_row], ignore_index=True)

# 포맷팅 (웹에 예쁘게 보이도록)
for col in ['보유수량', '현재가', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']:
    display_df[col] = display_df[col].map('{:,.0f}'.format)
display_df['수익률(%)'] = display_df['수익률(%)'].map('{:+.2f}%'.format)

# 웹에 표 출력 (key 추가로 깜빡임 방지 완료)
st.dataframe(display_df, use_container_width=True, hide_index=True, key="detail_table_key")
st.markdown("---")

# ---------------------------------------------------------
# 6. ⭐️ [업데이트] 목표가 기반 복리 시뮬레이터 (역산 방식)
# ---------------------------------------------------------
st.subheader("🔮 2027년 목표가 달성 시뮬레이터")
st.write("💡 **아래 표의 '월 적립금'과 '목표가격' 칸을 더블클릭해서 자유롭게 수정해보세요!** 목표가격까지 도달하기 위한 필요한 연 수익률이 자동으로 역산되어 계산됩니다.")

# 현재 자산 종목명 기준으로 통합
grouped_assets = df.groupby(['자산/종목명', '티커(기호)', '매수통화'])['현재가', '보유수량', '현재평가금액(KRW)'].agg({
    '현재가': 'last', '보유수량': 'sum', '현재평가금액(KRW)': 'sum'
}).reset_index()

# 초기 세팅값
default_pmt = {'ProShares QQQ 2X': 1000000, '비트코인': 600000, '이더리움': 400000, 'TIGER 미국배당다우존스': 1000000, '오클로': 180000, '프리포트 맥모란': 300000, 'Uranium ETF': 250000, '금': 450000}
# 목표가는 현재가의 2배로 임시 세팅 (사용자가 웹에서 고치게 유도)
grouped_assets['목표가격(수정가능)'] = grouped_assets['현재가'] * 2.0 
# 현금이나 티커 없는 자산은 목표가 세팅 제외
grouped_assets.loc[grouped_assets['티커(기호)'].isna() | (grouped_assets['티커(기호)'] == '') | (grouped_assets['티커(기호)'] == '-'), '목표가격(수정가능)'] = grouped_assets['현재가']

sim_data = []
for idx, row in grouped_assets.iterrows():
    asset = row['자산/종목명']
    ticker = row['티커(기호)']
    currency = row['매수통화']
    pmt = default_pmt.get(asset, 0)
    current_price = row['현재가']
    target_price = row['목표가격(수정가능)']

    # ⭐️ 복리 역산 수식 적용 (현재가 -> 목표가)
    # n = 1.75년 (2026년 4월 ~ 2027년 12월 말 = 약 21개월 = 1.75년)
    years_left = 1.75
    if current_price > 0 and target_price > current_price and pd.notna(ticker) and ticker != '':
        # r = (FV/PV)^(1/n) - 1
        required_annual_rate = (((target_price / current_price) ** (1 / years_left)) - 1) * 100
    else:
        required_annual_rate = 0.0

    sim_data.append({
        '자산/종목명': asset,
        '매수통화': currency,
        '현재가': current_price,
        '월 적립금(수정가능)': pmt,
        '목표가격(수정가능)': target_price,
        '필요 연수익률(%)': required_annual_rate,
        '현재 자산(원)': row['현재평가금액(KRW)']
    })

sim_input_df = pd.DataFrame(sim_data)

# 에디터 표 출력
edited_df = st.data_editor(
    sim_input_df, 
    disabled=['자산/종목명', '매수통화', '현재가', '현재 자산(원)', '필요 연수익률(%)'],
    use_container_width=True, hide_index=True,
    key="target_sim_table_key" # 깜빡임 방지 필수!
)

# 수정된 수치를 바탕으로 최종 계산
total_future_value = 0
result_data = []

for idx, row in edited_df.iterrows():
    asset = row['자산/종목명']
    current_price = row['현재가']
    target_price = row['목표가격(수정가능)']
    start_val = row['현재 자산(원)']
    pmt = row['월 적립금(수정가능)']
    currency = row['매수통화']

    # ⭐️ 다시 한번 역산 수익률 구하기 (사용자가 목표가를 고쳤을 때를 대비)
    years_left = 1.75
    if current_price > 0 and target_price > current_price:
        required_annual_rate = (((target_price / current_price) ** (1 / years_left)) - 1) * 100
    else:
        required_annual_rate = 0.0

    # 복리 계산용 월 수익률
    monthly_rate = (required_annual_rate / 100) / 12
    
    # 21개월 복리 계산
    if monthly_rate > 0:
        fv_start = start_val * ((1 + monthly_rate) ** 21)
        # 미국 자산은 목표가 달성 시 환율 변동까지 반영하는 것으로 시뮬레이션
        if currency in ['USD', 'USDT']:
             fv_start *= (usd_krw_rate / usd_krw_rate) # 단순화: 현재 환율 고정 가정
        fv_pmt = pmt * (((1 + monthly_rate) ** 21 - 1) / monthly_rate) * (1 + monthly_rate)
        fv_total = fv_start + fv_pmt
    else:
        fv_total = start_val + (pmt * 21)
        
    total_future_value += fv_total
    
    if start_val > 0 or pmt > 0:
        result_data.append({
            '자산/종목명': asset,
            '매수통화': currency,
            '설정된 필요 연수익률': required_annual_rate,
            '2027년 최종 예상금액': fv_total
        })

# 결과 출력
st.markdown("### 📊 시뮬레이션 결과")
if total_future_value >= target_amount:
    st.success(f"🎉 축하합니다! 이 목표가 계획대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 6억 목표를 초과 달성합니다!")
else:
    st.warning(f"⚠️ 이 목표가 계획대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 목표까지 **{target_amount - total_future_value:,.0f}원**이 더 필요합니다.")

# 상세 결과 표
result_df = pd.DataFrame(result_data).sort_values(by='2027년 최종 예상금액', ascending=False)
result_df['설정된 필요 연수익률'] = result_df['설정된 필요 연수익률'].map('{:+.2f}%'.format)
result_df['2027년 최종 예상금액'] = result_df['2027년 최종 예상금액'].map('{:,.0f}원'.format)
st.dataframe(result_df, use_container_width=True, hide_index=True)
