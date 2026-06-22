"""
app.py  —  Thin Film C-V / I-V Analyzer (Streamlit UI)
실행:  같은 폴더에 thinfilm_core.py 두고  ->  streamlit run app.py
"""
import os, tempfile
import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import thinfilm_core as tc

st.set_page_config(page_title="Thin Film C-V / I-V Analyzer", layout="wide")
st.title("🧪 Thin Film C–V / I–V Analyzer")
st.caption("MIM / MOS cap 측정 raw(CSV)에서 유전상수·누설·EOT를 계산하고 데이터 품질을 판정합니다.")

# ---------------- 사이드바: 시료 정보 (두 탭 공유) ----------------
with st.sidebar:
    st.header("① 시료 정보")
    structure = st.radio("구조", ["MIM", "MOS"], horizontal=True)
    film_mat  = st.selectbox("박막 재료", ["TiO2", "Bi2Ti4O11", "Al2O3", "HfO2", "SiO2", "기타"])
    elec_mat  = st.selectbox("전극 재료", ["W (텅스텐)", "TiN", "Ag", "Pt", "Au", "기타"])
    thickness = st.number_input("박막 두께 (nm)", min_value=0.1, value=39.0, step=1.0)
    radius    = st.number_input("전극 반지름 (µm)", min_value=0.1, value=100.0, step=10.0)

material  = None if film_mat == "기타" else film_mat
film_disp = film_mat if film_mat != "기타" else "N/A"
elec_disp = elec_mat.split(" (")[0]          # "W (텅스텐)" -> "W"
VERDICT_EN = {"사용 가능": "PASS", "주의 (조건부 사용)": "CAUTION", "사용 부적합": "FAIL"}

def save_uploads(files):
    paths = []
    for f in files:
        tmp = os.path.join(tempfile.gettempdir(), f.name)
        with open(tmp, "wb") as out:
            out.write(f.getbuffer())
        paths.append(tmp)
    return paths

def verdict_box(verdict):
    return st.success if verdict.startswith("사용 가능") else (st.error if "부적합" in verdict else st.warning)

tab_single, tab_batch = st.tabs(["📄 단일 측정", "📊 배치 분석 — 한 시료 (CV)"])

# ====================================================================
# 탭 1 : 단일 측정 (CV 또는 IV, IV 분할은 2개 병합)
# ====================================================================
with tab_single:
    files = st.file_uploader("측정 파일 (CSV)", type=["csv"],
                             accept_multiple_files=True, key="single")
    st.caption("IV 분할 측정은 음/양 두 파일을 함께 올리면 자동 병합됩니다 (−7~0 + 0~7 등 어떤 ± 범위든 OK).")

    if files:
        paths = save_uploads(files)
        arg = paths if len(paths) > 1 else paths[0]
        try:
            out = tc.analyze_full(arg, thickness, radius, structure=structure, material=material)
        except Exception as e:
            st.error(f"분석 실패: {e}")
            st.stop()
        res = out["result"]
        info = [f"{structure} | {film_disp} (d={thickness:g} nm)",
                f"Top elec: {elec_disp} (r={radius:g} um)"]

        if out["mode"] == "CV":
            info.append(f"f = {res['freq_kHz']:g} kHz" if res["freq_kHz"] else "f = ?")
            info.append(f"er(acc) = {res['er_accumulation']:.1f}   EOT = {res['EOT_nm']:.2f} nm")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("유전상수 εr (축적)", f"{res['er_accumulation']:.1f}")
            c2.metric("EOT", f"{res['EOT_nm']:.2f} nm")
            c3.metric("평균 Capacitance", f"{res['avg_C_pF']:.1f} pF")
            td = res["tan_delta_accumulation"]
            c4.metric("tan δ (축적)", f"{td:.3f}" if td is not None else "N/A")
            verdict_box(res["verdict"])(f"판정: {res['verdict']}")
            for r_ in res["verdict_reasons"]:
                st.write("• " + r_)
            cur = res["curve"]
            fig, ax = plt.subplots(figsize=(7.5, 5))
            ax.plot(cur["V"], cur["Cp_pF"], "-o", ms=2, color="#1f6feb")
            ax.set_xlabel("Bias (V)"); ax.set_ylabel("Capacitance (pF)")
            ax.set_title("C–V"); ax.grid(alpha=0.3)
            info.append(f"Verdict: {VERDICT_EN.get(res['verdict'], res['verdict'])}")
            ax.text(0.02, 0.98, "\n".join(info), transform=ax.transAxes, va="top",
                    bbox=dict(boxstyle="round", fc="white", ec="#888", alpha=0.9))
            st.pyplot(fig)
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
            ax.set_title("J–E (leakage)"); ax.grid(alpha=0.3, which="both")
            info.append(f"Jmax = {res['J_max_Acm2']:.1e} A/cm2")
            ax.text(0.02, 0.98, "\n".join(info), transform=ax.transAxes, va="top",
                    bbox=dict(boxstyle="round", fc="white", ec="#888", alpha=0.9))
            st.pyplot(fig)

        with st.expander("원본 결과(raw) 보기"):
            st.json(res)

# ====================================================================
# 탭 2 : 배치 분석 (한 시료, CV) — best + 분포
# ====================================================================
with tab_batch:
    st.markdown(
        "**같은 시료의 CV 측정 파일을 여러 개(수십~100개) 올리면**, 각각을 분석·품질판정해서 "
        "**가장 잘 측정된 것**과 **유효 측정의 분포(중앙값 ± 편차)**를 보여줍니다. "
        "접촉 불량 같은 실패 측정은 자동으로 걸러집니다."
    )
    bfiles = st.file_uploader("CV 파일 여러 개 (모두 같은 시료)", type=["csv"],
                              accept_multiple_files=True, key="batch")

    if bfiles:
        bpaths = save_uploads(bfiles)
        try:
            b = tc.cv_batch(bpaths, thickness, radius, structure=structure, material=material)
        except Exception as e:
            st.error(f"배치 분석 실패: {e}")
            st.stop()

        stats, best = b["stats"], b["best"]
        if stats is None:
            st.error("유효한 CV 측정이 없어요. (모두 읽기 실패했거나 CV 파일이 아닐 수 있어요.)")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("올린 파일", f"{stats['n_total']}개")
            c2.metric("분석 성공", f"{stats['n_ok']}개")
            c3.metric("유효(집계대상)", f"{stats['n_valid']}개")
            c4.metric("εr 중앙값", f"{stats['er_median']:.1f}")

            if stats.get("all_failed"):
                st.warning("품질판정을 통과한 측정이 없어, '그나마 나은' 것 기준으로 표시합니다. (장비·접촉 상태 점검 권장)")

            st.info(
                f"**대표 유전상수 εr = 중앙값 {stats['er_median']:.1f} "
                f"(평균 {stats['er_mean']:.1f} ± {stats['er_std']:.1f}, "
                f"범위 {stats['er_min']:.1f}~{stats['er_max']:.1f}, N={stats['n_valid']})**"
            )

            if best:
                br = best["rep"]
                st.subheader(f"🏆 가장 잘 측정된 것 — {best['name']}")
                cc1, cc2, cc3, cc4 = st.columns(4)
                cc1.metric("εr (축적)", f"{br['er_accumulation']:.1f}")
                cc2.metric("EOT", f"{br['EOT_nm']:.2f} nm")
                cc3.metric("tan δ", f"{br['tan_delta_accumulation']:.3f}")
                cc4.metric("판정", VERDICT_EN.get(br["verdict"], br["verdict"]))

                cur = br["curve"]
                fig, ax = plt.subplots(figsize=(7.5, 5))
                ax.plot(cur["V"], cur["Cp_pF"], "-o", ms=2, color="#1f6feb")
                ax.set_xlabel("Bias (V)"); ax.set_ylabel("Capacitance (pF)")
                ax.set_title("C–V  (best of batch)"); ax.grid(alpha=0.3)
                box = [f"{structure} | {film_disp} (d={thickness:g} nm)",
                       f"Top elec: {elec_disp} (r={radius:g} um)",
                       f"er(acc)={br['er_accumulation']:.1f}  tan d={br['tan_delta_accumulation']:.2f}",
                       f"valid N={stats['n_valid']}, er med={stats['er_median']:.1f}"]
                ax.text(0.02, 0.98, "\n".join(box), transform=ax.transAxes, va="top",
                        bbox=dict(boxstyle="round", fc="white", ec="#888", alpha=0.9))
                st.pyplot(fig)

            st.subheader("전체 측정 요약")
            rows = []
            for r in b["results"]:
                if r.get("ok"):
                    rp = r["rep"]
                    rows.append({
                        "파일": r["name"],
                        "εr": round(rp["er_accumulation"], 1) if rp["er_accumulation"] is not None else None,
                        "tan δ": round(rp["tan_delta_accumulation"], 3) if rp["tan_delta_accumulation"] is not None else None,
                        "판정": rp["verdict"],
                    })
                else:
                    rows.append({"파일": r["name"], "εr": None, "tan δ": None,
                                 "판정": "읽기실패: " + r.get("error", "")})
            st.dataframe(rows, use_container_width=True)
