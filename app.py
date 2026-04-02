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

if st.button("🔄 최신 데이터 불러오기 (구글시트/실시간시세 반영)"):
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
# 3. 화면 상단 요약 지표
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
# 4. 종목별 상세 현황 표 (+ 자산 비중)
# ---------------------------------------------------------
st.subheader("📋 종목별 상세 현황")

display_df = df[['소유자', '자산/종목명', '투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']].copy()
display_df['수익률(%)'] = np.where(display_df['투입원금(KRW)'] > 0, (display_df['수익금(KRW)'] / display_df['투입원금(KRW)']) * 100, 0)

if total_current > 0:
    display_df['자산비중(%)'] = (display_df['현재평가금액(KRW)'] / total_current) * 100
else:
    display_df['자산비중(%)'] = 0.0

total_row = pd.DataFrame({
    '소유자': ['🔥총합🔥'], '자산/종목명': ['전체 자산'], '투입원금(KRW)': [total_principal], 
    '현재평가금액(KRW)': [total_current], '수익금(KRW)': [total_profit], '수익률(%)': [total_rate], '자산비중(%)': [100.0]
})
display_df = pd.concat([display_df, total_row], ignore_index=True)

for col in ['투입원금(KRW)', '현재평가금액(KRW)', '수익금(KRW)']:
    display_df[col] = display_df[col].map('{:,.0f}'.format)
display_df['수익률(%)'] = display_df['수익률(%)'].map('{:+.2f}%'.format)
display_df['자산비중(%)'] = display_df['자산비중(%)'].map('{:.1f}%'.format)

st.dataframe(display_df, use_container_width=True, hide_index=True, key="detail_table_key")
st.markdown("---")

# ---------------------------------------------------------
# 5. ⭐️ [업데이트] 다중 통화 & 콤마 표시가 완벽 지원되는 시뮬레이터
# ---------------------------------------------------------
st.subheader("🔮 2027년 내 집 마련 시뮬레이터 (목표가격 기반)")
st.write("💡 **'월 적립금'과 '목표가격' 칸을 더블클릭해서 수정해보세요!** 엑셀처럼 자동으로 콤마가 찍히며 실시간으로 계산됩니다.")

current_assets = df.groupby('자산/종목명')['현재평가금액(KRW)'].sum()
asset_prices = df.groupby('자산/종목명')['현재가'].first()
# ⭐️ 구글 시트에서 '통화' 기준 가져오기
asset_currencies = df.groupby('자산/종목명')['매수통화'].first() 

default_pmt = {'ProShares QQQ 2X': 1000000, '비트코인': 600000, '이더리움': 400000, 'TIGER 미국배당다우존스': 1000000, '오클로': 180000, '프리포트 맥모란': 300000, 'Uranium ETF': 250000, '금': 450000}

sim_data = []
for asset, val in current_assets.items():
    curr_price = float(asset_prices.get(asset, 0))
    currency = str(asset_currencies.get(asset, 'KRW')).strip()
    if currency == 'nan' or currency == '' or currency == 'None': 
        currency = 'KRW'
        
    target_price = curr_price if curr_price > 0 else 0.0
    
    sim_data.append({
        '자산/종목명': asset,
        '통화': currency,  # 👈 통화 표시 추가!
        '현재 자산(원)': val,
        '월 적립금(수정가능)': default_pmt.get(asset, 0),
        '현재가격': curr_price,
        '목표가격(수정가능)': target_price
    })

sim_input_df = pd.DataFrame(sim_data)

# ⭐️ 스트림릿의 column_config를 이용해 콤마(,) 시각적 디자인 입히기
edited_df = st.data_editor(
    sim_input_df, 
    disabled=['자산/종목명', '통화', '현재 자산(원)', '현재가격'],
    column_config={
        "현재 자산(원)": st.column_config.NumberColumn(format="%,.0f"),
        "월 적립금(수정가능)": st.column_config.NumberColumn(format="%,.0f", step=10000),
        "현재가격": st.column_config.NumberColumn(format="%,.2f"),
        "목표가격(수정가능)": st.column_config.NumberColumn(format="%,.2f", step=1.0),
    },
    use_container_width=True, hide_index=True,
    key="simulator_table_key" 
)

# 복리 재계산 로직
total_future_value = 0
result_data = []

for idx, row in edited_df.iterrows():
    asset = row['자산/종목명']
    start_val = row['현재 자산(원)']
    pmt = row['월 적립금(수정가능)']
    curr_p = row['현재가격']
    target_p = row['목표가격(수정가능)']
    currency = row['통화']
    
    # 목표가격을 통한 연수익률 역산 (약 21개월 = 1.75년)
    years_left = 1.75
    if curr_p > 0 and target_p > curr_p:
        annual_rate = (((target_p / curr_p) ** (1 / years_left)) - 1)
    else:
        annual_rate = 0.0

    monthly_rate = annual_rate / 12
    
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
            '통화': currency,
            '21개월간 투자할 총 원금': pmt * 21,
            '목표가 달성 필요 연수익률': f"{annual_rate * 100:+.1f}%",
            '2027년 최종 예상금액': fv_total
        })

# 시뮬레이션 결과 요약
st.markdown("### 📊 시뮬레이션 결과")
if total_future_value >= target_amount:
    st.success(f"🎉 축하합니다! 이 목표가대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 6억 목표를 달성합니다!")
else:
    st.warning(f"⚠️ 이 목표가대로라면 21개월 후 **총 {total_future_value:,.0f}원**으로, 목표까지 **{target_amount - total_future_value:,.0f}원**이 더 필요합니다.")

# 시뮬레이션 상세 결과 표
result_df = pd.DataFrame(result_data).sort_values(by='2027년 최종 예상금액', ascending=False)
for col in ['21개월간 투자할 총 원금', '2027년 최종 예상금액']:
    result_df[col] = result_df[col].map('{:,.0f}원'.format)
st.dataframe(result_df, use_container_width=True, hide_index=True)
