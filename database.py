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

    # ------------------------------------------------------------
    # 테이블 / 인덱스 준비
    # ------------------------------------------------------------
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
        """기존에 만들어둔 테이블에 career/education 컬럼이 없다면 추가"""
        for column, ddl in [
            ("career", "ALTER TABLE job_posting ADD COLUMN career VARCHAR(255)"),
            ("education", "ALTER TABLE job_posting ADD COLUMN education VARCHAR(255)"),
            ("status", f"ALTER TABLE job_posting ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_STATUS}'"),
            ("memo", "ALTER TABLE job_posting ADD COLUMN memo TEXT"),
            ("applied_at", "ALTER TABLE job_posting ADD COLUMN applied_at TIMESTAMP NULL"),
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
    def get_jobs(self, region=None, keyword=None, condition_name=None,
                 status=None, group_by_company=False):
        """
        region: 위치(location)에 포함된 문자열로 필터 (예: '울산')
        keyword: 회사명 또는 제목에 포함된 문자열로 필터
        condition_name: 특정 검색 조건으로 수집된 데이터만 필터
        status: 지원 현황 상태로 필터 (STATUS_CHOICES 중 하나)
        group_by_company: True면 회사명 기준으로 정렬해서 반환 (회사별 보기용)

        반환값: 각 공고를 dict로 담은 리스트
        """
        sql = """
        SELECT id, title, company, location, link, condition_name,
               career, education, status, memo, applied_at
        FROM job_posting WHERE 1=1
        """
        params = []

        if region:
            sql += " AND location LIKE %s"
            params.append(f"%{region}%")

        if keyword:
            sql += " AND (company LIKE %s OR title LIKE %s)"
            params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")

        if condition_name:
            sql += " AND condition_name = %s"
            params.append(condition_name)

        if status:
            sql += " AND status = %s"
            params.append(status)

        if group_by_company:
            sql += " ORDER BY company, id DESC"
        else:
            sql += " ORDER BY id DESC"

        self.dict_cursor.execute(sql, tuple(params))
        return self.dict_cursor.fetchall()

    # ------------------------------------------------------------
    # 지원 현황 (상태 / 메모) 갱신
    # ------------------------------------------------------------
    def update_status(self, job_id, status):
        """공고의 지원 상태를 변경. '지원함'으로 바뀌는 최초 시점에 applied_at을 기록."""
        if status not in STATUS_CHOICES:
            raise ValueError(f"알 수 없는 상태값: {status}")

        if status == "지원함":
            self.cursor.execute("""
                UPDATE job_posting
                SET status = %s, applied_at = COALESCE(applied_at, NOW())
                WHERE id = %s
            """, (status, job_id))
        else:
            self.cursor.execute(
                "UPDATE job_posting SET status = %s WHERE id = %s",
                (status, job_id),
            )
        self.conn.commit()

    def update_memo(self, job_id, memo):
        self.cursor.execute(
            "UPDATE job_posting SET memo = %s WHERE id = %s",
            (memo, job_id),
        )
        self.conn.commit()

    def get_condition_names(self):
        """Flask 필터 드롭다운에 쓸 조건 목록"""
        self.cursor.execute("""
        SELECT DISTINCT condition_name
        FROM job_posting
        WHERE condition_name IS NOT NULL
        ORDER BY condition_name
        """)
        return [row[0] for row in self.cursor.fetchall()]

    def close(self):
        self.cursor.close()
        self.conn.close()
