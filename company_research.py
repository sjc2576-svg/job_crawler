"""
회사명 하나를 받아 Google AI Studio(Gemini)의 웹 검색 그라운딩으로 기업을 조사한다.
특정 채용공고에 지원하기 위한 자소서용 분석(cover_letter.py의 analyze_company)과 달리,
이건 "이 회사에 다닐 만한가"를 살펴보는 일반적인 기업 리서치用:
사업/최근 뉴스/매출/성장성/복지/장단점 6개 항목으로 구조화해서 보여준다.
"""
import re

from google import genai

MODEL = "gemini-3.6-flash"

SECTION_ORDER = ["사업", "최근 뉴스", "매출", "성장성", "복지", "장단점"]

_SYSTEM_PROMPT = (
    "너는 구직자를 위해 기업을 조사하는 리서치 전문가야. 웹 검색을 활용해서 주어진 "
    "회사를 조사하고, 아래 형식을 정확히 지켜서 답해(대괄호 제목을 그대로 사용):\n\n"
    "[사업]\n주요 사업 영역, 제품/서비스, 업계 내 위치를 설명\n\n"
    "[최근 뉴스]\n최근 1~2년 내 주요 뉴스/이슈. 확인되지 않으면 '확인되지 않음'\n\n"
    "[매출]\n확인 가능한 최신 매출/실적 규모와 기준 시점. 확인되지 않으면 '확인되지 않음'\n\n"
    "[성장성]\n속한 업계 전망과 이 회사의 성장 가능성/방향\n\n"
    "[복지]\n연봉 수준, 복지 제도, 근무 환경 등 확인되는 내용. 확인되지 않으면 '확인되지 않음'\n\n"
    "[장단점]\n장점 2~3가지와 단점 2~3가지를 각각 줄바꿈으로 나열\n\n"
    "확인되지 않는 내용은 추측해서 지어내지 말고 반드시 '확인되지 않음'이라고 표시해. "
    "각 섹션은 위 대괄호 제목 형식을 정확히 지켜서 구분해줘."
)


class CompanyResearchError(Exception):
    """실패 시 사용자에게 보여줄 메시지를 담는다."""


def parse_sections(text):
    """"[사업]\\n내용\\n\\n[최근 뉴스]\\n내용..." 형식을 dict로 분해.
    모델이 형식을 어기면 못 쪼갠 채로 남을 수 있어서, 정해진 섹션이 하나도
    안 잡히면 호출부에서 원문 그대로 보여주도록 빈 dict를 반환한다."""
    matches = list(re.finditer(r"\[([^\[\]]+)\]\s*\n", text))
    if not matches:
        return {}

    sections = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()

    return sections


def analyze_company_research(company, api_key):
    """company: 조사할 회사명(문자열).
    api_key: 사용자 본인의 Google AI Studio API 키(평문, 복호화된 값).

    성공 시 (raw_text, sections_dict, interaction_id)를 반환한다.
    sections_dict는 SECTION_ORDER에 있는 항목만 골라 담되, 파싱에 실패하면 빈 dict.
    실패 시 CompanyResearchError(사용자에게 보여줄 한글 메시지)를 발생시킨다."""

    if not api_key:
        raise CompanyResearchError(
            "등록된 Google AI Studio API 키가 없습니다. 'API 키 설정' 메뉴에서 "
            "https://aistudio.google.com/apikey 에서 발급받은 키를 먼저 등록해주세요."
        )

    company = (company or "").strip()
    if not company:
        raise CompanyResearchError("조사할 회사명을 입력해주세요.")

    client = genai.Client(api_key=api_key)

    try:
        interaction = client.interactions.create(
            model=MODEL,
            system_instruction=_SYSTEM_PROMPT,
            input=f"조사할 회사: {company}",
            tools=[{"type": "google_search"}],
            store=True,
            generation_config={"max_output_tokens": 4096},
        )
    except Exception as e:
        # client.interactions.create()의 예외 타입들은 google.genai._gaos.lib.compat_errors의
        # 비공개(내부) 모듈에 있어 클래스를 직접 import하지 않고, 공통으로 노출되는
        # status_code/message 속성만 덕타이핑으로 읽어 분류한다.
        status = getattr(e, "status_code", None)
        detail = getattr(e, "message", None) or str(e)
        if status in (401, 403) or "API_KEY_INVALID" in detail or "API key not valid" in detail:
            raise CompanyResearchError("Google AI Studio API 키가 유효하지 않습니다. 'API 키 설정'에서 키를 확인해주세요.")
        if status == 429:
            raise CompanyResearchError("무료 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
        if status and status >= 500:
            raise CompanyResearchError(f"Gemini 서버 오류가 발생했습니다 ({status}). 잠시 후 다시 시도해주세요.")
        if status:
            raise CompanyResearchError(f"요청이 거부되었습니다 ({status}): {detail}")
        raise CompanyResearchError(f"AI 호출 중 오류가 발생했습니다: {detail}")

    if interaction.status != "completed":
        raise CompanyResearchError(f"AI가 분석을 완료하지 못했습니다 (상태: {interaction.status}). 다시 시도해주세요.")

    text = (interaction.output_text or "").strip()
    if not text:
        raise CompanyResearchError("AI가 빈 응답을 반환했습니다. 다시 시도해주세요.")

    parsed = parse_sections(text)
    sections = {title: parsed[title] for title in SECTION_ORDER if title in parsed}

    return text, sections, interaction.id
