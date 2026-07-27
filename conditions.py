"""
검색 조건 조합을 만드는 순수 로직 (Selenium 등 외부 의존성 없음).
Vercel 서버리스 환경(app.py)과 GitHub Actions 워커(worker.py) 양쪽에서 공용으로 import한다.
"""
from config import JOB_CATEGORY, LOCATION, EXPERIENCE, EDUCATION, JOB_TYPE


def build_conditions(cat_sel, loc_sel, exp_sel, edu_sel, type_sel) -> list:
    """
    각 항목별로 선택된 (name, code) 튜플 리스트를 받아,
    그 조합(cartesian product) 전체를 condition dict 리스트로 반환.
    """
    conditions = []
    for cat_name, cat_code in cat_sel:
        for loc_name, loc_code in loc_sel:
            for exp_name, exp_code in exp_sel:
                for edu_name, edu_code in edu_sel:
                    for type_name, type_code in type_sel:
                        conditions.append({
                            "name": f"{loc_name} {cat_name} ({exp_name}/{edu_name}/{type_name})",
                            "cat_mcls": cat_code,
                            "loc_mcd": loc_code,
                            "exp_cd": exp_code,
                            "edu_lv": edu_code,
                            "job_type": type_code,
                            "exp_name": exp_name,
                            "edu_name": edu_name,
                            "type_name": type_name,
                        })

    return conditions


def selection_to_conditions(cat_names, loc_names, exp_names, edu_names, type_names) -> list:
    """체크된 이름 리스트들(직무/지역/경력/학력/근무형태)을 condition dict 리스트로 변환.
    직무 외에는 비어있으면 각각 전국/전체로 기본값 처리."""
    loc_names = loc_names or [next(iter(LOCATION))]
    exp_names = exp_names or [next(iter(EXPERIENCE))]
    edu_names = edu_names or [next(iter(EDUCATION))]
    type_names = type_names or [next(iter(JOB_TYPE))]

    cat_sel = [(name, JOB_CATEGORY[name]) for name in cat_names if name in JOB_CATEGORY]
    loc_sel = [(name, LOCATION[name]) for name in loc_names if name in LOCATION]
    exp_sel = [(name, EXPERIENCE[name]) for name in exp_names if name in EXPERIENCE]
    edu_sel = [(name, EDUCATION[name]) for name in edu_names if name in EDUCATION]
    type_sel = [(name, JOB_TYPE[name]) for name in type_names if name in JOB_TYPE]

    return build_conditions(cat_sel, loc_sel, exp_sel, edu_sel, type_sel)
