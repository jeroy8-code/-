"""
app.py  —  Thin Film C-V / I-V Analyzer (Streamlit UI)
실행:  같은 폴더에 thinfilm_core.py 두고  ->  streamlit run app.py
"""
import os, tempfile
import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
# 한글 폰트 (윈도우=맑은고딕, 맥=AppleGothic, 리눅스=나눔고딕)
matplotlib.rcParams["font.family"] = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import thinfilm_core as tc

st.set_page_config(page_title="Thin Film C-V / I-V Analyzer", layout="wide")
st.title("🧪 Thin Film C–V / I–V Analyzer")
st.caption("MIM / MOS cap 측정 raw(CSV)에서 유전상수·누설·EOT를 계산하고 데이터 품질을 판정합니다.")

# ---------------- 입력 패널 ----------------
with st.sidebar:
    st.header("① 시료 정보")
    structure = st.radio("구조", ["MIM", "MOS"], horizontal=True)
    film_mat  = st.selectbox("박막 재료", ["TiO2", "Bi2Ti4O11", "Al2O3", "HfO2", "SiO2", "기타"])
    elec_mat  = st.selectbox("전극 재료", ["W (텅스텐)", "TiN", "Ag", "Pt", "Au", "기타"])
    thickness = st.number_input("박막 두께 (nm)", min_value=0.1, value=39.0, step=1.0)
    radius    = st.number_input("전극 반지름 (µm)", min_value=0.1, value=100.0, step=10.0)
    st.divider()
    st.header("② 측정 파일")
    files = st.file_uploader("CSV 업로드", type=["csv"], accept_multiple_files=True)
    st.caption("IV 분할 측정은 음/양 두 파일을 함께 올리면 자동 병합됩니다 (−7~0 + 0~7 등 어떤 ± 범위든 OK).")

if not files:
    st.info("◀ 왼쪽에서 시료 정보를 입력하고 CSV를 업로드하세요.")
    st.stop()

# 업로드 파일 임시 저장
paths = []
for f in files:
    tmp = os.path.join(tempfile.gettempdir(), f.name)
    with open(tmp, "wb") as out:
        out.write(f.getbuffer())
    paths.append(tmp)

material = None if film_mat == "기타" else film_mat
arg = paths if len(paths) > 1 else paths[0]

# 그래프 박스용 영문 라벨 (폰트 안전)
film_disp = film_mat if film_mat != "기타" else "N/A"
elec_disp = elec_mat.split(" (")[0]   # "W (텅스텐)" -> "W"
VERDICT_EN = {"사용 가능": "PASS", "주의 (조건부 사용)": "CAUTION", "사용 부적합": "FAIL"}

try:
    out = tc.analyze_full(arg, thickness, radius, structure=structure, material=material)
except Exception as e:
    st.error(f"분석 실패: {e}")
    st.stop()

res = out["result"]
info_lines = [
    f"{structure} | {film_disp} (d={thickness:g} nm)",
    f"Top elec: {elec_disp} (r={radius:g} um)",
]

# ---------------- CV 결과 ----------------
if out["mode"] == "CV":
    info_lines.append(f"f = {res['freq_kHz']:g} kHz" if res["freq_kHz"] else "f = ?")
    info_lines.append(f"er(acc) = {res['er_accumulation']:.1f}   EOT = {res['EOT_nm']:.2f} nm")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("유전상수 εr (축적)", f"{res['er_accumulation']:.1f}")
    c2.metric("EOT", f"{res['EOT_nm']:.2f} nm")
    c3.metric("평균 Capacitance", f"{res['avg_C_pF']:.1f} pF")
    td = res["tan_delta_accumulation"]
    c4.metric("tan δ (축적)", f"{td:.3f}" if td is not None else "N/A")

    # 판정
    v = res["verdict"]
    box = st.success if v.startswith("사용 가능") else (st.error if "부적합" in v else st.warning)
    box(f"판정: {v}")
    for r_ in res["verdict_reasons"]:
        st.write("• " + r_)

    # 그래프 (캡쳐용 정보 박스 포함)
    cur = res["curve"]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.plot(cur["V"], cur["Cp_pF"], "-o", ms=2, color="#1f6feb")
    ax.set_xlabel("Bias (V)"); ax.set_ylabel("Capacitance (pF)")
    ax.set_title("C–V")
    ax.grid(alpha=0.3)
    info_lines.append(f"Verdict: {VERDICT_EN.get(v, v)}")
    ax.text(0.02, 0.98, "\n".join(info_lines), transform=ax.transAxes,
            va="top", ha="left", fontsize=9,
            bbox=dict(boxstyle="round", fc="white", ec="#888", alpha=0.9))
    st.pyplot(fig)

# ---------------- IV 결과 ----------------
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("J @ −1V", f"{res['leakage_at_-1V']['J_Acm2']:.2e} A/cm²")
    c2.metric("J @ +1V", f"{res['leakage_at_+1V']['J_Acm2']:.2e} A/cm²")
    c3.metric("J max", f"{res['J_max_Acm2']:.2e} A/cm²")
    st.caption(f"전기장 도달범위: {res['E_range_MVcm'][0]:.2f} ~ {res['E_range_MVcm'][1]:.2f} MV/cm  (병합 {res['n_points']}점)")

    cur = res["curve"]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.semilogy(cur["E_MVcm"], cur["J_Acm2"], "-o", ms=2, color="#cf222e")
    ax.set_xlabel("Electric Field (MV/cm)"); ax.set_ylabel("|J| (A/cm²)")
    ax.set_title("J–E (leakage)")
    ax.grid(alpha=0.3, which="both")
    info_lines.append(f"Jmax = {res['J_max_Acm2']:.1e} A/cm²")
    ax.text(0.02, 0.98, "\n".join(info_lines), transform=ax.transAxes,
            va="top", ha="left", fontsize=9,
            bbox=dict(boxstyle="round", fc="white", ec="#888", alpha=0.9))
    st.pyplot(fig)

with st.expander("원본 결과(raw) 보기"):
    st.json(res)
