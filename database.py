import mysql.connector
from settings import MYSQL_CONFIG

# 지원 현황 상태값
STATUS_CHOICES = ["미확인", "관심있음", "지원함", "제외"]
DEFAULT_STATUS = STATUS_CHOICES[0]


class DB:

    def __init__(self):
        self.conn = mysql.connector.connect(**MYSQL_CONFIG, use_pure=True)
        self.cursor = self.conn.cursor()
        self.dict_cursor = self.conn.cursor(dictionary=True)
        self._ensure_table()
        self._ensure_users_table()
        self._ensure_job_posting_owner_column()
        self._ensure_user_api_key_column()
        self._ensure_user_job_state_table()
        self._ensure_cover_letter_columns()
        self._migrate_legacy_status_to_per_user()
        self._ensure_schedule_table()
        self._ensure_company_analysis_table()
        self._ensure_crawl_request_table()

    # ------------------------------------------------------------
    # 테이블 / 인덱스 준비
    # ------------------------------------------------------------
    def _ensure_users_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_user (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            job_categories VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

    def _ensure_user_api_key_column(self):
        """계정별 AI API 키(암호화된 값)를 저장할 컬럼이 없으면 추가.
        예전에는 Anthropic 전용으로 anthropic_api_key_enc라는 이름을 썼는데,
        이제 어느 제공자든 담을 수 있는 이름(ai_api_key_enc)으로 바꿨다.
        기존에 저장해둔 값이 있으면 컬럼명만 바꿔서 그대로 보존한다."""
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'app_user'
          AND column_name = 'ai_api_key_enc'
        """)
        exists = self.cursor.fetchone()[0]

        if exists:
            return

        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'app_user'
          AND column_name = 'anthropic_api_key_enc'
        """)
        legacy_exists = self.cursor.fetchone()[0]

        if legacy_exists:
            self.cursor.execute(
                "ALTER TABLE app_user CHANGE anthropic_api_key_enc ai_api_key_enc VARCHAR(500)"
            )
        else:
            self.cursor.execute(
                "ALTER TABLE app_user ADD COLUMN ai_api_key_enc VARCHAR(500)"
            )
        self.conn.commit()

    def _ensure_user_job_state_table(self):
        """지원 현황(상태/메모)을 계정별로 저장하는 테이블"""
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_job_state (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            job_id INT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT '%s',
            memo TEXT,
            applied_at TIMESTAMP NULL,
            UNIQUE KEY uniq_user_job (user_id, job_id),
            FOREIGN KEY (user_id) REFERENCES app_user(id),
            FOREIGN KEY (job_id) REFERENCES job_posting(id)
        )
        """ % DEFAULT_STATUS)
        self.conn.commit()

    def _ensure_cover_letter_columns(self):
        """AI 자기소개서 생성(회사 분석/초안/대화 이어가기 ID)을 계정+공고별로
        저장할 컬럼이 없으면 추가. 웹 화면에서 나중에 다시 봐도 사라지지 않도록
        user_job_state에 함께 저장한다."""
        for column, ddl in [
            ("company_analysis", "ALTER TABLE user_job_state ADD COLUMN company_analysis TEXT"),
            ("cover_letter", "ALTER TABLE user_job_state ADD COLUMN cover_letter TEXT"),
            ("cover_letter_interaction_id", "ALTER TABLE user_job_state ADD COLUMN cover_letter_interaction_id VARCHAR(100)"),
        ]:
            self.cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'user_job_state'
              AND column_name = %s
            """, (column,))
            exists = self.cursor.fetchone()[0]

            if not exists:
                self.cursor.execute(ddl)
                self.conn.commit()

    def _ensure_company_analysis_table(self):
        """AI 기업 분석(사업/최근뉴스/매출/성장성/복지/장단점) 결과를 계정+회사명
        기준으로 저장하는 테이블. 특정 공고가 아니라 회사 자체에 대한 조사라서
        user_job_state가 아니라 별도 테이블에 둔다."""
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_analysis (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            company VARCHAR(255) NOT NULL,
            analysis TEXT,
            interaction_id VARCHAR(100),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_user_company (user_id, company(191)),
            FOREIGN KEY (user_id) REFERENCES app_user(id)
        )
        """)
        self.conn.commit()

    def _migrate_legacy_status_to_per_user(self):
        """
        예전에는 job_posting.status/memo/applied_at이 전체 계정 공용이었음.
        컬럼이 아직 남아있다면(=마이그레이션 전이라면) 기존 값을 모든 계정에
        복사해 넣고 job_posting에서는 컬럼을 제거한다. 한 번만 실행되면 됨.
        """
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'job_posting'
          AND column_name = 'status'
        """)
        if not self.cursor.fetchone()[0]:
            return  # 이미 마이그레이션 완료됨

        self.cursor.execute("SELECT id FROM app_user")
        user_ids = [row[0] for row in self.cursor.fetchall()]

        if user_ids:
            self.cursor.execute("""
            SELECT id, status, memo, applied_at FROM job_posting
            WHERE status != %s OR memo IS NOT NULL OR applied_at IS NOT NULL
            """, (DEFAULT_STATUS,))
            legacy_rows = self.cursor.fetchall()

            for job_id, status, memo, applied_at in legacy_rows:
                for user_id in user_ids:
                    self.cursor.execute("""
                    INSERT INTO user_job_state (user_id, job_id, status, memo, applied_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        status = VALUES(status),
                        memo = VALUES(memo),
                        applied_at = VALUES(applied_at)
                    """, (user_id, job_id, status, memo, applied_at))

        self.cursor.execute("ALTER TABLE job_posting DROP COLUMN status")
        self.cursor.execute("ALTER TABLE job_posting DROP COLUMN memo")
        self.cursor.execute("ALTER TABLE job_posting DROP COLUMN applied_at")
        self.conn.commit()

    def _ensure_schedule_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule_setting (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL UNIQUE,
            enabled TINYINT(1) NOT NULL DEFAULT 0,
            schedule_time VARCHAR(5) NOT NULL DEFAULT '09:00',
            job_categories VARCHAR(255) NOT NULL DEFAULT '',
            locations VARCHAR(255) NOT NULL DEFAULT '',
            experiences VARCHAR(255) NOT NULL DEFAULT '',
            educations VARCHAR(255) NOT NULL DEFAULT '',
            job_types VARCHAR(255) NOT NULL DEFAULT '',
            last_run_date DATE NULL,
            FOREIGN KEY (user_id) REFERENCES app_user(id)
        )
        """)
        self.conn.commit()

    def _ensure_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_posting (
            id INT AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(500),
            company VARCHAR(255),
            location VARCHAR(255),
            link VARCHAR(500),
            condition_name VARCHAR(255),
            career VARCHAR(255),
            education VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()
        self._ensure_columns()

    def _ensure_columns(self):
        """기존에 만들어둔 테이블에 career/education 컬럼이 없다면 추가
        (status/memo/applied_at은 계정별 user_job_state 테이블로 옮겨졌으므로 더 이상 여기서 추가하지 않음)"""
        for column, ddl in [
            ("career", "ALTER TABLE job_posting ADD COLUMN career VARCHAR(255)"),
            ("education", "ALTER TABLE job_posting ADD COLUMN education VARCHAR(255)"),
            ("deadline_text", "ALTER TABLE job_posting ADD COLUMN deadline_text VARCHAR(50)"),
            ("deadline_date", "ALTER TABLE job_posting ADD COLUMN deadline_date DATE NULL"),
        ]:
            self.cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'job_posting'
              AND column_name = %s
            """, (column,))
            exists = self.cursor.fetchone()[0]

            if not exists:
                self.cursor.execute(ddl)
                self.conn.commit()

    def _ensure_job_posting_owner_column(self):
        """job_posting을 계정별로 분리하기 위한 user_id 컬럼과, link 단독이 아니라
        (user_id, link) 조합으로 중복을 체크하는 UNIQUE 인덱스를 보장한다.

        예전엔 job_posting이 모든 계정이 보는 공용 데이터였다. 이미 있던 데이터는
        가장 먼저 만들어진(가장 오래된) 계정 소유로 전부 이관해서, 그 계정은 기존
        목록을 그대로 보고 다른 계정들은 빈 목록에서 새로 시작하게 한다."""
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'job_posting'
          AND column_name = 'user_id'
        """)
        has_user_id = self.cursor.fetchone()[0]

        if not has_user_id:
            self.cursor.execute("ALTER TABLE job_posting ADD COLUMN user_id INT NULL")
            self.cursor.execute("""
                UPDATE job_posting
                SET user_id = (SELECT id FROM app_user ORDER BY id ASC LIMIT 1)
                WHERE user_id IS NULL
            """)
            self.conn.commit()

        # 예전 link 단독 UNIQUE 인덱스가 남아있으면 제거(계정마다 같은 link를 각자
        # 가질 수 있어야 하므로 더 이상 link 하나만으로 유일해선 안 됨)
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'job_posting'
          AND index_name = 'uniq_link'
        """)
        if self.cursor.fetchone()[0]:
            self.cursor.execute("ALTER TABLE job_posting DROP INDEX uniq_link")
            self.conn.commit()

        # (user_id, link) 조합 UNIQUE 인덱스 (link가 500자라 앞 255byte만 사용)
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'job_posting'
          AND index_name = 'uniq_user_link'
        """)
        if not self.cursor.fetchone()[0]:
            try:
                self.cursor.execute("""
                ALTER TABLE job_posting
                ADD UNIQUE INDEX uniq_user_link (user_id, link(255))
                """)
                self.conn.commit()
            except mysql.connector.Error as e:
                # 이미 중복 데이터가 있어 인덱스 생성이 실패할 수 있음
                print(f"UNIQUE 인덱스(uniq_user_link) 생성 실패: {e}")

    # ------------------------------------------------------------
    # 삭제
    # ------------------------------------------------------------
    def delete_jobs(self, user_id, job_ids):
        """이 계정 소유의 공고 중 선택한 것들을 한 번에 삭제(웹 화면의 정리 기능용).
        user_id 조건을 함께 걸어서, 요청을 조작해도 다른 계정 소유 공고는 지울 수 없다.
        딸린 계정별 상태/메모/자소서(user_job_state)도 함께 지워야 FK 제약에 걸리지 않는다.
        반환값: 실제로 삭제된 job_posting 행 수."""
        if not job_ids:
            return 0

        placeholders = ", ".join(["%s"] * len(job_ids))

        self.cursor.execute(
            f"DELETE FROM user_job_state WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        )
        self.cursor.execute(
            f"DELETE FROM job_posting WHERE user_id = %s AND id IN ({placeholders})",
            (user_id, *job_ids),
        )
        deleted = self.cursor.rowcount
        self.conn.commit()
        return deleted

    def prune_missing_jobs(self, user_id, condition_name, seen_links):
        """이 계정의 이 조건(condition_name)으로 저장된 공고 중 마감일을 알 수 없는
        것들(deadline_date가 NULL: 상시채용/채용시/형식 파싱 실패)만 대상으로,
        이번 크롤링에서 더 이상 보이지 않으면 마감(또는 내려감)으로 간주해 삭제한다.
        마감일을 알고 있는 공고는 delete_expired_jobs가 날짜로 직접 정리하므로
        여기서 다시 건드리지 않는다. 반환값: 삭제된 행 수."""
        self.cursor.execute(
            """
            SELECT id, link FROM job_posting
            WHERE user_id = %s AND condition_name = %s AND deadline_date IS NULL
            """,
            (user_id, condition_name),
        )
        stale_ids = [job_id for job_id, link in self.cursor.fetchall() if link not in seen_links]
        return self.delete_jobs(user_id, stale_ids)

    def delete_expired_jobs(self):
        """마감일(deadline_date)이 지난 공고를 계정 구분 없이 전부 삭제.
        실제 날짜를 알고 있으므로 크롤링이 몇 페이지까지 갔는지와 무관하게
        안전하게 지울 수 있다. 반환값: 삭제된 job_posting 행 수."""
        self.cursor.execute(
            "SELECT id FROM job_posting WHERE deadline_date IS NOT NULL AND deadline_date < CURDATE()"
        )
        expired_ids = [row[0] for row in self.cursor.fetchall()]
        if not expired_ids:
            return 0

        placeholders = ", ".join(["%s"] * len(expired_ids))
        self.cursor.execute(
            f"DELETE FROM user_job_state WHERE job_id IN ({placeholders})",
            tuple(expired_ids),
        )
        self.cursor.execute(
            f"DELETE FROM job_posting WHERE id IN ({placeholders})",
            tuple(expired_ids),
        )
        deleted = self.cursor.rowcount
        self.conn.commit()
        return deleted

    # ------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------
    def insert_job(self, user_id, title, company, location, link, condition_name=None,
                   career=None, education=None, deadline_text=None, deadline_date=None):
        """
        이 계정이 이미 저장해둔 link면 건너뜁니다 (계정별 중복 방지).
        deadline_date를 알고 있으면 delete_expired_jobs가 마감일이 지났을 때 정리하고,
        모르면(상시채용/채용시 등) prune_missing_jobs가 재크롤링 결과 기준으로 정리한다.
        반환값: 새로 저장됐으면 True, 이미 있어서 건너뛰었으면 False
        """
        sql = """
        INSERT IGNORE INTO job_posting
        (user_id, title, company, location, link, condition_name, career, education,
         deadline_text, deadline_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        self.cursor.execute(
            sql,
            (user_id, title, company, location, link, condition_name, career, education,
             deadline_text, deadline_date),
        )
        self.conn.commit()

        return self.cursor.rowcount > 0

    # ------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------
    def get_jobs(self, user_id, region=None, keyword=None, condition_name=None,
                 status=None, group_by_company=False):
        """
        user_id: 지원 현황(상태/메모)을 이 계정 기준으로 붙여서 반환
        region: 위치(location)에 포함된 문자열로 필터 (예: '울산')
        keyword: 회사명 또는 제목에 포함된 문자열로 필터
        condition_name: 특정 검색 조건으로 수집된 데이터만 필터
        status: 지원 현황 상태로 필터 (STATUS_CHOICES 중 하나)
        group_by_company: True면 회사명 기준으로 정렬해서 반환 (회사별 보기용)

        반환값: 각 공고를 dict로 담은 리스트
        """
        sql = """
        SELECT jp.id, jp.title, jp.company, jp.location, jp.link, jp.condition_name,
               jp.career, jp.education,
               COALESCE(ujs.status, %s) AS status,
               ujs.memo AS memo,
               ujs.applied_at AS applied_at,
               (ujs.cover_letter IS NOT NULL) AS has_cover_letter
        FROM job_posting jp
        LEFT JOIN user_job_state ujs ON ujs.job_id = jp.id AND ujs.user_id = %s
        WHERE jp.user_id = %s
        """
        params = [DEFAULT_STATUS, user_id, user_id]

        if region:
            sql += " AND jp.location LIKE %s"
            params.append(f"%{region}%")

        if keyword:
            sql += " AND (jp.company LIKE %s OR jp.title LIKE %s)"
            params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")

        if condition_name:
            sql += " AND jp.condition_name = %s"
            params.append(condition_name)

        if status:
            sql += " AND COALESCE(ujs.status, %s) = %s"
            params.append(DEFAULT_STATUS)
            params.append(status)

        if group_by_company:
            sql += " ORDER BY jp.company, jp.id DESC"
        else:
            sql += " ORDER BY jp.id DESC"

        self.dict_cursor.execute(sql, tuple(params))
        return self.dict_cursor.fetchall()

    # ------------------------------------------------------------
    # 지원 현황 (상태 / 메모) 갱신 - 계정별로 저장됨
    # ------------------------------------------------------------
    def update_status(self, user_id, job_id, status):
        """공고의 지원 상태를 계정별로 변경. '지원함'으로 바뀌는 최초 시점에 applied_at을 기록."""
        if status not in STATUS_CHOICES:
            raise ValueError(f"알 수 없는 상태값: {status}")

        if status == "지원함":
            self.cursor.execute("""
                INSERT INTO user_job_state (user_id, job_id, status, applied_at)
                VALUES (%s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    applied_at = COALESCE(applied_at, NOW())
            """, (user_id, job_id, status))
        else:
            self.cursor.execute("""
                INSERT INTO user_job_state (user_id, job_id, status)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status)
            """, (user_id, job_id, status))
        self.conn.commit()

    def update_memo(self, user_id, job_id, memo):
        self.cursor.execute("""
            INSERT INTO user_job_state (user_id, job_id, memo)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE memo = VALUES(memo)
        """, (user_id, job_id, memo))
        self.conn.commit()

    # ------------------------------------------------------------
    # AI 자기소개서 생성 (회사 분석 / 초안 / 대화 이어가기 ID) - 계정+공고별 저장
    # ------------------------------------------------------------
    def get_cover_letter(self, user_id, job_id):
        """이 계정이 이 공고에 대해 생성해둔 회사 분석/자소서를 반환. 없으면 None."""
        self.dict_cursor.execute("""
            SELECT company_analysis, cover_letter, cover_letter_interaction_id
            FROM user_job_state
            WHERE user_id = %s AND job_id = %s
        """, (user_id, job_id))
        row = self.dict_cursor.fetchone()
        if not row or (row["company_analysis"] is None and row["cover_letter"] is None):
            return None
        return row

    def save_cover_letter(self, user_id, job_id, company_analysis, cover_letter, interaction_id):
        self.cursor.execute("""
            INSERT INTO user_job_state
                (user_id, job_id, company_analysis, cover_letter, cover_letter_interaction_id)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                company_analysis = VALUES(company_analysis),
                cover_letter = VALUES(cover_letter),
                cover_letter_interaction_id = VALUES(cover_letter_interaction_id)
        """, (user_id, job_id, company_analysis, cover_letter, interaction_id))
        self.conn.commit()

    # ------------------------------------------------------------
    # AI 기업 분석 - 계정+회사명 기준 저장 (특정 공고가 아니라 회사 자체)
    # ------------------------------------------------------------
    def get_companies(self, user_id):
        """이 계정이 수집한 공고에 등장하는 회사명 목록 (기업 분석 선택용)"""
        self.cursor.execute("""
        SELECT DISTINCT company
        FROM job_posting
        WHERE user_id = %s AND company IS NOT NULL AND company != ''
        ORDER BY company
        """, (user_id,))
        return [row[0] for row in self.cursor.fetchall()]

    def get_company_analysis(self, user_id, company):
        """이 계정이 이 회사에 대해 생성해둔 기업 분석을 반환. 없으면 None."""
        self.dict_cursor.execute("""
            SELECT company, analysis, interaction_id, updated_at
            FROM company_analysis
            WHERE user_id = %s AND company = %s
        """, (user_id, company))
        return self.dict_cursor.fetchone()

    def save_company_analysis(self, user_id, company, analysis, interaction_id):
        self.cursor.execute("""
            INSERT INTO company_analysis (user_id, company, analysis, interaction_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                analysis = VALUES(analysis),
                interaction_id = VALUES(interaction_id)
        """, (user_id, company, analysis, interaction_id))
        self.conn.commit()

    def get_condition_names(self, user_id):
        """Flask 필터 드롭다운에 쓸 조건 목록 (이 계정이 수집한 공고 기준)"""
        self.cursor.execute("""
        SELECT DISTINCT condition_name
        FROM job_posting
        WHERE user_id = %s AND condition_name IS NOT NULL
        ORDER BY condition_name
        """, (user_id,))
        return [row[0] for row in self.cursor.fetchall()]

    # ------------------------------------------------------------
    # 회원 관리
    # ------------------------------------------------------------
    def create_user(self, username, password_hash, job_categories):
        """
        새 회원을 등록. job_categories는 콤마로 구분된 문자열(예: '생산,기획전략').
        username이 이미 존재하면 mysql.connector.errors.IntegrityError 발생.
        """
        self.cursor.execute(
            "INSERT INTO app_user (username, password_hash, job_categories) VALUES (%s, %s, %s)",
            (username, password_hash, job_categories),
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def get_user_by_username(self, username):
        self.dict_cursor.execute(
            "SELECT id, username, password_hash, job_categories FROM app_user WHERE username = %s",
            (username,),
        )
        return self.dict_cursor.fetchone()

    def get_user_by_id(self, user_id):
        self.dict_cursor.execute(
            "SELECT id, username, password_hash, job_categories FROM app_user WHERE id = %s",
            (user_id,),
        )
        return self.dict_cursor.fetchone()

    def get_api_key_encrypted(self, user_id):
        """이 계정에 등록된 AI API 키(암호화된 상태)를 반환. 없으면 None."""
        self.cursor.execute(
            "SELECT ai_api_key_enc FROM app_user WHERE id = %s", (user_id,)
        )
        row = self.cursor.fetchone()
        return row[0] if row else None

    def set_api_key_encrypted(self, user_id, encrypted_key_or_none):
        """이 계정의 AI API 키를 저장(또는 None으로 삭제)."""
        self.cursor.execute(
            "UPDATE app_user SET ai_api_key_enc = %s WHERE id = %s",
            (encrypted_key_or_none, user_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------
    # 자동 크롤링 스케줄 (사용자별)
    # ------------------------------------------------------------
    def get_schedule(self, user_id):
        self.dict_cursor.execute(
            "SELECT * FROM schedule_setting WHERE user_id = %s", (user_id,)
        )
        return self.dict_cursor.fetchone()

    def upsert_schedule(self, user_id, enabled, schedule_time, job_categories,
                         locations, experiences, educations, job_types):
        self.cursor.execute("""
            INSERT INTO schedule_setting
            (user_id, enabled, schedule_time, job_categories, locations,
             experiences, educations, job_types)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                enabled = VALUES(enabled),
                schedule_time = VALUES(schedule_time),
                job_categories = VALUES(job_categories),
                locations = VALUES(locations),
                experiences = VALUES(experiences),
                educations = VALUES(educations),
                job_types = VALUES(job_types)
        """, (user_id, enabled, schedule_time, job_categories, locations,
              experiences, educations, job_types))
        self.conn.commit()

    def get_due_schedules(self, current_time, today):
        """
        current_time: "HH:MM" 형식 문자열, today: date 객체.
        오늘 아직 실행되지 않은, 이미 지정 시각이 지난 스케줄들을 반환.
        (worker.py가 GitHub Actions cron으로 몇 분 간격으로 폴링하므로,
        정확히 같은 분에 실행된다는 보장이 없어 '이하'로 비교한다.)
        """
        self.dict_cursor.execute("""
            SELECT * FROM schedule_setting
            WHERE enabled = 1 AND schedule_time <= %s
              AND (last_run_date IS NULL OR last_run_date != %s)
        """, (current_time, today))
        return self.dict_cursor.fetchall()

    def mark_schedule_run(self, user_id, run_date):
        self.cursor.execute(
            "UPDATE schedule_setting SET last_run_date = %s WHERE user_id = %s",
            (run_date, user_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------
    # 즉시 크롤링 요청 대기열 (웹에서 요청 -> GitHub Actions 워커가 처리)
    # ------------------------------------------------------------
    def _ensure_crawl_request_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS crawl_request (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            job_categories VARCHAR(255) NOT NULL DEFAULT '',
            locations VARCHAR(255) NOT NULL DEFAULT '',
            experiences VARCHAR(255) NOT NULL DEFAULT '',
            educations VARCHAR(255) NOT NULL DEFAULT '',
            job_types VARCHAR(255) NOT NULL DEFAULT '',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            found_count INT NULL,
            saved_count INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP NULL,
            FOREIGN KEY (user_id) REFERENCES app_user(id)
        )
        """)
        self.conn.commit()

    def create_crawl_request(self, user_id, job_categories, locations,
                              experiences, educations, job_types):
        self.cursor.execute("""
            INSERT INTO crawl_request
            (user_id, job_categories, locations, experiences, educations, job_types)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, job_categories, locations, experiences, educations, job_types))
        self.conn.commit()

    def get_pending_crawl_requests(self):
        self.dict_cursor.execute(
            "SELECT * FROM crawl_request WHERE status = 'pending' ORDER BY created_at"
        )
        return self.dict_cursor.fetchall()

    def mark_crawl_request_done(self, request_id, saved, found):
        self.cursor.execute("""
            UPDATE crawl_request
            SET status = 'done', found_count = %s, saved_count = %s, processed_at = NOW()
            WHERE id = %s
        """, (found, saved, request_id))
        self.conn.commit()

    def get_recent_crawl_requests(self, user_id, limit=5):
        self.dict_cursor.execute("""
            SELECT * FROM crawl_request WHERE user_id = %s
            ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        return self.dict_cursor.fetchall()

    def close(self):
        self.cursor.close()
        self.conn.close()
