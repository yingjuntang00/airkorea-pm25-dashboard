import requests
import pandas as pd
from datetime import timedelta

import streamlit as st
import plotly.express as px

# 🔑 AirKorea에서 발급받은 Decoding Key 입력
DEC_KEY = st.secrets["AIRKOREA_DEC_KEY"]

# 1️⃣ 시도별 측정소 목록 가져오기
def list_stations(sido):
    url = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"
    params = {
        "serviceKey": DEC_KEY,
        "returnType": "json",
        "numOfRows": 100,
        "pageNo": 1,
        "sidoName": sido,
        "ver": "1.3"
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    items = data.get("response", {}).get("body", {}).get("items", [])
    return sorted({it["stationName"] for it in items})

# 2️⃣ 측정소별 최근 PM2.5 시간대 데이터
def station_recent_hours(station, data_term="MONTH"):
    url = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
    params = {
        "serviceKey": DEC_KEY,
        "returnType": "json",
        "numOfRows": 100,
        "pageNo": 1,
        "stationName": station,
        "dataTerm": data_term,  # DAILY / MONTH / 3MONTH
        "ver": "1.3",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    items = data.get("response", {}).get("body", {}).get("items", [])
    if not items:
        return pd.DataFrame(columns=["datetime", "pm25", "station"])
    df = pd.DataFrame(items).rename(columns={"dataTime": "datetime", "pm25Value": "pm25"})
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["pm25"] = pd.to_numeric(df["pm25"], errors="coerce")
    df = df.dropna(subset=["datetime", "pm25"])
    return df[["datetime", "pm25"]].assign(station=station)

# 3️⃣ 도시 단위 시계열 생성 (여러 측정소 평균)
def city_hourly_series(sido_kr, city_en, n_hours=48, max_stations=6):
    try:
        stations = list_stations(sido_kr)
    except Exception as e:
        st.warning(f"{sido_kr} 측정소 목록 불러오기 실패: {e}")
        return pd.DataFrame(columns=["datetime", "pm25", "city"])

    frames = []
    for st_name in stations[:max_stations]:
        try:
            frames.append(station_recent_hours(st_name, data_term="MONTH"))
        except Exception as e:
            st.write(f"{sido_kr} - {st_name} 데이터 실패: {e}")
    if not frames:
        return pd.DataFrame(columns=["datetime", "pm25", "city"])

    df = pd.concat(frames, ignore_index=True)
    if df["datetime"].isna().all():
        return pd.DataFrame(columns=["datetime", "pm25", "city"])

    tmax = df["datetime"].max()
    cutoff = tmax - timedelta(hours=n_hours)
    df = df[df["datetime"] >= cutoff]

    hourly = df.groupby("datetime", as_index=False)["pm25"].mean()
    hourly["city"] = city_en
    return hourly

SIDO_MAP = {"서울": "Seoul", "인천": "Incheon", "대전": "Daejeon", "부산": "Busan"}

def load_all_cities(n_hours=48):
    all_lines = []
    for kr, en in SIDO_MAP.items():
        all_lines.append(city_hourly_series(kr, en, n_hours=n_hours, max_stations=6))
    ts = pd.concat(all_lines, ignore_index=True)
    return ts

# ========== Streamlit UI 시작 ==========

st.set_page_config(page_title="AirKorea PM2.5 Dashboard", layout="wide")

st.title("한국 주요 도시 PM2.5 실시간 대시보드 (Streamlit + AirKorea API)")
st.write("서울, 인천, 대전, 부산의 최근 시간대별 PM2.5 변화를 비교합니다.")

# Sidebar 옵션
st.sidebar.header("설정")
n_hours = st.sidebar.slider("최근 몇 시간까지 볼까요?", min_value=24, max_value=168, value=48, step=24)

city_options = list(SIDO_MAP.values())
selected_cities = st.sidebar.multiselect(
    "도시 선택",
    options=city_options,
    default=city_options
)

if st.sidebar.button("데이터 새로고침"):
    st.experimental_rerun()

st.info(f"현재 설정: 최근 {n_hours}시간 / 도시: {', '.join(selected_cities)}")

# 데이터 불러오기
with st.spinner("AirKorea API에서 데이터를 불러오는 중입니다..."):
    ts = load_all_cities(n_hours=n_hours)

if ts.empty:
    st.error("데이터를 불러오지 못했습니다. API 키 또는 네트워크 상태를 확인해주세요.")
else:
    ts_sel = ts[ts["city"].isin(selected_cities)]

    # 라인 그래프
    fig = px.line(
        ts_sel,
        x="datetime",
        y="pm25",
        color="city",
        title=f"PM2.5 — 최근 {n_hours}시간 도시별 평균",
    )
    fig.update_layout(
        xaxis_title="Datetime",
        yaxis_title="PM2.5 (㎍/㎥)",
        legend_title="City"
    )
    st.plotly_chart(fig, use_container_width=True)

    # 요약 통계 (가장 최근 시각 스냅샷)
    latest_t = ts_sel["datetime"].max()
    snap = ts_sel[ts_sel["datetime"] == latest_t]
    summary = snap.groupby("city")["pm25"].agg(["mean", "max", "std"]).round(2).reset_index()
    st.subheader(f"가장 최근 시각 기준 요약 ( {latest_t} )")
    st.dataframe(summary)
