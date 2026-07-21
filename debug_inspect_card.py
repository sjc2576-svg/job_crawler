"""
디버그 전용 스크립트.
사람인 공고 카드 1개의 실제 HTML 구조를 출력합니다.
이 결과를 그대로 복사해서 알려주시면, 경력/학력/근무형태를
정확히 추출하는 selector를 만들 수 있습니다.

실행: python debug_inspect_card.py
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import time

URL = "https://www.saramin.co.kr/zf_user/jobs/list/job-category?cat_mcls=11"

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
driver.get(URL)

try:
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.list_item[id^='rec-']"))
    )
except Exception as e:
    print("타임아웃:", e)
    driver.quit()
    raise SystemExit

time.sleep(1.5)

first_job = driver.find_element(By.CSS_SELECTOR, "div.list_body > div.list_item[id^='rec-']")

print("\n" + "=" * 60)
print("아래 내용을 통째로 복사해서 보내주세요")
print("=" * 60 + "\n")
print(first_job.get_attribute("outerHTML"))
print("\n" + "=" * 60)

driver.quit()
