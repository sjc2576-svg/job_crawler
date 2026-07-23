"""
업로드된 이력서(PDF)를 Google AI Studio(Gemini)에 보내 기술 스택을 파악하고,
DB에 저장된 채용공고 목록과 비교해서 공고별 매칭률(%)과 부족한 스킬을 분석한다.

이력서 PDF는 서버에 저장하지 않고 요청 처리 중에만 메모리에 두고 즉시 버린다.
API 키는 계정마다 직접 등록한 개인 키를 사용한다(공용 키 없음) - 호출하는
쪽(app.py)에서 로그인한 사용자의 키를 복호화해 api_key 인자로 넘겨준다.
"""
import io
import json

from google import genai

MODEL = "gemini-3.6-flash"

# 프롬프트/응답 크기를 감당할 수 있는 선에서 한 번에 분석할 공고 수 상한
MAX_JOBS = 100


class JobMatchError(Exception):
    """분석 실패 시 사용자에게 보여줄 메시지를 담는다."""


def _build_job_lines(jobs):
    lines = []
    for j in jobs[:MAX_JOBS]:
        parts = [
            str(j["id"]),
            j.get("title") or "",
            j.get("company") or "",
            j.get("location") or "",
            j.get("career") or "",
            j.get("education") or "",
            j.get("condition_name") or "",
        ]
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _build_user_prompt(jobs):
    job_lines = _build_job_lines(jobs)
    return (
        "아래는 DB에 저장된 채용공고 목록이다. 각 줄은 "
        '"공고ID | 제목 | 회사 | 지역 | 경력조건 | 학력조건 | 수집조건명" 형식이다.\n\n'
        f"{job_lines}\n\n"
        "첨부된 이력서(PDF)를 분석해서 지원자의 기술 스택/경험을 파악하고, "
        "위 채용공고 각각에 대한 적합도를 평가해줘."
    )


_SYSTEM_PROMPT = (
    "너는 이력서와 채용공고를 비교해서 직무 적합도를 분석하는 커리어 코치야. "
    "반드시 아래 JSON 형식으로만 응답해. 마크다운 코드블록이나 설명 문구 없이 "
    "순수 JSON 객체 하나만 출력해.\n"
    "{\n"
    '  "extracted_skills": ["이력서에서 확인되는 기술/경험 키워드", ...],\n'
    '  "overall_summary": "전반적인 강점과 부족한 부분 3~5문장 요약",\n'
    '  "matches": [\n'
    "    {\n"
    '      "job_id": 공고ID(정수),\n'
    '      "match_percent": 0~100 사이 정수,\n'
    '      "missing_skills": ["이 공고에 부족한 스킬", ...],\n'
    '      "reason": "이 매칭률을 준 이유 한 문장"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "matches 배열에는 전달받은 채용공고 전부에 대해 하나씩 항목을 만들어줘. "
    "이력서에 없는 경험을 지어내지 말고, 실제로 이력서에서 확인되는 내용만 근거로 판단해."
)


def _strip_code_fence(text):
    """Gemini가 지시를 무시하고 ```json ... ``` 로 감싸는 경우를 대비한 방어 코드"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def analyze_resume_match(pdf_bytes, jobs, api_key):
    """pdf_bytes: 업로드된 이력서 PDF의 원본 바이트.
    jobs: database.get_jobs()가 반환한 dict 리스트
          (id, title, company, location, career, education, condition_name 포함).
    api_key: 이 요청을 보낼 사용자 본인의 Google AI Studio API 키(평문, 복호화된 값).

    성공 시 다음 형태의 dict를 반환한다:
        {"extracted_skills": [...], "overall_summary": "...",
         "matches": [{"job_id":.., "match_percent":.., "missing_skills":[..], "reason":".."}]}
    실패 시 JobMatchError(사용자에게 보여줄 한글 메시지)를 발생시킨다."""

    if not api_key:
        raise JobMatchError(
            "등록된 Google AI Studio API 키가 없습니다. 'API 키 설정' 메뉴에서 "
            "https://aistudio.google.com/apikey 에서 발급받은 키를 먼저 등록해주세요."
        )

    if not jobs:
        raise JobMatchError("분석할 채용공고가 없습니다. 먼저 크롤링을 실행해서 공고를 수집해주세요.")

    client = genai.Client(api_key=api_key)

    try:
        interaction = client.interactions.create(
            model=MODEL,
            system_instruction=_SYSTEM_PROMPT,
            input=[
                {"type": "document", "data": io.BytesIO(pdf_bytes), "mime_type": "application/pdf"},
                {"type": "text", "text": _build_user_prompt(jobs)},
            ],
            response_format={"type": "text", "mime_type": "application/json"},
            generation_config={"max_output_tokens": 8192},
        )
    except Exception as e:
        # client.interactions.create()의 예외 타입들은 google.genai._gaos.lib.compat_errors의
        # 비공개(내부) 모듈에 있어 클래스를 직접 import하지 않고, 공통으로 노출되는
        # status_code/message 속성만 덕타이핑으로 읽어 분류한다.
        status = getattr(e, "status_code", None)
        detail = getattr(e, "message", None) or str(e)
        # 유효하지 않은 API 키는 401/403이 아니라 400(INVALID_ARGUMENT, API_KEY_INVALID)로 온다
        if status in (401, 403) or "API_KEY_INVALID" in detail or "API key not valid" in detail:
            raise JobMatchError("Google AI Studio API 키가 유효하지 않습니다. 'API 키 설정'에서 키를 확인해주세요.")
        if status == 429:
            raise JobMatchError("무료 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
        if status and status >= 500:
            raise JobMatchError(f"Gemini 서버 오류가 발생했습니다 ({status}). 잠시 후 다시 시도해주세요.")
        if status:
            raise JobMatchError(f"요청이 거부되었습니다 ({status}): {detail}")
        raise JobMatchError(f"AI 호출 중 오류가 발생했습니다: {detail}")

    if interaction.status != "completed":
        raise JobMatchError(f"AI가 분석을 완료하지 못했습니다 (상태: {interaction.status}). 다시 시도해주세요.")

    text = _strip_code_fence(interaction.output_text or "")
    if not text:
        raise JobMatchError("AI가 빈 응답을 반환했습니다. 다시 시도해주세요.")

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise JobMatchError("AI 응답을 해석하지 못했습니다(JSON 형식 오류). 다시 시도해주세요.")

    if not isinstance(result, dict) or not isinstance(result.get("matches"), list):
        raise JobMatchError("AI 응답 형식이 예상과 달랐습니다. 다시 시도해주세요.")

    return result
