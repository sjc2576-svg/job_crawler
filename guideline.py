"""
'관심있음'으로 표시한 채용공고들을 규칙 기반으로 집계해서
자기소개서 방향성을 잡는 데 참고할 요약/제안을 만든다. 외부 API를 쓰지 않는다.
"""
import re
from collections import Counter

STOPWORDS = {
    "및", "등", "경력", "신입", "정규직", "채용", "모집", "담당자", "담당",
    "인재", "우대", "필수", "채용공고", "채용중", "구인", "모집중", "명",
}


def _tokenize(text):
    tokens = re.split(r"[\s/,·\-()\[\]{}|]+", text or "")
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def _top_counts(values, top_n=5):
    counter = Counter(v for v in values if v)
    return counter.most_common(top_n)


def build_guideline(jobs):
    """관심있음 공고 리스트(dict의 리스트)를 받아 가이드 데이터를 dict로 반환."""
    total = len(jobs)

    locations = _top_counts([j["location"] for j in jobs])
    conditions = _top_counts([j["condition_name"] for j in jobs])
    careers = _top_counts([j["career"] for j in jobs])
    educations = _top_counts([j["education"] for j in jobs])

    title_tokens = []
    for j in jobs:
        title_tokens.extend(_tokenize(j["title"]))
    keywords = _top_counts(title_tokens, top_n=8)

    memo_notes = [
        {"company": j["company"], "title": j["title"], "memo": j["memo"].strip()}
        for j in jobs if (j.get("memo") or "").strip()
    ]

    suggestions = []

    if conditions:
        suggestions.append(
            f"'{conditions[0][0]}' 조건으로 수집된 공고에 관심을 가장 많이 보이셨습니다. "
            f"이 직무/조건과 관련된 경험과 역량을 자기소개서 앞부분에서 강조해보세요."
        )

    if keywords:
        kw_list = ", ".join(f"'{k}'" for k, _ in keywords[:5])
        suggestions.append(
            f"관심있음 공고 제목에 {kw_list} 같은 단어가 자주 등장합니다. "
            f"이 키워드와 관련된 프로젝트/경험을 구체적인 사례로 녹여서 작성해보세요."
        )

    if careers:
        suggestions.append(
            f"가장 많이 요구된 경력 조건은 '{careers[0][0]}'입니다. "
            f"신입/경력 여부에 맞는 톤으로 강점을 배치하세요."
        )

    if locations:
        suggestions.append(
            f"'{locations[0][0]}' 지역 공고에 관심이 가장 많으셨습니다. "
            f"해당 지역·현장 근무에 대한 의지나 연고를 언급하면 설득력을 더할 수 있습니다."
        )

    if memo_notes:
        suggestions.append(
            "공고별 메모에 적어두신 지원 포인트들을 자기소개서의 핵심 문장으로 그대로 활용해보세요."
        )
    else:
        suggestions.append(
            "관심있음 공고의 메모 칸에 '이 공고에 지원할 때 강조하고 싶은 직무/포인트'를 적어두시면, "
            "다음에 이 페이지에서 모아서 보여드립니다."
        )

    return {
        "total": total,
        "locations": locations,
        "conditions": conditions,
        "careers": careers,
        "educations": educations,
        "keywords": keywords,
        "memo_notes": memo_notes,
        "suggestions": suggestions,
    }
