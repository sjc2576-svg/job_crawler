import logging
import re
import time
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from urllib.parse import urlencode

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

from database import DB

# ------------------------------------------------------------
# 로깅 설정 (콘솔 + crawler.log 파일에 함께 기록)
# ------------------------------------------------------------
logger = logging.getLogger("crawler")
logger.setLevel(logging.INFO)

if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        "crawler.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

BASE_URL = "https://www.saramin.co.kr/zf_user/jobs/list/job-category"

# 기존에 이미 확인된 고정 파라미터
FIXED_PARAMS = {
    "panel_type": "",
    "search_optional_item": "n",
    "search_done": "y",
    "panel_count": "y",
    "preview": "y",
}

MAX_RETRY = 2           # 페이지 로딩 실패 시 재시도 횟수
WAIT_TIMEOUT = 15       # 채용공고 로딩 대기 시간(초)
MAX_PAGES = 10          # 조건 하나당 최대 몇 페이지까지 넘어갈지


def build_url(condition: dict) -> str:
    """검색 조건 dict를 사람인 URL로 변환"""
    params = dict(FIXED_PARAMS)
    params["cat_mcls"] = condition["cat_mcls"]

    if condition.get("loc_mcd") is not None:
        params["loc_mcd"] = condition["loc_mcd"]
    if condition.get("exp_cd") is not None:
        params["exp_cd"] = condition["exp_cd"]
    if condition.get("edu_lv"):
        # EDUCATION 값은 {"edu_min": .., "edu_max": ..} 또는 {"edu_none": "y"} 형태의
        # dict라서 단일 파라미터가 아니라 URL 쿼리에 그대로 병합한다.
        params.update(condition["edu_lv"])
    if condition.get("job_type") is not None:
        params["job_type"] = condition["job_type"]

    return f"{BASE_URL}?{urlencode(params)}"


def _norm(text: str) -> str:
    """비교를 위해 공백/구두점 제거"""
    return re.sub(r"[\s·/]", "", text or "")


def matches_experience(career_text: str, desired: str) -> bool:
    """career_text(예: '신입 · 정규직')가 desired 경력 조건에 맞는지 확인"""
    if not desired or desired == "전체":
        return True

    t = _norm(career_text)

    if desired == "신입":
        return "신입" in t
    if desired == "경력무관":
        return "경력무관" in t
    if desired == "신입경력":
        return "신입" in t and "경력" in t
    if desired == "경력":
        # '경력무관'은 제외하고, '경력' 문구나 'N년' 형태를 경력으로 인정
        return ("경력" in t and "경력무관" not in t) or bool(re.search(r"\d+년", t))

    return desired in t


def matches_employment_type(career_text: str, desired: str) -> bool:
    """career_text(예: '신입 · 정규직')가 desired 근무형태 조건에 맞는지 확인"""
    if not desired or desired == "전체":
        return True

    return _norm(desired) in _norm(career_text)


def matches_education(education_text: str, desired: str) -> bool:
    """education_text(예: '학력무관', '대졸(4년)이상')가 desired 학력 조건에 맞는지 확인"""
    if not desired or desired == "전체":
        return True

    t = _norm(education_text)

    keyword_map = {
        "학력무관": ["학력무관"],
        "고졸이하": ["학력무관", "고졸"],
        "고졸": ["고졸"],
        "대졸(2,3년)": ["초대졸", "대학(2", "대학(3"],
        "대졸(4년)": ["대졸(4", "대학교(4", "대학(4"],
        "석사": ["석사"],
        "박사": ["박사"],
        "박사이상": ["박사"],
    }

    keywords = keyword_map.get(desired, [_norm(desired)])
    return any(_norm(k) in t for k in keywords)


_DEADLINE_DATE_RE = re.compile(r"(\d{1,2})[.\/](\d{1,2})")
_DEADLINE_DDAY_RE = re.compile(r"D-(\d+)", re.IGNORECASE)


def parse_deadline(raw_text: str, today: date = None):
    """사람인 마감일 표시(예: '~08.26(수)', 'D-2', '오늘마감')를 실제 날짜로 변환.
    '상시채용'/'채용시'처럼 특정 날짜가 없는 경우엔 None을 반환하며, 이런 공고는
    날짜 대신 재크롤링 시 목록에 남아있는지로만 마감 여부를 판단한다(prune_missing_jobs)."""
    if today is None:
        today = date.today()

    text = (raw_text or "").strip()
    if not text or "상시채용" in text or "채용시" in text:
        return None

    if "오늘마감" in text:
        return today
    if "내일마감" in text:
        return today + timedelta(days=1)

    dday = _DEADLINE_DDAY_RE.search(text)
    if dday:
        return today + timedelta(days=int(dday.group(1)))

    m = _DEADLINE_DATE_RE.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            deadline = date(today.year, month, day)
        except ValueError:
            return None
        if deadline < today:
            # 예: 12월에 크롤링했는데 마감이 1월 -> 올해가 아니라 내년
            deadline = date(today.year + 1, month, day)
        return deadline

    return None


def condition_matches(career_text: str, education_text: str, condition: dict) -> bool:
    return (
        matches_experience(career_text, condition.get("exp_name"))
        and matches_employment_type(career_text, condition.get("type_name"))
        and matches_education(education_text, condition.get("edu_name"))
    )


def extract_jobs_on_page(driver, db: DB, condition: dict, user_id: int):
    """현재 페이지에 보이는 채용공고를 추출해서, 조건에 맞는 것만 이 계정 소유로 저장.
    (신규 저장 건수, 발견 건수, 조건에 맞아 이번에 실제로 보인 링크 집합) 반환.
    마지막 값은 마감된 공고 정리(prune_missing_jobs)에 쓰인다."""
    name = condition.get("name", "이름없음")

    jobs = driver.find_elements(
        By.CSS_SELECTOR, "div.list_body > div.list_item[id^='rec-']"
    )
    found = len(jobs)
    saved = 0
    seen_links = set()

    for job in jobs:
        try:
            company = job.find_element(
                By.CSS_SELECTOR, ".col.company_nm .str_tit"
            ).text.strip()
            title = job.find_element(By.CSS_SELECTOR, ".job_tit").text.strip()
            location = job.find_element(By.CSS_SELECTOR, ".work_place").text.strip()

            try:
                career_text = job.find_element(By.CSS_SELECTOR, ".career").text.strip()
            except Exception:
                career_text = ""

            try:
                education_text = job.find_element(By.CSS_SELECTOR, ".education").text.strip()
            except Exception:
                education_text = ""

            try:
                link = job.find_element(
                    By.CSS_SELECTOR, ".job_tit a"
                ).get_attribute("href")
            except Exception:
                link = job.find_element(By.TAG_NAME, "a").get_attribute("href")

            try:
                deadline_text = job.find_element(
                    By.CSS_SELECTOR, ".support_detail .date"
                ).text.strip()
            except Exception:
                deadline_text = ""

            if not condition_matches(career_text, education_text, condition):
                logger.info(f"  [조건불일치 스킵] {title} ({career_text} / {education_text})")
                continue

            seen_links.add(link)

            is_new = db.insert_job(
                user_id, title, company, location, link,
                condition_name=name,
                career=career_text,
                education=education_text,
                deadline_text=deadline_text or None,
                deadline_date=parse_deadline(deadline_text),
            )

            if is_new:
                saved += 1
                logger.info(
                    f"  [신규] {title} / {company} ({career_text} / {education_text}) "
                    f"마감:{deadline_text or '-'}"
                )
            # 이미 있는 공고면 조용히 건너뜀 (중복 방지)

        except Exception as e:
            logger.warning(f"  스킵(필드 추출 실패): {e}")

    return saved, found, seen_links


def go_to_next_page(driver, next_page_num: int) -> bool:
    """
    페이지 번호 버튼(예: page="2")을 클릭해서 다음 페이지로 이동.
    (사람인 페이지네이션은 <a> 링크가 아니라 <button>이고, '다음' 버튼은
    한 칸씩이 아니라 페이지를 10개씩 묶어서 건너뛰므로 사용하지 않음)
    성공하면 True, 더 이상 다음 페이지가 없으면 False.
    """
    try:
        old_first_job = driver.find_elements(
            By.CSS_SELECTOR, "div.list_item[id^='rec-']"
        )[0]
    except IndexError:
        old_first_job = None

    # 페이지네이션(PageBox)이 화면 맨 아래에 있어, 절반만 스크롤한 상태로는
    # 아직 렌더링되지 않았을 수 있음 -> 끝까지 스크롤 후 등장을 기다림
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.PageBox"))
        )
    except Exception:
        return False  # PageBox 자체가 없으면 페이지가 하나뿐인 것으로 판단

    try:
        next_btn = driver.find_element(
            By.CSS_SELECTOR, f"div.PageBox button.BtnType[page='{next_page_num}']"
        )
    except Exception:
        return False  # 해당 번호의 페이지 버튼이 없으면 마지막 페이지로 판단

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)

    try:
        next_btn.click()
    except Exception as e:
        logger.warning(f"페이지 이동 버튼 클릭 실패: {e}")
        return False

    try:
        # 이전 페이지의 첫 공고가 사라질 때까지(=페이지 전환 완료) 대기
        if old_first_job is not None:
            WebDriverWait(driver, WAIT_TIMEOUT).until(EC.staleness_of(old_first_job))

        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.list_item[id^='rec-']")
            )
        )
        time.sleep(1)  # 렌더링 안정화 대기
        return True

    except Exception as e:
        logger.warning(f"다음 페이지 로딩 대기 실패: {e}")
        return False


def crawl_condition(driver, db: DB, condition: dict, user_id: int):
    """검색 조건 하나에 대해 여러 페이지를 순회하며 크롤링 + 이 계정 소유로 저장.
    (신규 저장 건수, 발견 건수) 반환"""
    name = condition.get("name", "이름없음")
    url = build_url(condition)

    logger.info(f"=== [{name}] 크롤링 시작 ===")
    logger.info(f"URL: {url}")

    for attempt in range(1, MAX_RETRY + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.list_item[id^='rec-']")
                )
            )
            break  # 성공하면 재시도 루프 탈출

        except Exception as e:
            logger.warning(f"[{name}] 시도 {attempt}/{MAX_RETRY} 타임아웃: {e}")
            driver.save_screenshot(f"debug_timeout_{name}_{attempt}.png")

            if attempt == MAX_RETRY:
                logger.warning(f"[{name}] 최종 실패, 이 조건은 건너뜁니다.")
                return 0, 0

            time.sleep(3)  # 잠시 대기 후 재시도

    # 지연 로딩 콘텐츠까지 확보하기 위해 스크롤 + 대기
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(1.5)

    total_saved = 0
    total_found = 0
    all_seen_links = set()
    truncated = False

    for page_num in range(1, MAX_PAGES + 1):
        saved, found, seen_links = extract_jobs_on_page(driver, db, condition, user_id)
        total_saved += saved
        total_found += found
        all_seen_links |= seen_links
        logger.info(f"[{name}] {page_num}페이지: 발견 {found}건 / 신규 저장 {saved}건")

        if page_num == MAX_PAGES:
            logger.info(f"[{name}] 설정된 최대 페이지({MAX_PAGES})에 도달, 다음 조건으로 이동")
            truncated = True  # 더 있을 수도 있으므로 마감 정리는 건너뜀
            break

        moved = go_to_next_page(driver, page_num + 1)
        if not moved:
            logger.info(f"[{name}] 마지막 페이지로 판단, 다음 조건으로 이동")
            break

    logger.info(f"[{name}] 조건 합계: 신규 저장 {total_saved}건 / 발견 {total_found}건")

    if not truncated:
        # 이 조건의 결과를 끝까지 다 확인했을 때만, 이번에 안 보인 기존 공고를
        # 마감(또는 내려감)으로 간주해 정리한다. 페이지 제한에 걸려 중간에
        # 멈췄다면 아직 못 본 뒷페이지 공고까지 잘못 지울 수 있어 건너뛴다.
        pruned = db.prune_missing_jobs(user_id, name, all_seen_links)
        if pruned:
            logger.info(f"[{name}] 마감/누락 공고 정리: {pruned}건 삭제")

    return total_saved, total_found


def run_conditions(driver, db: DB, conditions: list, user_id: int):
    """조건 리스트를 순서대로 크롤링해서 이 계정 소유로 저장.
    (신규 저장 건수, 발견 건수) 반환"""
    total_saved = 0
    total_found = 0

    for condition in conditions:
        saved, found = crawl_condition(driver, db, condition, user_id)
        total_saved += saved
        total_found += found

    return total_saved, total_found


def run_web_conditions(conditions: list, user_id: int):
    """
    worker.py에서 호출 (대기열 요청 처리 및 계정별 자동 스케줄): 해당 계정 소유로
    크롤링을 실행. Selenium/Chrome이 필요하므로 GitHub Actions 워커에서만 실행된다.
    (신규 저장 건수, 발견 건수) 반환
    """
    if not conditions:
        return 0, 0

    logger.info(f"=== 웹 요청 크롤링 시작 (계정 {user_id} / 조건 {len(conditions)}개) ===")

    # 사람인이 headless Chrome을 감지해 렌더러를 멈춰버리므로(--headless 사용 시
    # 항상 타임아웃), 일부러 headless를 안 쓰고 GitHub Actions 워크플로우가 제공하는
    # 가상 디스플레이(Xvfb) 위에서 "진짜" 창 모드로 띄운다.
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(30)
    db = DB()

    try:
        total_saved, total_found = run_conditions(driver, db, conditions, user_id)
    finally:
        db.close()
        driver.quit()

    logger.info(f"=== 웹 요청 크롤링 완료: 발견 {total_found}건 / 신규 저장 {total_saved}건 ===")
    return total_saved, total_found
