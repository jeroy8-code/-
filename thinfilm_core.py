"""
thinfilm_core.py
MIM cap CV/IV 측정 raw(csv)를 읽어 유전상수(er)와 누설전류(J)를 계산하는 코어.

설계 원칙
- 시료마다 두께/반지름이 다르므로, 이 값들은 '함수 인자'로 받는다 (절대 하드코딩/시료간 혼용 금지).
- 장비가 미리 계산해 넣은 Field/Current Density 컬럼은 신뢰하지 않고, raw V/I로 직접 재계산한다.
  (장비는 소프트웨어 설정에 남아있던 두께/면적으로 그 컬럼을 만들기 때문)
- CV / IV 모드는 파일의 컬럼 헤더로 자동 판별한다.
"""

import math
import os
import statistics

EPS0 = 8.854e-12      # 진공 유전율 [F/m]
# 참고: 기존 엑셀은 3.14 / 8.85e-12 를 썼음. 차이는 er 기준 약 0.1% 수준.


# ----------------------------------------------------------------------
# 1) 파서 : 장비 csv 한 개를 읽어 mode/metadata/data 로 분해
#    컬럼을 '정확한 이름'이 아니라 '의미(역할)'로 인식하므로
#    오타(Voltgae), 컬럼 수 차이, 장비별 라벨 차이를 모두 견딘다.
# ----------------------------------------------------------------------
def _map_roles(header):
    """헤더 컬럼명 리스트 -> {역할: 인덱스}. 대소문자/오타/띄어쓰기에 관대."""
    roles = {}
    for idx, name in enumerate(header):
        n = name.strip().lower()
        # 전압 : 'volt'(voltage/voltgae 오타) 또는 'bias', 단 AC는 제외
        if ("volt" in n or "bias" in n) and "ac" not in n:
            roles.setdefault("V", idx)
        elif "ac" in n and "volt" in n:
            roles.setdefault("AC", idx)
        # 전류(raw) : 'current' 이되 density/abs 는 제외
        if "current" in n and "density" not in n and "abs" not in n:
            roles.setdefault("I", idx)
        if "current density" in n:
            roles.setdefault("J", idx)
        # 용량(CV)
        if n.startswith("cp") or "capacit" in n:
            roles.setdefault("Cp", idx)
        # 컨덕턴스 G
        if n == "g [s]" or n.startswith("g [") or "conductance" in n:
            roles.setdefault("G", idx)
        if "freq" in n:
            roles.setdefault("freq", idx)
    return roles


def parse_csv(path):
    raw = open(path, "rb").read().decode("cp949", errors="replace")
    lines = raw.splitlines()

    meta = {"date": None}
    header = header_idx = roles = None
    for i, l in enumerate(lines):
        ll = l.lower()
        if ll.startswith("# date"):
            parts = l.lstrip("# ").split(",")
            meta["date"] = ",".join(parts[1:]) if len(parts) > 1 else parts[0]
            continue
        if ll.startswith("# mode"):
            meta["mode_label"] = l.split(",", 1)[-1].strip()
            continue
        if l.startswith("#") or l.strip() == "":
            continue
        cand = [c.strip() for c in l.split(",")]
        r = _map_roles(cand)
        # 헤더 후보 : 전압 + (전류 또는 용량) 컬럼이 보이면 그 줄이 헤더
        if "V" in r and ("I" in r or "Cp" in r):
            header, header_idx, roles = cand, i, r
            break
    if header is None:
        raise ValueError(f"컬럼 헤더를 찾지 못함(전압/전류 컬럼 인식 실패): {path}")

    mode = "CV" if "Cp" in roles else "IV"
    col = {name: idx for idx, name in enumerate(header)}

    vcol = roles["V"]
    rows = []
    for l in lines[header_idx + 1:]:
        p = l.split(",")
        if len(p) < len(header):
            continue
        if p[0].startswith("Append") or (vcol < len(p) and p[vcol].strip() == ""):
            continue
        try:
            rows.append([float(x) if x.strip() != "" else None for x in p[:len(header)]])
        except ValueError:
            continue

    return {"mode": mode, "meta": meta, "col": col, "roles": roles, "rows": rows}


def _area_cm2(radius_um):
    r_cm = radius_um * 1e-4          # um -> cm
    return math.pi * r_cm ** 2


# ----------------------------------------------------------------------
# 2) CV 분석 : Cp -> 유전상수, tan delta, 히스테리시스
# ----------------------------------------------------------------------
def analyze_cv(parsed, thickness_nm, radius_um):
    assert parsed["mode"] == "CV"
    c = parsed["roles"]
    d_m = thickness_nm * 1e-9
    A_m2 = math.pi * (radius_um * 1e-6) ** 2

    V, Cp, G, er, tand = [], [], [], [], []
    freq = None
    for r in parsed["rows"]:
        v = r[c["V"]]
        cp = r[c["Cp"]]
        g = r[c["G"]] if "G" in c else None
        f = r[c["freq"]] if "freq" in c else None
        if None in (v, cp):
            continue
        freq = f
        e = cp * d_m / (EPS0 * A_m2)           # er = C*d / (e0*A)
        w = 2 * math.pi * f if f else None
        td = (g / (w * cp)) if (g is not None and w and cp) else None
        V.append(v); Cp.append(cp); G.append(g); er.append(e); tand.append(td)

    # 보고값 : 최대 용량(=accumulation) 지점의 er, 그리고 V~0 에서의 er
    i_max = max(range(len(er)), key=lambda i: er[i])
    i_0v = min(range(len(V)), key=lambda i: abs(V[i]))

    # 신뢰도 : er_max 지점의 tan delta 가 크면 용량값이 누설에 묻힌 것 -> er 신뢰 불가
    td_at_max = tand[i_max]
    if td_at_max is None:
        reliability = "unknown"
    elif td_at_max < 0.1:
        reliability = "good"
    elif td_at_max < 1.0:
        reliability = "caution (tan d %.2f)" % td_at_max
    else:
        reliability = "UNRELIABLE: tan d=%.2f (손실>>용량, er 무의미)" % td_at_max

    # 히스테리시스 : 왕복 스윕이면 정/역방향 분리해 같은 전압에서 용량차 확인
    hysteresis = None
    i_vmax = max(range(len(V)), key=lambda i: V[i])
    if 0 < i_vmax < len(V) - 3:            # 중간에서 전압이 꺾이면 왕복으로 판단
        fwd = list(zip(V[:i_vmax + 1], Cp[:i_vmax + 1]))
        rev = list(zip(V[i_vmax:], Cp[i_vmax:]))
        # 0V 부근에서 forward/reverse 용량차
        cf = min(fwd, key=lambda x: abs(x[0]))[1]
        cr = min(rev, key=lambda x: abs(x[0]))[1]
        hysteresis = {"round_trip": True, "dC_at_0V_pF": (cr - cf) * 1e12}
    else:
        hysteresis = {"round_trip": False}

    return {
        "frequency_Hz": freq,
        "er_max": er[i_max], "er_at_Vmax_capacitance": V[i_max],
        "er_at_0V": er[i_0v],
        "tan_delta_at_0V": tand[i_0v],
        "tan_delta_at_er_max": td_at_max,
        "er_reliability": reliability,
        "Cp_max_pF": max(Cp) * 1e12, "Cp_min_pF": min(Cp) * 1e12,
        "hysteresis": hysteresis,
        "curve": {"V": V, "Cp_pF": [x * 1e12 for x in Cp], "er": er, "tan_delta": tand},
    }


# ----------------------------------------------------------------------
# 3) IV 분석 : raw V/I -> 전기장(E), 누설전류밀도(J). 분할 브랜치 병합.
# ----------------------------------------------------------------------
def analyze_iv(parsed_list, thickness_nm, radius_um):
    """parsed_list : IV 파일 1개 또는 [음브랜치, 양브랜치] 처럼 여러 개를 넘기면 병합."""
    if isinstance(parsed_list, dict):
        parsed_list = [parsed_list]
    d_cm = thickness_nm * 1e-7
    A_cm2 = _area_cm2(radius_um)

    pts = []   # (V, E_MVcm, J_Acm2)
    for parsed in parsed_list:
        assert parsed["mode"] == "IV"
        c = parsed["roles"]
        for r in parsed["rows"]:
            v = r[c["V"]]
            i = r[c["I"]]
            if None in (v, i):
                continue
            E = (v / d_cm) / 1e6           # V/cm -> MV/cm
            J = abs(i) / A_cm2             # A/cm2
            pts.append((v, E, J))

    pts.sort(key=lambda x: x[0])
    V = [p[0] for p in pts]; E = [p[1] for p in pts]; J = [p[2] for p in pts]

    def J_at_voltage(target_v):
        k = min(range(len(V)), key=lambda i: abs(V[i] - target_v))
        return {"V": V[k], "E_MVcm": E[k], "J_Acm2": J[k]}

    return {
        "n_points": len(V), "V_min": V[0], "V_max": V[-1],
        "E_range_MVcm": (min(E), max(E)),
        "leakage_at_-1V": J_at_voltage(-1.0),
        "leakage_at_+1V": J_at_voltage(1.0),
        "J_max_Acm2": max(J),
        "curve": {"V": V, "E_MVcm": E, "J_Acm2": J},
    }


# ----------------------------------------------------------------------
# 4) 통합 진입점
# ----------------------------------------------------------------------
def analyze(path_or_list, thickness_nm, radius_um):
    paths = [path_or_list] if isinstance(path_or_list, str) else path_or_list
    parsed = [parse_csv(p) for p in paths]
    if parsed[0]["mode"] == "CV":
        return {"mode": "CV", "result": analyze_cv(parsed[0], thickness_nm, radius_um)}
    else:
        return {"mode": "IV", "result": analyze_iv(parsed, thickness_nm, radius_um)}


# ======================================================================
# 5) 종합 CV 분석 + 데이터 품질 판정 (도핑 몰라도 가능한 최대치)
# ======================================================================
MATERIAL_K_RANGE = {           # 재료별 유전상수 기대범위 (대략)
    "TiO2": (20, 120),         # anatase~30-45, rutile~80-110
    "Bi2Ti4O11": (5, 100),
    "Al2O3": (6, 12), "HfO2": (15, 30), "SiO2": (3, 5),
}

def cv_report(V, Cp, G, freq_Hz, thickness_nm, radius_um, structure="MIM", material=None):
    d_m = thickness_nm * 1e-9
    A_m2 = math.pi * (radius_um * 1e-6) ** 2
    n = len(V)
    er = [Cp[i] * d_m / (EPS0 * A_m2) for i in range(n)]
    w = 2 * math.pi * freq_Hz if freq_Hz else None
    tand = [(G[i] / (w * Cp[i]) if (G and G[i] is not None and w and Cp[i]) else None)
            for i in range(n)]
    has_loss = any(t is not None for t in tand)

    i_cox = max(range(n), key=lambda i: Cp[i])      # 축적(Cox) 추정 = 최대 용량
    i_0v = min(range(n), key=lambda i: abs(V[i]))
    Cox, er_acc, tand_acc = Cp[i_cox], er[i_cox], tand[i_cox]
    Cmin, avg_C = min(Cp), sum(Cp) / n
    eot_nm = 3.9 * thickness_nm / er_acc if er_acc else None
    has_neg_C = Cmin < 0
    bias_ratio = (Cox / Cmin) if Cmin > 0 else None

    i_vmax = max(range(n), key=lambda i: V[i])       # 히스테리시스
    if 0 < i_vmax < n - 3:
        cf = min(zip(V[:i_vmax+1], Cp[:i_vmax+1]), key=lambda x: abs(x[0]))[1]
        cr = min(zip(V[i_vmax:], Cp[i_vmax:]), key=lambda x: abs(x[0]))[1]
        hyst = {"round_trip": True, "dC_at_0V_pF": (cr - cf) * 1e12}
    else:
        hyst = {"round_trip": False}

    # ---- 데이터 품질 판정 ----
    reasons, level = [], 0      # level 0=ok 1=caution 2=fail
    def bump(x):
        nonlocal level; level = max(level, x)
    if has_neg_C:
        reasons.append("음의 capacitance 구간 존재 -> 특정 bias에서 측정 아티팩트/누설 의심"); bump(1)
    if not has_loss:
        reasons.append("G(컨덕턴스) 미입력 -> tan delta(손실) 평가 불가. raw CSV 사용 권장"); bump(1)
    elif tand_acc is not None:
        if tand_acc > 1: reasons.append("축적 tan d=%.2f (>1, 손실>>용량) -> er 신뢰불가" % tand_acc); bump(2)
        elif tand_acc > 0.1: reasons.append("축적 tan d=%.2f (다소 큼)" % tand_acc); bump(1)
        else: reasons.append("축적 tan d=%.3f (양호)" % tand_acc)
    if er_acc is not None:
        if er_acc < 1:
            reasons.append("축적 er<1 (물리적 불가) -> 측정 무효"); bump(2)
        else:
            rng = MATERIAL_K_RANGE.get(material)
            if rng and not (rng[0] <= er_acc <= rng[1]):
                reasons.append("축적 er=%.0f -> %s 기대범위 %g~%g 벗어남" % (er_acc, material, *rng)); bump(1)
            else:
                reasons.append("축적 er=%.0f%s" % (er_acc, " (%s 기대범위 내)" % material if rng else ""))
    if bias_ratio and structure == "MIM" and bias_ratio > 1.5:
        reasons.append("MIM인데 C-V 비대칭 큼(ratio=%.1f) -> 구조/측정 의심" % bias_ratio); bump(1)
    if structure == "MOS" and bias_ratio and bias_ratio > 1.5:
        reasons.append("MOS 비대칭(ratio=%.1f) -> 정상 (축적/공핍 거동)" % bias_ratio)
    if freq_Hz and freq_Hz < 100e3:
        reasons.append("측정 %gkHz (저주파) -> 분산/손실 영향 가능, 고주파 교차확인 권장" % (freq_Hz/1000)); bump(1)

    verdict = ["사용 가능", "주의 (조건부 사용)", "사용 부적합"][level]

    return {
        "structure": structure, "material": material, "freq_kHz": freq_Hz/1000 if freq_Hz else None,
        "er_accumulation": er_acc, "er_at_0V": er[i_0v],
        "Cox_pF": Cox*1e12, "Cmin_pF": Cmin*1e12, "avg_C_pF": avg_C*1e12,
        "EOT_nm": eot_nm, "tan_delta_accumulation": tand_acc, "loss_measured": has_loss,
        "bias_modulation_ratio": bias_ratio, "negative_capacitance": has_neg_C,
        "hysteresis": hyst, "verdict": verdict, "verdict_reasons": reasons,
        "curve": {"V": list(V), "Cp_pF": [c*1e12 for c in Cp], "er": er, "tan_delta": tand},
    }


# ======================================================================
# 6) UI용 통합 진입점 : CSV -> (CV면 종합리포트+판정 / IV면 J-E)  자동 라우팅
#    CV CSV는 G 컬럼이 있으므로 tan delta(손실) 판정까지 완성됨.
# ======================================================================
def analyze_full(path_or_list, thickness_nm, radius_um, structure="MIM", material=None):
    paths = [path_or_list] if isinstance(path_or_list, str) else path_or_list
    parsed = [parse_csv(p) for p in paths]
    if parsed[0]["mode"] == "CV":
        p = parsed[0]; c = p["roles"]
        V, Cp, G, freq = [], [], [], None
        for r in p["rows"]:
            v = r[c["V"]]; cp = r[c["Cp"]]
            if v is None or cp is None:
                continue
            g = r[c["G"]] if "G" in c else None
            freq = r[c["freq"]] if "freq" in c else None
            V.append(v); Cp.append(cp); G.append(g)
        return {"mode": "CV", "meta": p["meta"],
                "result": cv_report(V, Cp, G, freq, thickness_nm, radius_um, structure, material)}
    else:
        return {"mode": "IV", "meta": parsed[0]["meta"],
                "result": analyze_iv(parsed, thickness_nm, radius_um)}


# ======================================================================
# 7) 배치 분석(한 시료, CV) : 여러 측정 파일 -> 품질판정 -> best + 분포
#    같은 시료이므로 두께/반지름은 배치 전체에 동일하게 적용.
# ======================================================================
def cv_batch(paths, thickness_nm, radius_um, structure="MIM", material=None):
    results = []
    for p in paths:
        name = os.path.basename(p)
        try:
            parsed = parse_csv(p)
            if parsed["mode"] != "CV":
                results.append({"name": name, "ok": False, "error": "CV 파일이 아님(IV로 인식)"})
                continue
            c = parsed["roles"]
            V, Cp, G, freq = [], [], [], None
            for r in parsed["rows"]:
                v = r[c["V"]]; cp = r[c["Cp"]]
                if v is None or cp is None:
                    continue
                g = r[c["G"]] if "G" in c else None
                freq = r[c["freq"]] if "freq" in c else None
                V.append(v); Cp.append(cp); G.append(g)
            if not V:
                results.append({"name": name, "ok": False, "error": "데이터 행 없음"})
                continue
            rep = cv_report(V, Cp, G, freq, thickness_nm, radius_um, structure, material)
            results.append({"name": name, "ok": True, "rep": rep})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)})

    # 분석 성공 + tan d/er 있는 것만 후보
    candidates = [r for r in results if r.get("ok")
                  and r["rep"]["tan_delta_accumulation"] is not None
                  and r["rep"]["er_accumulation"] is not None]
    passing = [r for r in candidates if r["rep"]["verdict"] != "사용 부적합"]
    pool = passing if passing else candidates           # 통과 없으면 '그나마 나은' 것들
    all_failed = (not passing) and bool(candidates)

    # best = 손실(tan d) 가장 낮은 = 가장 깨끗한 측정
    best = min(pool, key=lambda r: r["rep"]["tan_delta_accumulation"]) if pool else None

    stats = None
    if pool:
        ers = [r["rep"]["er_accumulation"] for r in pool]
        stats = {
            "n_total": len(paths),
            "n_ok": sum(1 for r in results if r.get("ok")),
            "n_valid": len(pool),
            "er_median": statistics.median(ers),
            "er_mean": statistics.mean(ers),
            "er_min": min(ers), "er_max": max(ers),
            "er_std": statistics.pstdev(ers) if len(ers) > 1 else 0.0,
            "all_failed": all_failed,
        }
    return {"results": results, "best": best, "pool": pool, "stats": stats}
