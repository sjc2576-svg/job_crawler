"""
공고 선택 -> 회사 분석(웹 검색) -> 자소서 자동 생성 -> 직무 맞춤 수정,
4단계 흐름을 Gemini "Interactions API"의 대화 이어가기(previous_interaction_id)로 구현한다.

각 단계는 client.interactions.create()를 previous_interaction_id로 이어서 호출하므로,
직전 단계에서 무엇을 조사/작성했는지는 Google 서버 쪽에 보관된 대화 맥락으로 넘어간다.
그래서 이 서버는 최신 interaction.id 하나만 화면의 hidden 필드로 들고 있으면 되고,
DB나 세션에 대화 내용을 따로 저장하지 않는다.

이력서 PDF는 저장하지 않고, "자소서 생성" 단계에서만 사용한 뒤 즉시 버린다.
API 키는 계정마다 등록한 개인 Google AI Studio 키를 사용한다(공용 키 없음).
"""
import io

from google import genai

MODEL = "gemini-3.6-flash"


class CoverLetterError(Exception):
    """실패 시 사용자에게 보여줄 메시지를 담는다."""


def _handle_error(e):
    """client.interactions.create()가 던지는 예외는 google.genai._gaos.lib.compat_errors의
    비공개(내부) 모듈에 있어 클래스를 직접 import하지 않고, 공통으로 노출되는
    status_code/message 속성만 덕타이핑으로 읽어 분류한다."""
    status = getattr(e, "status_code", None)
    detail = getattr(e, "message", None) or str(e)

    if status in (401, 403) or "API_KEY_INVALID" in detail or "API key not valid" in detail:
        raise CoverLetterError("Google AI Studio API 키가 유효하지 않습니다. 'API 키 설정'에서 키를 확인해주세요.")
    if status == 429:
        raise CoverLetterError("무료 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
    if status and status >= 500:
        raise CoverLetterError(f"Gemini 서버 오류가 발생했습니다 ({status}). 잠시 후 다시 시도해주세요.")
    if status:
        raise CoverLetterError(f"요청이 거부되었습니다 ({status}): {detail}")
    raise CoverLetterError(f"AI 호출 중 오류가 발생했습니다: {detail}")


def _require_api_key(api_key):
    if not api_key:
        raise CoverLetterError(
            "등록된 Google AI Studio API 키가 없습니다. 'API 키 설정' 메뉴에서 "
            "https://aistudio.google.com/apikey 에서 발급받은 키를 먼저 등록해주세요."
        )


def _finish(interaction):
    if interaction.status != "completed":
        raise CoverLetterError(f"AI가 응답을 완료하지 못했습니다 (상태: {interaction.status}). 다시 시도해주세요.")
    text = (interaction.output_text or "").strip()
    if not text:
        raise CoverLetterError("AI가 빈 응답을 반환했습니다. 다시 시도해주세요.")
    return text, interaction.id


_COMPANY_ANALYSIS_SYSTEM = (
    "너는 채용 지원자를 돕는 리서치 도우미야. 웹 검색을 활용해서 주어진 회사에 대한 "
    "정보를 조사해줘. 사업 영역, 최근 이슈/뉴스, 인재상이나 핵심가치(찾을 수 있다면), "
    "이 직무와 관련되어 보이는 특징을 정리해줘. 검색으로 확인되지 않는 내용은 추측해서 "
    "지어내지 말고 '확인되지 않음'이라고 표시해. 한국어로, 불릿 포인트 위주로 간결하게 "
    "작성하고, 마지막에 자기소개서에 활용하기 좋은 포인트 1~2가지를 짚어줘."
)

_COVER_LETTER_SYSTEM = (
    "너는 한국 채용 자기소개서를 작성하는 커리어 코치야. 방금 조사한 회사 분석 내용과 "
    "첨부된 이력서를 근거로, 이 공고에 지원하는 자기소개서 초안을 작성해. "
    "이력서에 없는 경험은 절대 지어내지 말고, 부족한 부분은 "
    "'(여기에 관련 경험을 구체적으로 채워주세요)' 같은 빈칸으로 남겨. "
    "지원동기 / 직무 관련 역량·경험 / 입사 후 포부, 이렇게 세 문단으로 구성하고, "
    "회사 분석에서 파악한 내용(사업영역, 인재상 등)을 지원동기 문단에 자연스럽게 녹여. "
    "마크다운 헤더나 불릿기호 없이, 사람이 읽기 좋은 자연스러운 문장으로 작성해."
)

_REVISE_SYSTEM = (
    "사용자가 방금 작성된 자기소개서에 대한 수정 요청을 줄 거야. 요청을 반영해서 "
    "자기소개서 전체를 처음부터 다시 작성해줘(일부만 언급하지 말고 전체 본문을 출력해). "
    "이력서에 없는 경험을 새로 지어내지 말고, 이전과 마찬가지로 부족한 부분은 빈칸으로 남겨."
)


def analyze_company(job, api_key):
    """1단계: 공고 정보를 바탕으로 회사를 웹 검색해서 분석.
    job: database.get_jobs()가 반환하는 dict 하나 (title, company, location, link 포함).
    반환값: (분석 텍스트, interaction_id)"""
    _require_api_key(api_key)

    client = genai.Client(api_key=api_key)
    prompt = (
        f"회사명: {job.get('company') or '알 수 없음'}\n"
        f"공고 제목: {job.get('title') or ''}\n"
        f"근무 지역: {job.get('location') or ''}\n"
        f"공고 링크: {job.get('link') or ''}\n\n"
        "위 회사를 웹 검색으로 조사해서 자기소개서에 활용할 수 있도록 정리해줘."
    )

    try:
        interaction = client.interactions.create(
            model=MODEL,
            system_instruction=_COMPANY_ANALYSIS_SYSTEM,
            input=prompt,
            tools=[{"type": "google_search"}],
            store=True,
            generation_config={"max_output_tokens": 2048},
        )
    except Exception as e:
        _handle_error(e)

    return _finish(interaction)


def generate_cover_letter(job, pdf_bytes, previous_interaction_id, api_key):
    """2단계: 1단계의 회사 분석(대화 맥락) + 이력서로 자소서 초안을 생성.
    previous_interaction_id: analyze_company()가 반환한 interaction_id.
    반환값: (자소서 초안 텍스트, interaction_id)"""
    _require_api_key(api_key)
    if not previous_interaction_id:
        raise CoverLetterError("먼저 회사 분석을 실행해주세요.")

    client = genai.Client(api_key=api_key)
    prompt = (
        f"공고 제목: {job.get('title') or ''}\n"
        f"회사: {job.get('company') or ''}\n"
        f"경력조건: {job.get('career') or ''}\n"
        f"학력조건: {job.get('education') or ''}\n\n"
        "첨부한 이력서와 방금 분석한 회사 정보를 바탕으로, 이 공고에 지원할 "
        "자기소개서 초안을 작성해줘."
    )

    try:
        interaction = client.interactions.create(
            model=MODEL,
            previous_interaction_id=previous_interaction_id,
            system_instruction=_COVER_LETTER_SYSTEM,
            input=[
                {"type": "document", "data": io.BytesIO(pdf_bytes), "mime_type": "application/pdf"},
                {"type": "text", "text": prompt},
            ],
            store=True,
            generation_config={"max_output_tokens": 4096},
        )
    except Exception as e:
        _handle_error(e)

    return _finish(interaction)


def revise_cover_letter(feedback, previous_interaction_id, api_key):
    """3단계: 사용자 피드백을 반영해 직전 자소서를 다시 작성(반복 호출 가능).
    반환값: (수정된 자소서 텍스트, interaction_id)"""
    _require_api_key(api_key)
    if not previous_interaction_id:
        raise CoverLetterError("먼저 자소서 초안을 생성해주세요.")
    if not feedback:
        raise CoverLetterError("어떻게 수정할지 요청 내용을 입력해주세요.")

    client = genai.Client(api_key=api_key)

    try:
        interaction = client.interactions.create(
            model=MODEL,
            previous_interaction_id=previous_interaction_id,
            system_instruction=_REVISE_SYSTEM,
            input=f"수정 요청: {feedback}",
            store=True,
            generation_config={"max_output_tokens": 4096},
        )
    except Exception as e:
        _handle_error(e)

    return _finish(interaction)
