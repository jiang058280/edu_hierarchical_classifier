"""业务 Store：分类留痕 / 题库 / 反馈 / 统计 / 模型版本。

设计约束（对齐 knowforge）：
- application 层不直接写 SQL，全部通过本模块的 Store；
- 使用 SQLAlchemy Core（text() + 参数绑定），不引入 ORM 全家桶；
- 连接串来自 settings.mysql_url，进程级单例引擎。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings, get_settings

logger = get_logger(__name__)


def text_hash(content: str) -> str:
    """题目/分类文本指纹（sha256，去首尾空白后归一化）。"""
    return hashlib.sha256(" ".join(content.split()).encode("utf-8")).hexdigest()


def sqlalchemy_error_to_message(exc: Exception) -> str | None:
    """把 SQLAlchemy/PyMySQL 异常转成用户可读信息；非数据库异常返回 None。"""
    from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
    if isinstance(exc, (OperationalError, ProgrammingError)):
        return "数据库连接失败或表结构未初始化，请先运行 scripts/init_db.py"
    if isinstance(exc, SQLAlchemyError):
        return "数据库操作失败"
    return None


_engine: Engine | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    """进程级 SQLAlchemy 引擎单例（连接池回收防 MySQL 8h 断连）。

    Settings 含 dict/list 字段不可哈希，故用模块级单例而非 lru_cache；
    首次调用传入的 settings 决定连接串，后续调用复用同一引擎。
    """
    global _engine
    if _engine is None:
        s = settings or get_settings()
        _engine = create_engine(
            s.mysql_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
    return _engine


class ModelVersionStore:
    """模型版本注册表 + active 指针。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def register(self, version: str, directory: str, manifest: dict | None = None,
                 metrics: dict | None = None, description: str = "") -> int:
        """注册新版本（STAGED）。版本号重复时报错。"""
        with self.engine.begin() as conn:
            exists = conn.execute(
                text("SELECT id FROM model_versions WHERE version = :v"), {"v": version}).first()
            if exists:
                raise ValueError(f"模型版本已存在：{version}")
            row = conn.execute(text("""
                INSERT INTO model_versions (version, directory, manifest_json, metrics_json, status, description)
                VALUES (:v, :d, :m, :mt, 'STAGED', :desc)
            """), {
                "v": version, "d": directory,
                "m": json.dumps(manifest, ensure_ascii=False) if manifest else None,
                "mt": json.dumps(metrics, ensure_ascii=False) if metrics else None,
                "desc": description,
            })
            logger.info("模型版本已注册（STAGED）：%s -> %s", version, directory)
            return row.lastrowid

    def update_metrics(self, version: str, metrics: dict) -> None:
        """更新版本评估指标（评估脚本写入）。"""
        with self.engine.begin() as conn:
            conn.execute(text("""
                UPDATE model_versions SET metrics_json = :mt WHERE version = :v
            """), {"mt": json.dumps(metrics, ensure_ascii=False), "v": version})

    def activate(self, version: str) -> None:
        """激活版本：目标置 ACTIVE，其余 ACTIVE/ARCHIVED 归档，指针指向目标。"""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT id FROM model_versions WHERE version = :v"), {"v": version}).first()
            if not row:
                raise ValueError(f"模型版本不存在：{version}")
            conn.execute(text(
                "UPDATE model_versions SET status = 'ARCHIVED' WHERE status IN ('ACTIVE','ARCHIVED')"))
            conn.execute(text(
                "UPDATE model_versions SET status = 'ACTIVE' WHERE version = :v"), {"v": version})
            conn.execute(text("""
                INSERT INTO active_model_pointer (id, active_version) VALUES (1, :v)
                ON DUPLICATE KEY UPDATE active_version = :v
            """), {"v": version})
        logger.info("模型版本已激活：%s", version)

    def get_active(self) -> dict | None:
        """当前 active 版本（含目录与指标）。"""
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT v.version, v.directory, v.manifest_json, v.metrics_json, v.status
                FROM model_versions v
                JOIN active_model_pointer p ON p.active_version = v.version
                WHERE p.id = 1
            """)).mappings().first()
        if not row:
            return None
        data = dict(row)
        data["manifest"] = json.loads(data.pop("manifest_json")) if data.get("manifest_json") else {}
        data["metrics"] = json.loads(data.pop("metrics_json")) if data.get("metrics_json") else {}
        return data

    def list_versions(self) -> list[dict]:
        """全部版本（新->旧）。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT version, directory, status, description, metrics_json, created_at
                FROM model_versions ORDER BY id DESC
            """)).mappings().all()
        out = []
        for r in rows:
            data = dict(r)
            data["metrics"] = json.loads(data.pop("metrics_json")) if data.get("metrics_json") else None
            data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(data["created_at"], datetime) else data["created_at"]
            out.append(data)
        return out


class ClassificationStore:
    """分类留痕。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def insert(self, text_content: str, model_version: str, result: dict,
               avg_confidence: float, band: str) -> int:
        """写入一次推理留痕，返回 id（供反馈关联）。"""
        conf = result["confidence"]
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO classifications
                    (text_hash, text_preview, model_version,
                     subject_pred, subject_conf, type_pred, type_conf,
                     knowledge_pred, knowledge_conf,
                     avg_confidence, confidence_band, latency_ms, cached)
                VALUES (:h, :tp, :mv, :sp, :sc, :yp, :yc, :kp, :kc, :avg, :band, :lat, :cached)
            """), {
                "h": text_hash(text_content),
                "tp": text_content[:500],
                "mv": model_version,
                "sp": result["subject"], "sc": conf["subject"],
                "yp": result["question_type"], "yc": conf["question_type"],
                "kp": result["knowledge_point"], "kc": conf["knowledge_point"],
                "avg": avg_confidence, "band": band,
                "lat": result.get("latency_ms", 0.0),
                "cached": 1 if result.get("cached") else 0,
            })
            return row.lastrowid

    def get(self, classification_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM classifications WHERE id = :i"), {"i": classification_id}
            ).mappings().first()
        return dict(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, text_preview, model_version, subject_pred, type_pred,
                       knowledge_pred, avg_confidence, confidence_band, latency_ms, created_at
                FROM classifications ORDER BY id DESC LIMIT :l
            """), {"l": int(limit)}).mappings().all()
        return [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")} for r in rows]

    def subject_distribution(self) -> dict[str, int]:
        """全量分类按学科分布（SQL 聚合，替代旧版内存统计）。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT subject_pred AS s, COUNT(*) AS c FROM classifications GROUP BY subject_pred"
            )).all()
        return {r.s: int(r.c) for r in rows}


class QuestionStore:
    """题库（持久化，替代旧版内存 question_db）。

    平台 M1 起支持完整题目信息：结构化选项/答案/解析/难度/学段/知识点树挂接/审核状态。
    """

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    # 可编辑字段白名单（update 动态 SET 用）
    _EDITABLE = ("content", "subject", "question_type", "knowledge_point", "answer",
                 "analysis", "difficulty", "grade_band", "grade", "knowledge_node_id", "status", "options_json")

    def insert(self, content: str, subject: str, question_type: str = "",
               knowledge_point: str = "", source: str = "manual", *,
               options: list | None = None, answer: str | None = None,
               analysis: str | None = None, difficulty: int | None = None,
               grade_band: str | None = None, grade: str | None = None,
               knowledge_node_id: int | None = None,
               created_by: int | None = None, status: str = "published") -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO questions (content, subject, question_type, knowledge_point, source,
                                       options_json, answer, analysis, difficulty,
                                       grade_band, grade, knowledge_node_id, created_by, text_hash, status)
                VALUES (:c, :s, :t, :k, :src, :oj, :a, :an, :d, :gb, :g, :kn, :by, :h, :status)
            """), {
                "c": content, "s": subject, "t": question_type, "k": knowledge_point,
                "src": source,
                "oj": json.dumps(options, ensure_ascii=False) if options else None,
                "a": answer, "an": analysis, "d": difficulty,
                "gb": grade_band, "g": grade, "kn": knowledge_node_id, "by": created_by,
                "h": text_hash(content), "status": status,
            })
            return row.lastrowid

    def update(self, question_id: int, **fields) -> bool:
        """按白名单更新题目；content 变更时同步 text_hash。返回是否存在。"""
        sets, params = [], {"i": question_id}
        if "options" in fields:
            options = fields.pop("options")
            fields["options_json"] = json.dumps(options, ensure_ascii=False) if options else None
        for key, value in fields.items():
            if key not in self._EDITABLE:
                raise ValueError(f"非法编辑字段：{key}")
            sets.append(f"{key} = :{key}")
            params[key] = value
        if "content" in fields:
            sets.append("text_hash = :h")
            params["h"] = text_hash(fields["content"])
        if not sets:
            return True
        with self.engine.begin() as conn:
            self._require_unreferenced(conn, question_id)
            row = conn.execute(text(
                f"UPDATE questions SET {', '.join(sets)} WHERE id = :i"), params)
            return row.rowcount > 0

    def delete(self, question_id: int) -> bool:
        with self.engine.begin() as conn:
            self._require_unreferenced(conn, question_id)
            row = conn.execute(text(
                "DELETE FROM questions WHERE id = :i"), {"i": question_id})
            return row.rowcount > 0

    @staticmethod
    def _require_unreferenced(conn, question_id: int) -> None:
        # 当前试卷使用题目引用；锁定原题，避免已有试卷与学生历史被改写。
        conn.execute(text("SELECT id FROM questions WHERE id = :i FOR UPDATE"),
                     {"i": question_id}).first()
        used = conn.execute(text(
            "SELECT paper_id FROM paper_questions WHERE question_id = :i LIMIT 1"
        ), {"i": question_id}).first()
        if used:
            raise ValueError("题目已被试卷使用，不能修改或删除；请另存新题后重新组卷")

    def get(self, question_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM questions WHERE id = :i"),
                               {"i": question_id}).mappings().first()
        if not row:
            return None
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json")) if data.get("options_json") else None
        data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return data

    def list(self, subject: str = "", question_type: str = "", keyword: str = "",
             limit: int = 100, offset: int = 0, grade_band: str = "",
             difficulty: int | None = None, status: str = "",
             knowledge: str = "") -> tuple[list[dict], int]:
        """筛选列表 + 总数（平台 M1：新增 学段/难度/状态/知识点 筛选）。"""
        conditions, params = [], {"limit": int(limit), "offset": int(offset)}
        if subject:
            conditions.append("subject = :subject")
            params["subject"] = subject
        if question_type:
            conditions.append("question_type = :qtype")
            params["qtype"] = question_type
        if grade_band:
            conditions.append("grade_band = :gb")
            params["gb"] = grade_band
        if difficulty is not None:
            conditions.append("difficulty = :diff")
            params["diff"] = int(difficulty)
        if status:
            conditions.append("status = :st")
            params["st"] = status
        if knowledge:
            conditions.append("knowledge_point LIKE :kp")
            params["kp"] = f"%{knowledge}%"
        if keyword:
            conditions.append("(content LIKE :kw OR knowledge_point LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.engine.connect() as conn:
            total = conn.execute(text(f"SELECT COUNT(*) FROM questions {where}"), params).scalar_one()
            rows = conn.execute(text(f"""
                SELECT id, content, subject, question_type, knowledge_point, source, status,
                       difficulty, grade_band, grade, answer, created_at
                FROM questions {where} ORDER BY id DESC LIMIT :limit OFFSET :offset
            """), params).mappings().all()
        items = [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")} for r in rows]
        return items, int(total)

    def count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one())

    def question_type_counts(self, subject: str = "", grade_band: str = "",
                             status: str = "published", knowledge: str = "",
                             difficulty: int | None = None) -> dict[str, int]:
        """按题型聚合数量，供组卷配额在选择前展示可用库存。"""
        conditions, params = [], {}
        if subject:
            conditions.append("subject = :subject")
            params["subject"] = subject
        if grade_band:
            conditions.append("grade_band = :grade_band")
            params["grade_band"] = grade_band
        if status:
            conditions.append("status = :status")
            params["status"] = status
        if knowledge:
            conditions.append("knowledge_point LIKE :knowledge")
            params["knowledge"] = f"%{knowledge}%"
        if difficulty is not None:
            conditions.append("difficulty = :difficulty")
            params["difficulty"] = int(difficulty)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT question_type, COUNT(*) AS total
                FROM questions {where}
                GROUP BY question_type
            """), params).mappings().all()
        return {str(row["question_type"]): int(row["total"]) for row in rows}


class FeedbackStore:
    """反馈（必须关联 classification_id，才能用于再训练闭环）。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def insert(self, classification_id: int | None, is_correct: bool,
               question_text: str | None = None, subject: str | None = None,
               corrected_subject: str | None = None, corrected_type: str | None = None,
               corrected_knowledge: str | None = None, comment: str | None = None,
               model_version: str | None = None) -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO feedback
                    (classification_id, is_correct, question_text, subject,
                     corrected_subject, corrected_type, corrected_knowledge, comment, model_version)
                VALUES (:cid, :ok, :txt, :subj, :cs, :ct, :ck, :cmt, :mv)
            """), {
                "cid": classification_id, "ok": 1 if is_correct else 0,
                "txt": (question_text or "")[:500] or None, "subj": subject,
                "cs": corrected_subject, "ct": corrected_type, "ck": corrected_knowledge,
                "cmt": comment, "mv": model_version,
            })
            return row.lastrowid

    def list(self, limit: int = 100, is_correct: bool | None = None) -> list[dict]:
        where, params = "", {"limit": int(limit)}
        if is_correct is not None:
            where = "WHERE is_correct = :ok"
            params["ok"] = 1 if is_correct else 0
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id, classification_id, is_correct, question_text, subject,
                       corrected_subject, corrected_type, corrected_knowledge, model_version, created_at
                FROM feedback {where} ORDER BY id DESC LIMIT :limit
            """), params).mappings().all()
        return [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")} for r in rows]

    def counts(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT SUM(is_correct = 1) AS ok, SUM(is_correct = 0) AS bad, COUNT(*) AS total FROM feedback"
            )).first()
        return {"total_correct": int(row.ok or 0), "total_wrong": int(row.bad or 0), "total": int(row.total or 0)}

    def analysis(self) -> dict:
        """学情分析：各学科反馈正确率 + 错误反馈薄弱学科分布（SQL 聚合）。"""
        with self.engine.connect() as conn:
            rates = conn.execute(text("""
                SELECT subject AS s, SUM(is_correct = 1) AS ok, COUNT(*) AS total
                FROM feedback WHERE subject IS NOT NULL AND subject <> ''
                GROUP BY subject
            """)).all()
            weak = conn.execute(text("""
                SELECT subject AS s, COUNT(*) AS c FROM feedback
                WHERE is_correct = 0 AND subject IS NOT NULL AND subject <> ''
                GROUP BY subject ORDER BY c DESC
            """)).all()
        subject_rates = {
            r.s: round(float(r.ok) / float(r.total), 4) if r.total else 0.0 for r in rates
        }
        weak_points = {r.s: int(r.c) for r in weak}
        return {"subject_correct_rates": subject_rates, "weak_points": weak_points}


class StatsStore:
    """按日累计统计（修复旧版单日覆盖丢历史）。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def bump_processed(self, avg_confidence: float) -> None:
        """分类数 +1，滑动平均置信度（原子 upsert，单行数学在 SQL 内完成）。"""
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO daily_stats (stat_date, total_processed, avg_confidence)
                VALUES (:d, 1, :c)
                ON DUPLICATE KEY UPDATE
                    total_processed = total_processed + 1,
                    avg_confidence = ROUND(
                        (avg_confidence * (total_processed - 1) + :c) / total_processed, 4)
            """), {"d": date.today(), "c": avg_confidence})

    def bump_feedback(self, is_correct: bool) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO daily_stats (stat_date, total_correct, total_wrong)
                VALUES (:d, :ok, :bad)
                ON DUPLICATE KEY UPDATE
                    total_correct = total_correct + :ok,
                    total_wrong = total_wrong + :bad
            """), {"d": date.today(), "ok": 1 if is_correct else 0, "bad": 0 if is_correct else 1})

    def today(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM daily_stats WHERE stat_date = :d"), {"d": date.today()}
            ).mappings().first()
        if not row:
            return {"stat_date": str(date.today()), "total_processed": 0,
                    "total_correct": 0, "total_wrong": 0, "avg_confidence": 0.0}
        data = dict(row)
        data["stat_date"] = str(data["stat_date"])
        return data


class UserStore:
    """用户（JWT 登录 + RBAC 角色；学生角色见平台计划 M0）。"""

    ALLOWED_ROLES = ("admin", "teacher", "student")

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def create(self, username: str, password_hash: str, role: str = "teacher",
               real_name: str | None = None, grade_band: str | None = None,
               grade: str | None = None, class_id: int | None = None,
               student_no: str | None = None,
               must_change_password: bool = False) -> int:
        if role not in self.ALLOWED_ROLES:
            raise ValueError(f"非法角色：{role}（仅 {'/'.join(self.ALLOWED_ROLES)}）")
        with self.engine.begin() as conn:
            exists = conn.execute(
                text("SELECT id FROM users WHERE username = :u"), {"u": username}).first()
            if exists:
                raise ValueError(f"用户名已存在：{username}")
            row = conn.execute(text("""
                INSERT INTO users (username, password_hash, role, real_name,
                                   grade_band, grade, class_id, student_no, must_change_password)
                VALUES (:u, :p, :r, :rn, :gb, :g, :cid, :sno, :mcp)
            """), {
                "u": username, "p": password_hash, "r": role, "rn": real_name,
                "gb": grade_band, "g": grade, "cid": class_id, "sno": student_no,
                "mcp": 1 if must_change_password else 0,
            })
            return row.lastrowid

    def get_by_username(self, username: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, username, password_hash, role, is_active,
                       real_name, grade_band, grade, class_id, student_no
                FROM users WHERE username = :u
            """), {"u": username}).mappings().first()
        return dict(row) if row else None

    def get(self, user_id: int) -> dict | None:
        """按主键读取用户基本资料，供作业归属等服务层校验。"""
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, username, role, is_active, real_name,
                       grade_band, grade, class_id, student_no
                FROM users WHERE id = :i
            """), {"i": int(user_id)}).mappings().first()
        return dict(row) if row else None

    def count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one())

    def count_by_role(self, role: str) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(
                text("SELECT COUNT(*) FROM users WHERE role = :r"), {"r": role}).scalar_one())

    def list_by_class(self, class_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, username, real_name, grade_band, grade, student_no, is_active, created_at
                FROM users WHERE class_id = :c AND role = 'student' ORDER BY id
            """), {"c": class_id}).mappings().all()
        return [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")}
                for r in rows]

    def assign_class(self, user_id: int, class_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE users SET class_id = :c WHERE id = :i"), {"c": class_id, "i": user_id})


class ClassStore:
    """班级（平台计划 M0）：建班/邀请码入班/名单。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def create(self, name: str, grade_band: str, grade: str, created_by: int,
               invite_code: str) -> int:
        if grade_band not in ("初中", "高中"):
            raise ValueError(f"学段仅支持 初中/高中：{grade_band}")
        with self.engine.begin() as conn:
            dup = conn.execute(text(
                "SELECT id FROM classes WHERE name = :n AND is_active = 1"),
                {"n": name}).first()
            if dup:
                raise ValueError(f"班级名已存在：{name}")
            row = conn.execute(text("""
                INSERT INTO classes (name, grade_band, grade, invite_code, created_by)
                VALUES (:n, :gb, :g, :code, :by)
            """), {"n": name, "gb": grade_band, "g": grade, "code": invite_code, "by": created_by})
            return row.lastrowid

    def get(self, class_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM classes WHERE id = :i"),
                               {"i": class_id}).mappings().first()
        data = dict(row) if row else None
        if data:
            data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return data

    def get_by_invite_code(self, code: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM classes WHERE invite_code = :c AND is_active = 1"),
                {"c": code}).mappings().first()
        data = dict(row) if row else None
        if data:
            data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return data

    def list_by_teacher(self, teacher_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT c.*, (SELECT COUNT(*) FROM users u
                             WHERE u.class_id = c.id AND u.role = 'student') AS student_count
                FROM classes c WHERE c.created_by = :t AND c.is_active = 1 ORDER BY c.id DESC
            """), {"t": teacher_id}).mappings().all()
        return [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")}
                for r in rows]

    def join(self, class_id: int, student_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE users SET class_id = :c WHERE id = :i"), {"c": class_id, "i": student_id})


class KnowledgeNodeStore:
    """知识点树（平台计划 M0；播种由 scripts/seed_knowledge_nodes.py 完成）。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def list(self, subject: str = "", grade_band: str | None = None,
             level: int | None = None) -> list[dict]:
        conditions, params = [], {}
        if subject:
            conditions.append("subject = :s")
            params["s"] = subject
        if grade_band is not None:
            conditions.append("grade_band = :gb")
            params["gb"] = grade_band
        if level is not None:
            conditions.append("level = :l")
            params["l"] = level
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT id, parent_id, subject, grade_band, name, level, is_active "
                f"FROM knowledge_nodes {where} ORDER BY subject, level, id"), params).mappings().all()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM knowledge_nodes")).scalar_one())


class PaperStore:
    """试卷库（平台计划 M1）：试卷与受保护的题目引用。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def create(self, title: str, subject: str, grade_band: str, created_by: int,
               question_ids: list[int]) -> int:
        if not question_ids:
            raise ValueError("试卷至少需要一道题目")
        # 幂等去重（保持顺序）：同一题不允许在一次组卷中重复出现
        deduped: list[int] = []
        seen: set[int] = set()
        for qid in question_ids:
            qid = int(qid)
            if qid not in seen:
                seen.add(qid)
                deduped.append(qid)
        with self.engine.begin() as conn:
            for qid in sorted(deduped):
                if not conn.execute(text(
                    "SELECT id FROM questions WHERE id = :i FOR UPDATE"
                ), {"i": qid}).first():
                    raise ValueError(f"题目不存在：{qid}")
            row = conn.execute(text("""
                INSERT INTO papers (title, subject, grade_band, created_by)
                VALUES (:t, :s, :gb, :by)
            """), {"t": title, "s": subject, "gb": grade_band, "by": created_by})
            paper_id = row.lastrowid
            for order_no, qid in enumerate(deduped, start=1):
                conn.execute(text("""
                    INSERT INTO paper_questions (paper_id, question_id, order_no)
                    VALUES (:p, :q, :o)
                """), {"p": paper_id, "q": qid, "o": order_no})
            return paper_id

    def get(self, paper_id: int) -> dict | None:
        with self.engine.connect() as conn:
            head = conn.execute(text("SELECT * FROM papers WHERE id = :i"),
                                {"i": paper_id}).mappings().first()
            if not head:
                return None
            data = dict(head)
            data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            qrows = conn.execute(text("""
                SELECT q.id, q.content, q.subject, q.question_type, q.knowledge_point,
                       q.options_json, q.answer, q.analysis, q.difficulty, q.grade_band,
                       pq.order_no
                FROM paper_questions pq JOIN questions q ON q.id = pq.question_id
                WHERE pq.paper_id = :i ORDER BY pq.order_no
            """), {"i": paper_id}).mappings().all()
        questions = []
        for r in qrows:
            q = dict(r)
            q["options"] = json.loads(q.pop("options_json")) if q.get("options_json") else None
            questions.append(q)
        data["questions"] = questions
        return data

    def list(self, created_by: int | None = None, limit: int = 50,
             offset: int = 0) -> tuple[list[dict], int]:
        conditions, params = [], {"limit": int(limit), "offset": int(offset)}
        if created_by is not None:
            conditions.append("p.created_by = :by")
            params["by"] = int(created_by)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine.connect() as conn:
            total = conn.execute(text(
                f"SELECT COUNT(*) FROM papers p {where}"), params).scalar_one()
            rows = conn.execute(text(f"""
                SELECT p.id, p.title, p.subject, p.grade_band, p.created_at,
                       (SELECT COUNT(*) FROM paper_questions pq
                        JOIN questions q ON q.id = pq.question_id
                        WHERE pq.paper_id = p.id) AS question_count
                FROM papers p {where} ORDER BY p.id DESC LIMIT :limit OFFSET :offset
            """), params).mappings().all()
        items = [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")}
                 for r in rows]
        return items, int(total)

    def delete(self, paper_id: int) -> bool:
        with self.engine.begin() as conn:
            conn.execute(text("SELECT id FROM papers WHERE id = :i FOR UPDATE"),
                         {"i": paper_id}).first()
            if conn.execute(text(
                "SELECT id FROM assignments WHERE paper_id = :i LIMIT 1"
            ), {"i": paper_id}).first():
                raise ValueError("试卷已发布为作业，不能删除，以保留学生作答记录")
            conn.execute(text("DELETE FROM paper_questions WHERE paper_id = :i"), {"i": paper_id})
            row = conn.execute(text("DELETE FROM papers WHERE id = :i"), {"i": paper_id})
            return row.rowcount > 0


class AssignmentStore:
    """作业、提交与作答记录的事务型持久化。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    @staticmethod
    def _serialize_assignment(row) -> dict:
        data = dict(row)
        for key in ("created_at", "due_at", "submitted_at"):
            value = data.get(key)
            if isinstance(value, datetime):
                data[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        for key in ("auto_score", "final_score"):
            if data.get(key) is not None:
                data[key] = float(data[key])
        if "allow_self_check" in data:
            data["allow_self_check"] = bool(data["allow_self_check"])
        return data

    def create(self, *, paper_id: int, class_id: int, title: str,
               created_by: int, mode: str = "homework", due_at: datetime | None = None,
               allow_self_check: bool = True) -> int:
        with self.engine.begin() as conn:
            if not conn.execute(text("SELECT id FROM papers WHERE id = :i FOR UPDATE"),
                                {"i": paper_id}).first():
                raise ValueError("试卷不存在或已删除")
            row = conn.execute(text("""
                INSERT INTO assignments
                    (paper_id, class_id, title, mode, due_at, allow_self_check, created_by, created_at)
                VALUES (:p, :c, :t, :m, :due, :self_check, :by, :now)
            """), {
                "p": int(paper_id), "c": int(class_id), "t": title,
                "m": mode, "due": due_at,
                "self_check": 1 if allow_self_check else 0,
                "by": int(created_by),
                "now": datetime.now(),
            })
            return int(row.lastrowid)

    def get(self, assignment_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT a.*, p.subject, p.grade_band, p.title AS paper_title,
                       c.name AS class_name
                FROM assignments a
                JOIN papers p ON p.id = a.paper_id
                JOIN classes c ON c.id = a.class_id
                WHERE a.id = :i
            """), {"i": int(assignment_id)}).mappings().first()
        return self._serialize_assignment(row) if row else None

    def list_by_teacher(self, teacher_id: int, limit: int = 50,
                        offset: int = 0) -> tuple[list[dict], int]:
        params = {"teacher": int(teacher_id), "limit": int(limit), "offset": int(offset)}
        with self.engine.connect() as conn:
            total = conn.execute(text(
                "SELECT COUNT(*) FROM assignments WHERE created_by = :teacher"
            ), params).scalar_one()
            rows = conn.execute(text("""
                SELECT a.id, a.paper_id, a.class_id, a.title, a.mode, a.due_at,
                       a.allow_self_check, a.created_by, a.created_at,
                       p.subject, p.grade_band, p.title AS paper_title, c.name AS class_name,
                       COUNT(s.id) AS submission_count,
                       SUM(s.status IN ('submitted', 'checked')) AS completed_count
                FROM assignments a
                JOIN papers p ON p.id = a.paper_id
                JOIN classes c ON c.id = a.class_id
                LEFT JOIN submissions s ON s.assignment_id = a.id
                WHERE a.created_by = :teacher
                GROUP BY a.id, a.paper_id, a.class_id, a.title, a.mode, a.due_at,
                         a.allow_self_check, a.created_by, a.created_at,
                         p.subject, p.grade_band, p.title, c.name
                ORDER BY a.id DESC LIMIT :limit OFFSET :offset
            """), params).mappings().all()
        return [self._serialize_assignment(r) for r in rows], int(total)

    def list_by_student(self, student_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT a.id, a.paper_id, a.class_id, a.title, a.mode, a.due_at,
                       a.allow_self_check, a.created_at, p.subject, p.grade_band,
                       s.id AS submission_id, COALESCE(s.status, 'not_started') AS status,
                       s.auto_score, s.final_score, s.submitted_at
                FROM users u
                JOIN assignments a ON a.class_id = u.class_id OR EXISTS (
                    SELECT 1 FROM submissions history
                    WHERE history.assignment_id = a.id AND history.student_id = u.id
                      AND history.status IN ('submitted', 'checked'))
                JOIN papers p ON p.id = a.paper_id
                LEFT JOIN submissions s
                    ON s.assignment_id = a.id AND s.student_id = u.id
                WHERE u.id = :student AND u.role = 'student'
                ORDER BY a.id DESC
            """), {"student": int(student_id)}).mappings().all()
        return [self._serialize_assignment(r) for r in rows]

    def get_detail(self, assignment_id: int, student_id: int | None = None) -> dict | None:
        assignment = self.get(assignment_id)
        if not assignment:
            return None
        with self.engine.connect() as conn:
            qrows = conn.execute(text("""
                SELECT q.id, q.content, q.subject, q.question_type, q.knowledge_point,
                       q.options_json, q.answer, q.analysis, q.difficulty, q.grade_band,
                       pq.order_no, pq.score
                FROM assignments a
                JOIN paper_questions pq ON pq.paper_id = a.paper_id
                JOIN questions q ON q.id = pq.question_id
                WHERE a.id = :i ORDER BY pq.order_no
            """), {"i": int(assignment_id)}).mappings().all()
            submission = None
            records = []
            if student_id is not None:
                submission = conn.execute(text("""
                    SELECT id, assignment_id, student_id, status, auto_score,
                           final_score, submitted_at
                    FROM submissions
                    WHERE assignment_id = :a AND student_id = :s
                """), {"a": int(assignment_id), "s": int(student_id)}).mappings().first()
                if submission:
                    records = conn.execute(text("""
                        SELECT question_id, answer, is_correct, source, created_at
                        FROM answer_records WHERE submission_id = :submission
                        ORDER BY id
                    """), {"submission": int(submission["id"])}).mappings().all()

        questions = []
        for row in qrows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json")) if item.get("options_json") else None
            item["score"] = float(item["score"])
            questions.append(item)
        assignment["questions"] = questions
        assignment["submission"] = self._serialize_assignment(submission) if submission else None
        assignment["answers"] = []
        for row in records:
            item = self._serialize_assignment(row)
            if item.get("is_correct") is not None:
                item["is_correct"] = bool(item["is_correct"])
            assignment["answers"].append(item)
        return assignment

    def submit(self, *, assignment_id: int, student_id: int,
               answers: list[dict]) -> dict:
        """原子写入一次提交及其全部作答；已提交/已批改时拒绝重复提交。"""
        gradable = [item for item in answers
                    if item.get("auto_gradable", True) and item.get("is_correct") is not None]
        auto_score = None
        if gradable:
            correct = sum(1 for item in gradable if item["is_correct"] is True)
            auto_score = round(correct / len(gradable) * 100.0, 1)

        with self.engine.begin() as conn:
            # 唯一键 upsert 先获得记录锁，避免两个首次提交同时查空后的插入竞态。
            conn.execute(text("""
                INSERT INTO submissions (assignment_id, student_id, status)
                VALUES (:a, :s, 'in_progress')
                ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
            """), {"a": int(assignment_id), "s": int(student_id)})
            existing = conn.execute(text("""
                SELECT id, status FROM submissions
                WHERE assignment_id = :a AND student_id = :s FOR UPDATE
            """), {"a": int(assignment_id), "s": int(student_id)}).mappings().first()
            if existing and existing["status"] in ("submitted", "checked"):
                raise ValueError("该作业已经提交，不能重复提交")
            # 截止字段采用应用本地时间；不能与可能运行在 UTC 的数据库 NOW() 混用。
            now = datetime.now()
            allowed = conn.execute(text("""
                SELECT a.id FROM assignments a JOIN users u ON u.class_id = a.class_id
                WHERE a.id = :a AND u.id = :s AND u.is_active = 1
                  AND (a.due_at IS NULL OR a.due_at > :now)
            """), {"a": int(assignment_id), "s": int(student_id), "now": now}).first()
            if not allowed:
                raise ValueError("作业已截止或学生班级已变化，请刷新后重试")
            submission_id = int(existing["id"])
            conn.execute(text(
                "DELETE FROM answer_records WHERE submission_id = :i"
            ), {"i": submission_id})

            for item in answers:
                conn.execute(text("""
                    INSERT INTO answer_records
                        (submission_id, student_id, question_id, answer, is_correct, source, created_at)
                    VALUES (:submission, :student, :question, :answer, :correct, 'assignment', :now)
                """), {
                    "submission": submission_id,
                    "student": int(student_id),
                    "question": int(item["question_id"]),
                    "answer": item.get("answer"),
                    "correct": None if item.get("is_correct") is None
                    else (1 if item["is_correct"] else 0),
                    "now": now,
                })
            conn.execute(text("""
                UPDATE submissions
                SET status = 'submitted', auto_score = :score, submitted_at = :now
                WHERE id = :i
            """), {"score": auto_score, "i": submission_id, "now": now})
        return {"submission_id": submission_id, "status": "submitted", "auto_score": auto_score}

    def get_submission(self, submission_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT s.*, a.created_by, a.class_id, a.title AS assignment_title
                FROM submissions s JOIN assignments a ON a.id = s.assignment_id
                WHERE s.id = :i
            """), {"i": int(submission_id)}).mappings().first()
        return self._serialize_assignment(row) if row else None

    def list_submissions(self, assignment_id: int) -> list[dict]:
        """返回班级完整名册及提交状态，未开始的学生也包含在内。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT u.id AS student_id, u.username, u.real_name, u.student_no,
                       s.id AS submission_id,
                       COALESCE(s.status, 'not_started') AS status,
                       s.auto_score, s.final_score, s.submitted_at,
                       SUM(ar.answer IS NOT NULL AND TRIM(ar.answer) <> '') AS answered_count,
                       SUM(ar.id IS NOT NULL AND ar.is_correct IS NULL) AS pending_count
                FROM assignments a
                JOIN users u
                  ON u.class_id = a.class_id
                 AND u.role = 'student'
                 AND u.is_active = 1
                LEFT JOIN submissions s
                  ON s.assignment_id = a.id AND s.student_id = u.id
                LEFT JOIN answer_records ar ON ar.submission_id = s.id
                WHERE a.id = :assignment
                GROUP BY u.id, u.username, u.real_name, u.student_no,
                         s.id, s.status, s.auto_score, s.final_score, s.submitted_at
                ORDER BY u.id
            """), {"assignment": int(assignment_id)}).mappings().all()
            answer_rows = conn.execute(text("""
                SELECT s.id AS submission_id, ar.question_id, ar.answer, ar.is_correct
                FROM submissions s
                JOIN answer_records ar ON ar.submission_id = s.id
                WHERE s.assignment_id = :assignment
                ORDER BY s.id, ar.id
            """), {"assignment": int(assignment_id)}).mappings().all()
        answers_by_submission: dict[int, list[dict]] = {}
        for row in answer_rows:
            item = dict(row)
            if item["is_correct"] is not None:
                item["is_correct"] = bool(item["is_correct"])
            answers_by_submission.setdefault(int(item.pop("submission_id")), []).append(item)
        items = []
        for row in rows:
            item = self._serialize_assignment(row)
            item["answered_count"] = int(item.get("answered_count") or 0)
            item["pending_count"] = int(item.get("pending_count") or 0)
            item["answers"] = answers_by_submission.get(item.get("submission_id"), [])
            items.append(item)
        return items

    def check(self, submission_id: int, final_score: float) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE submissions SET status = 'checked', final_score = :score
                WHERE id = :i AND status IN ('submitted', 'checked')
            """), {"i": int(submission_id), "score": float(final_score)})
            return row.rowcount > 0


class AuditStore:
    """治理动作审计日志（谁/何时/做了什么/来源 IP）。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def insert(self, action: str, user_id: int | None = None, username: str | None = None,
               resource: str | None = None, detail: dict | None = None,
               client_ip: str | None = None) -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO audit_logs (user_id, username, action, resource, detail_json, client_ip)
                VALUES (:uid, :u, :a, :r, :d, :ip)
            """), {
                "uid": user_id, "u": username, "a": action, "r": resource,
                "d": json.dumps(detail, ensure_ascii=False) if detail else None,
                "ip": client_ip,
            })
            return row.lastrowid

    def recent(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, user_id, username, action, resource, detail_json, client_ip, created_at
                FROM audit_logs ORDER BY id DESC LIMIT :l
            """), {"l": int(limit)}).mappings().all()
        out = []
        for r in rows:
            data = dict(r)
            data["detail"] = json.loads(data.pop("detail_json")) if data.get("detail_json") else None
            data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            out.append(data)
        return out


class StoreBundle:
    """全部 Store 的聚合容器，service 层一次注入。"""

    def __init__(self, engine: Engine | None = None, settings: Settings | None = None):
        self.engine = engine or get_engine(settings)
        self.model_versions = ModelVersionStore(self.engine)
        self.classifications = ClassificationStore(self.engine)
        self.questions = QuestionStore(self.engine)
        self.feedback = FeedbackStore(self.engine)
        self.stats = StatsStore(self.engine)
        self.users = UserStore(self.engine)
        self.audit = AuditStore(self.engine)
        self.classes = ClassStore(self.engine)
        self.knowledge_nodes = KnowledgeNodeStore(self.engine)
        self.papers = PaperStore(self.engine)
        self.assignments = AssignmentStore(self.engine)
