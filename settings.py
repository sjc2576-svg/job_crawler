# ==========================================================
# 비밀값 설정 (환경변수 기반)
#
# 로컬 개발: 저장소 루트에 .env 파일을 만들고 값을 채우면
# python-dotenv가 자동으로 읽어들인다 (.env.example 참고).
# 배포 환경(Vercel/GitHub Actions): 각 플랫폼의 Environment
# Variables / Secrets 설정 화면에 아래 이름 그대로 등록한다.
# ==========================================================
import os

from dotenv import load_dotenv

load_dotenv()

MYSQL_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", "1234"),
    "database": os.environ.get("MYSQL_DATABASE", "jobs"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
}
if os.environ.get("MYSQL_SSL_CA"):
    MYSQL_CONFIG["ssl_ca"] = os.environ["MYSQL_SSL_CA"]

# Flask 세션 서명 및 API 키 암호화(crypto_utils.py)에 사용.
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
