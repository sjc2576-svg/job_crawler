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

_REVIEW_SYSTEM = (
    "너는 자기소개서를 첨삭하는 커리어 코치야. 특정 회사나 공고와는 무관하게, 사용자가 작성한 "
    "자기소개서 원문 자체의 완성도만 놓고 첨삭 리뷰를 해줘. 새 자기소개서를 대신 작성해주지 말고 "
    "원문을 존중하면서 다음을 정리해: "
    "1) 총평(강점과 약점을 3~5문장으로), "
    "2) 문단/문장 단위 구체적 개선 제안(어색한 표현, 상투적이고 두루뭉술한 표현, 근거 없는 주장, "
    "논리적으로 매끄럽지 않은 흐름을 콕 집어서), "
    "3) 자기소개서에 흔히 담기는 항목(성장과정, 성격의 장단점, 지원동기, 입사 후 포부 등) 중 "
    "빠져 있거나 부실하게 다뤄진 부분이 있으면 지적. "
    "원문에 없는 경험을 지어내서 채우라고 하지 말고 '~을 구체적인 사례로 추가하면 좋겠다' 정도로 "
    "방향만 제시해. 마크다운 헤더나 불릿기호 없이 번호와 자연스러운 문단으로 작성해."
)


def analyze_company(job, api_key):
    """1단계: 공고 정보를 바탕으로 회사를 웹 검색해서 분석.
    job: database.get_jobs()가 반환하는 dict 하나 (title, company, location, link 포함).
    웹 검색(google_search) 도구는 순수 생성보다 훨씬 빡빡한 무료 쿼터가 걸려있어서,
    검색 포함 요청이 429로 막히면 검색 없이 한 번 더 시도해 완전히 실패하는 대신
    "검색 없이 생성된" 결과라도 보여준다.
    반환값: (분석 텍스트, interaction_id, 검색이 실제로 사용됐는지 여부)"""
    _require_api_key(api_key)

    client = genai.Client(api_key=api_key)
    prompt = (
        f"회사명: {job.get('company') or '알 수 없음'}\n"
        f"공고 제목: {job.get('title') or ''}\n"
        f"근무 지역: {job.get('location') or ''}\n"
        f"공고 링크: {job.get('link') or ''}\n\n"
        "위 회사를 웹 검색으로 조사해서 자기소개서에 활용할 수 있도록 정리해줘."
    )

    def _call(use_search):
        kwargs = dict(
            model=MODEL,
            system_instruction=_COMPANY_ANALYSIS_SYSTEM,
            input=prompt,
            store=True,
            generation_config={"max_output_tokens": 2048},
        )
        if use_search:
            kwargs["tools"] = [{"type": "google_search"}]
        return client.interactions.create(**kwargs)

    used_search = True
    try:
        interaction = _call(use_search=True)
    except Exception as e:
        if getattr(e, "status_code", None) != 429:
            _handle_error(e)
        try:
            interaction = _call(use_search=False)
            used_search = False
        except Exception as e2:
            _handle_error(e2)

    text, interaction_id = _finish(interaction)
    return text, interaction_id, used_search


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


def review_cover_letter(api_key, text=None, pdf_bytes=None):
    """사용자가 직접 작성해 온 자기소개서(텍스트 또는 PDF)를 회사/공고와 무관하게 첨삭 리뷰한다.
    다른 단계와 이어지는 대화가 아니라 단발성 호출이므로 interaction_id는 반환하지 않는다.
    반환값: 리뷰 텍스트"""
    _require_api_key(api_key)
    if not text and not pdf_bytes:
        raise CoverLetterError("리뷰할 자기소개서 텍스트를 입력하거나 PDF 파일을 업로드해주세요.")

    client = genai.Client(api_key=api_key)

    if pdf_bytes:
        input_payload = [
            {"type": "document", "data": io.BytesIO(pdf_bytes), "mime_type": "application/pdf"},
            {"type": "text", "text": "첨부한 PDF가 사용자가 작성한 자기소개서 원문이야. 첨삭 리뷰를 해줘."},
        ]
    else:
        input_payload = f"아래는 사용자가 작성한 자기소개서 원문이야. 첨삭 리뷰를 해줘.\n\n{text}"

    try:
        interaction = client.interactions.create(
            model=MODEL,
            system_instruction=_REVIEW_SYSTEM,
            input=input_payload,
            generation_config={"max_output_tokens": 4096},
        )
    except Exception as e:
        _handle_error(e)

    review_text, _ = _finish(interaction)
    return review_text
