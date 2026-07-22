import mysql.connector
from config import MYSQL_CONFIG

# 지원 현황 상태값
STATUS_CHOICES = ["미확인", "관심있음", "지원함", "제외"]
DEFAULT_STATUS = STATUS_CHOICES[0]


class DB:

    def __init__(self):
        self.conn = mysql.connector.connect(**MYSQL_CONFIG, use_pure=True)
        self.cursor = self.conn.cursor()
        self.dict_cursor = self.conn.cursor(dictionary=True)
        self._ensure_table()
        self._ensure_unique_index()
        self._ensure_users_table()
        self._ensure_user_job_state_table()
        self._migrate_legacy_status_to_per_user()
        self._ensure_schedule_table()

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

    def _ensure_unique_index(self):
        # link 컬럼에 UNIQUE 인덱스가 없으면 추가 (중복 저장 방지용)
        # link가 500자라 인덱스는 앞부분만 사용 (255byte)
        self.cursor.execute("""
        SELECT COUNT(*) FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'job_posting'
          AND index_name = 'uniq_link'
        """)
        exists = self.cursor.fetchone()[0]

        if not exists:
            try:
                self.cursor.execute("""
                ALTER TABLE job_posting
                ADD UNIQUE INDEX uniq_link (link(255))
                """)
                self.conn.commit()
            except mysql.connector.Error as e:
                # 이미 중복 데이터가 있어 인덱스 생성이 실패할 수 있음
                print(f"UNIQUE 인덱스 생성 실패 (기존 중복 데이터 존재 가능): {e}")

    # ------------------------------------------------------------
    # 삭제
    # ------------------------------------------------------------
    def count_jobs(self):
        """저장된 채용공고 총 개수"""
        self.cursor.execute("SELECT COUNT(*) FROM job_posting")
        return self.cursor.fetchone()[0]

    def clear_jobs(self):
        """저장된 채용공고를 모두 삭제 (새 검색 시작 전 초기화용)"""
        self.cursor.execute("DELETE FROM job_posting")
        self.conn.commit()

    # ------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------
    def insert_job(self, title, company, location, link, condition_name=None,
                   career=None, education=None):
        """
        link가 이미 존재하면 저장을 건너뜁니다 (중복 방지).
        반환값: 새로 저장됐으면 True, 이미 있어서 건너뛰었으면 False
        """
        sql = """
        INSERT IGNORE INTO job_posting
        (title, company, location, link, condition_name, career, education)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        self.cursor.execute(
            sql,
            (title, company, location, link, condition_name, career, education),
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
               ujs.applied_at AS applied_at
        FROM job_posting jp
        LEFT JOIN user_job_state ujs ON ujs.job_id = jp.id AND ujs.user_id = %s
        WHERE 1=1
        """
        params = [DEFAULT_STATUS, user_id]

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

    def get_interested_jobs(self, user_id):
        """이 계정이 '관심있음'으로 표시한 공고 전체 (자기소개서 방향성 가이드용)"""
        self.dict_cursor.execute("""
        SELECT jp.id, jp.title, jp.company, jp.location, jp.condition_name,
               jp.career, jp.education, ujs.memo AS memo
        FROM job_posting jp
        JOIN user_job_state ujs ON ujs.job_id = jp.id AND ujs.user_id = %s
        WHERE ujs.status = '관심있음'
        ORDER BY jp.id DESC
        """, (user_id,))
        return self.dict_cursor.fetchall()

    def get_condition_names(self):
        """Flask 필터 드롭다운에 쓸 조건 목록"""
        self.cursor.execute("""
        SELECT DISTINCT condition_name
        FROM job_posting
        WHERE condition_name IS NOT NULL
        ORDER BY condition_name
        """)
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
        오늘 아직 실행되지 않은, 지금 이 시각에 예약된 스케줄들을 반환.
        """
        self.dict_cursor.execute("""
            SELECT * FROM schedule_setting
            WHERE enabled = 1 AND schedule_time = %s
              AND (last_run_date IS NULL OR last_run_date != %s)
        """, (current_time, today))
        return self.dict_cursor.fetchall()

    def mark_schedule_run(self, user_id, run_date):
        self.cursor.execute(
            "UPDATE schedule_setting SET last_run_date = %s WHERE user_id = %s",
            (run_date, user_id),
        )
        self.conn.commit()

    def close(self):
        self.cursor.close()
        self.conn.close()
