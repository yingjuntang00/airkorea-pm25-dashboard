import requests
import pandas as pd
from datetime import timedelta

import streamlit as st
import plotly.express as px

# 🔑 AirKorea에서 발급받은 Decoding Key 입력
DEC_KEY = st.secrets["AIRKOREA_DEC_KEY"]

# ----------------------------
# 1️⃣ 시도별 측정소 목록 가져오기
# ----------------------------
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
    items = r.json().get("response", {}).get("body", {}).get("items", [])
    return sorted({it["stationName"] for it in items})

# ----------------------------
# 2️⃣ 특정 측정소 최근 시간대 데이터
# ----------------------------
def station_recent_hours(station, data_term="MONTH", target="pm25"):
    url = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
    params = {
        "serviceKey": DEC_KEY,
        "returnType": "json",
        "numOfRows": 100,
        "pageNo": 1,
        "stationName": station,
        "dataTerm": data_term,
        "ver": "1.3",
    }

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    items = r.json().get("response", {}).get("body", {}).get("items", [])

    if not items:
        return pd.DataFrame(columns=["datetime", target, "station"])

    df = pd.DataFrame(items)
    df = df.rename(columns={"dataTime": "datetime", target + "Value": target})

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df[target] = pd.to_numeric(df[target], errors="coerce")

    df = df.dropna(subset=["datetime", target])
    return df[["datetime", target]].assign(station=station)

# ----------------------------
# 3️⃣ 도시 단위 시계열 평균
# ----------------------------
def city_hourly_series(sido_kr, city_en, target="pm25", n_hours=48, max_stations=6):

    stations = list_stations(sido_kr)
    frames = []

    for st_name in stations[:max_stations]:
        try:
            frames.append(station_recent_hours(st_name, data_term="MONTH", target=target))
        except:
            pass

    if not frames:
        return pd.DataFrame(columns=["datetime", target, "city"])

    df = pd.concat(frames, ignore_index=True)

    tmax = df["datetime"].max()
    cutoff = tmax - timedelta(hours=n_hours)
    df = df[df["datetime"] >= cutoff]

    hourly = df.groupby("datetime", as_index=False)[target].mean()
    hourly["city"] = city_en
    return hourly

SIDO_MAP = {"서울": "Seoul", "인천": "Incheon", "대전": "Daejeon", "부산": "Busan"}

def load_all_cities(n_hours=48, pollutant="pm25"):
    frames = []
    for kr, en in SIDO_MAP.items():
        frames.append(city_hourly_series(kr, en, target=pollutant, n_hours=n_hours))
    return pd.concat(frames, ignore_index=True)

# ----------------------------
# Streamlit UI 시작
# ----------------------------
st.set_page_config(page_title="AirKorea Dashboard", layout="wide")

st.title("한국 주요 도시 실시간 공기질 대시보드 (Streamlit + AirKorea API)")

st.sidebar.header("설정")
n_hours = st.sidebar.slider("최근 몇 시간까지 볼까요?", 24, 168, 48, step=24)
city_options = list(SIDO_MAP.values())
selected_cities = st.sidebar.multiselect("도시 선택", city_options, default=city_options)

pollutant = st.sidebar.selectbox("오염물질 선택", ["pm25", "pm10", "o3", "no2"])

if st.sidebar.button("데이터 새로고침"):
    st.experimental_rerun()

# ----------------------------
# 데이터 로딩
# ----------------------------
with st.spinner("AirKorea API에서 데이터를 불러오는 중..."):
    ts = load_all_cities(n_hours=n_hours, pollutant=pollutant)

if ts.empty:
    st.error("데이터 로딩 실패")
    st.stop()

ts_sel = ts[ts["city"].isin(selected_cities)]

# ----------------------------
# 라인 그래프
# ----------------------------
fig = px.line(
    ts_sel,
    x="datetime",
    y=pollutant,
    color="city",
    title=f"{pollutant.upper()} — 최근 {n_hours}시간 도시별 평균"
)

fig.update_layout(xaxis_title="Datetime", yaxis_title=pollutant.upper())

# ----------------------------
# 평균 / 최대 / 최소 표시
# ----------------------------
if not ts_sel.empty:

    mean_val = ts_sel[pollutant].mean()
    max_row = ts_sel.loc[ts_sel[pollutant].idxmax()]
    min_row = ts_sel.loc[ts_sel[pollutant].idxmin()]

    fig.add_hline(
        y=mean_val,
        line_dash="dot",
        line_color="blue",
        annotation_text=f"평균 {mean_val:.1f}",
    )

    fig.add_scatter(
        x=[max_row["datetime"]],
        y=[max_row[pollutant]],
        mode="markers+text",
        text=[f"최대 {max_row[pollutant]:.1f}"],
        marker=dict(color="red", size=12),
    )

    fig.add_scatter(
        x=[min_row["datetime"]],
        y=[min_row[pollutant]],
        mode="markers+text",
        text=[f"최소 {min_row[pollutant]:.1f}"],
        marker=dict(color="green", size=12),
    )

st.plotly_chart(fig, use_container_width=True)

# ================================================================
#  🔥 최근 시각 Snapshot 요약 + 색상 표시 테이블
# ================================================================
latest_t = ts_sel["datetime"].max()
snap = ts_sel[ts_sel["datetime"] == latest_t]
summary = snap.groupby("city")[pollutant].agg(["mean", "max", "std"]).round(2).reset_index()

# PM2.5 등급
def pm25_grade(v):
    if v <= 15: return "좋음"
    if v <= 35: return "보통"
    if v <= 75: return "나쁨"
    return "매우나쁨"

# 색상
def pm25_color(g):
    return {
        "좋음": "#4CAF50",
        "보통": "#2196F3",
        "나쁨": "#FF9800",
        "매우나쁨": "#F44336"
    }.get(g, "gray")

summary["grade"] = summary["mean"].apply(pm25_grade)
summary["color"] = summary["grade"].apply(pm25_color)

st.subheader(f"가장 최근 시각 기준 요약 ( {latest_t} )")
def highlight(row):
    return [f"background-color:{row['color']}" for _ in row]

st.dataframe(summary.style.apply(highlight, axis=1), use_container_width=True)