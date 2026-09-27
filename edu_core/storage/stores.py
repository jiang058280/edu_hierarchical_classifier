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


def is_unreadable_text(content: str | None) -> bool:
    """识别已经以问号/替换符写入数据库、无法人工复核的历史乱码。"""
    compact = "".join(str(content or "").split())
    if len(compact) < 8:
        return False
    replacement_count = compact.count("?") + compact.count("？") + compact.count("�")
    has_cjk = any("\u3400" <= char <= "\u9fff" for char in compact)
    return not has_cjk and replacement_count / len(compact) >= 0.35


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

    def review_queue(self, limit: int = 100) -> list[dict]:
        """E1 只读复核队列：低置信分类、异常错题与 RAG 失败/差评。"""
        with self.engine.connect() as conn:
            low_rows = conn.execute(text("""SELECT id,text_preview,subject_pred,type_pred,knowledge_pred,avg_confidence,created_at
                FROM classifications WHERE confidence_band='low' ORDER BY avg_confidence ASC,id DESC LIMIT :limit"""),
                {"limit": int(limit) * 2}).mappings().all()
            # 早期终端编码错误可能已把中文永久写成“????”。原文无法恢复，
            # 继续展示只会污染复核队列；保留底层审计记录，但不作为可处理任务。
            low = [row for row in low_rows if not is_unreadable_text(row["text_preview"])][:limit]
            anomalous = conn.execute(text("""SELECT q.id,q.content,q.subject,q.question_type,q.knowledge_point,COUNT(ar.id) attempts,
                AVG(ar.is_correct) correct_rate,MAX(ar.created_at) created_at
                FROM answer_records ar JOIN questions q ON q.id=ar.question_id WHERE ar.is_correct IS NOT NULL
                GROUP BY q.id,q.content,q.subject,q.question_type,q.knowledge_point
                HAVING COUNT(ar.id)>=5 AND AVG(ar.is_correct)<.4 ORDER BY correct_rate ASC,attempts DESC LIMIT :limit"""),
                {"limit": int(limit)}).mappings().all()
            rag = conn.execute(text("""SELECT DISTINCT t.id,t.query_text,t.failure_stage,t.created_at,
                MAX(CASE WHEN f.rating=-1 THEN 1 ELSE 0 END) negative_feedback
                FROM rag_query_traces t LEFT JOIN rag_sessions s ON s.id=t.session_id
                LEFT JOIN rag_messages m ON m.session_id=s.id AND m.role='assistant'
                LEFT JOIN rag_feedback f ON f.message_id=m.id
                WHERE t.failure_stage IS NOT NULL OR f.rating=-1
                GROUP BY t.id,t.query_text,t.failure_stage,t.created_at ORDER BY t.id DESC LIMIT :limit"""),
                {"limit": int(limit)}).mappings().all()
        items = [{"kind": "low_confidence", "key": f"classification:{row['id']}", "priority": 3,
                  "title": "低置信分类", "content": row["text_preview"], "classification_id": row["id"],
                  "prediction": {"subject": row["subject_pred"], "question_type": row["type_pred"], "knowledge_point": row["knowledge_pred"]},
                  "evidence": f"综合置信度 {round(float(row['avg_confidence']) * 100)}%", "created_at": row["created_at"]} for row in low]
        items += [{"kind": "question_anomaly", "key": f"question:{row['id']}", "priority": 2,
                   "title": "异常错题", "content": row["content"], "question_id": row["id"],
                   "prediction": {"subject": row["subject"], "question_type": row["question_type"], "knowledge_point": row["knowledge_point"]},
                   "evidence": f"{row['attempts']} 次已判分作答，正确率 {round(float(row['correct_rate']) * 100)}%", "created_at": row["created_at"]} for row in anomalous]
        items += [{"kind": "rag_quality", "key": f"rag:{row['id']}", "priority": 1,
                   "title": "RAG 证据或反馈异常", "content": row["query_text"], "rag_trace_id": row["id"],
                   "prediction": {}, "evidence": "低证据拒答" if row["failure_stage"] else "学生标记不准确", "created_at": row["created_at"]} for row in rag]
        return sorted(items, key=lambda item: (-item["priority"], item["created_at"]), reverse=False)[:limit]


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

    def existing_hashes(self, hashes: list[str]) -> set[str]:
        """分批查询已存在题干指纹，供批量导入幂等去重。"""
        found: set[str] = set()
        with self.engine.connect() as conn:
            for start in range(0, len(hashes), 500):
                chunk = hashes[start:start + 500]
                if not chunk:
                    continue
                binds = ",".join(f":h{i}" for i in range(len(chunk)))
                params = {f"h{i}": value for i, value in enumerate(chunk)}
                rows = conn.execute(text(
                    f"SELECT text_hash FROM questions WHERE text_hash IN ({binds})"
                ), params).all()
                found.update(str(row[0]) for row in rows)
        return found

    def bulk_insert(self, rows: list[dict], *, created_by: int,
                    source: str = "excel_import") -> tuple[list[int], int]:
        """在一个事务中批量写题；数据库已有指纹自动跳过。"""
        if not rows:
            return [], 0
        hashes = [text_hash(str(row["content"])) for row in rows]
        existing = self.existing_hashes(hashes)
        inserted_ids: list[int] = []
        skipped = 0
        statement = text("""
            INSERT INTO questions (
                content, subject, question_type, knowledge_point, source, options_json,
                answer, analysis, difficulty, grade_band, grade, knowledge_node_id,
                created_by, text_hash, status
            ) VALUES (
                :content, :subject, :question_type, :knowledge_point, :source, :options_json,
                :answer, :analysis, :difficulty, :grade_band, :grade, NULL,
                :created_by, :text_hash, :status
            )
        """)
        with self.engine.begin() as conn:
            for row, fingerprint in zip(rows, hashes, strict=True):
                if fingerprint in existing:
                    skipped += 1
                    continue
                result = conn.execute(statement, {
                    "content": row["content"], "subject": row["subject"],
                    "question_type": row["question_type"],
                    "knowledge_point": row.get("knowledge_point", ""), "source": source,
                    "options_json": json.dumps(row.get("options"), ensure_ascii=False)
                    if row.get("options") else None,
                    "answer": row.get("answer"), "analysis": row.get("analysis"),
                    "difficulty": row.get("difficulty"), "grade_band": row.get("grade_band"),
                    "grade": row.get("grade"), "created_by": created_by,
                    "text_hash": fingerprint, "status": row.get("status", "published"),
                })
                inserted_ids.append(int(result.lastrowid))
                existing.add(fingerprint)
        return inserted_ids, skipped

    def export_rows(self, *, subject: str = "", question_type: str = "", keyword: str = "",
                    grade_band: str = "", difficulty: int | None = None,
                    status: str = "", knowledge: str = "") -> list[dict]:
        """按题库页面筛选条件导出完整字段，不受分页上限影响。"""
        conditions, params = [], {}
        mapping = {
            "subject": (subject, "subject = :subject"),
            "question_type": (question_type, "question_type = :question_type"),
            "grade_band": (grade_band, "grade_band = :grade_band"),
            "status": (status, "status = :status"),
        }
        for key, (value, condition) in mapping.items():
            if value:
                conditions.append(condition)
                params[key] = value
        if difficulty is not None:
            conditions.append("difficulty = :difficulty")
            params["difficulty"] = int(difficulty)
        if knowledge:
            conditions.append("knowledge_point LIKE :knowledge")
            params["knowledge"] = f"%{knowledge}%"
        if keyword:
            conditions.append("(content LIKE :keyword OR knowledge_point LIKE :keyword)")
            params["keyword"] = f"%{keyword}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine.connect() as conn:
            records = conn.execute(text(f"""
                SELECT id, content, subject, question_type, knowledge_point, options_json,
                       answer, analysis, difficulty, grade_band, grade, status, source, created_at
                FROM questions {where} ORDER BY id ASC
            """), params).mappings().all()
        output = []
        for record in records:
            item = dict(record)
            item["options"] = json.loads(item.pop("options_json")) if item.get("options_json") else None
            output.append(item)
        return output

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

    def pending_question_ids_for_student(self, student_id: int) -> set[int]:
        """学生尚未提交的作业题目不得通过题库问答泄露答案。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT pq.question_id
                FROM users u
                JOIN assignments a ON a.class_id = u.class_id
                JOIN paper_questions pq ON pq.paper_id = a.paper_id
                LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = u.id
                WHERE u.id = :student AND u.role = 'student'
                  AND (s.id IS NULL OR s.status NOT IN ('submitted', 'checked'))
            """), {"student": int(student_id)}).mappings().all()
        return {int(row["question_id"]) for row in rows}

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


class LearningStore:
    """R3.1 错题本与自主练习；所有练习结果统一写入 answer_records。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def list_wrong(self, student_id: int, *, subject: str = "", knowledge_point: str = "",
                   include_resolved: bool = False) -> list[dict]:
        conditions = ["ar.student_id=:student", "ar.is_correct=0"]
        params: dict[str, object] = {"student": int(student_id)}
        if not include_resolved:
            conditions.append("wm.resolved_at IS NULL")
        if subject:
            conditions.append("q.subject=:subject")
            params["subject"] = subject
        if knowledge_point:
            conditions.append("q.knowledge_point=:knowledge")
            params["knowledge"] = knowledge_point
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT q.id AS question_id,q.content,q.subject,q.question_type,q.knowledge_point,q.options_json,
                       q.answer,q.analysis,q.difficulty,COUNT(ar.id) AS wrong_count,
                       MAX(ar.created_at) AS last_wrong_at,wm.resolved_at,wm.note
                FROM answer_records ar JOIN questions q ON q.id=ar.question_id
                LEFT JOIN wrong_book_marks wm ON wm.student_id=ar.student_id AND wm.question_id=ar.question_id
                WHERE {' AND '.join(conditions)}
                GROUP BY q.id,q.content,q.subject,q.question_type,q.knowledge_point,q.options_json,q.answer,q.analysis,
                         q.difficulty,wm.resolved_at,wm.note
                ORDER BY last_wrong_at DESC,wrong_count DESC
            """), params).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json")) if item.get("options_json") else []
            item["wrong_count"] = int(item["wrong_count"])
            item["resolved"] = item.get("resolved_at") is not None
            result.append(item)
        return result

    def mark_resolved(self, student_id: int, question_id: int, *, resolved: bool, note: str | None = None) -> bool:
        with self.engine.begin() as conn:
            exists = conn.execute(text("""SELECT 1 FROM answer_records
                WHERE student_id=:student AND question_id=:question AND is_correct=0 LIMIT 1"""),
                {"student": int(student_id), "question": int(question_id)}).first()
            if not exists:
                return False
            conn.execute(text("""INSERT INTO wrong_book_marks (student_id,question_id,resolved_at,note)
                VALUES (:student,:question,:resolved,:note)
                ON DUPLICATE KEY UPDATE resolved_at=VALUES(resolved_at),note=VALUES(note)"""), {
                    "student": int(student_id), "question": int(question_id),
                    "resolved": datetime.now() if resolved else None, "note": note,
                })
        return True

    def practice_questions(self, *, subject: str = "", knowledge_point: str = "", grade_band: str = "",
                           limit: int = 10) -> list[dict]:
        conditions, params = ["status='published'"], {"limit": int(limit)}
        for field, value in (("subject", subject), ("knowledge_point", knowledge_point), ("grade_band", grade_band)):
            if value:
                conditions.append(f"{field}=:{field}")
                params[field] = value
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""SELECT id,content,subject,question_type,knowledge_point,options_json,difficulty
                FROM questions WHERE {' AND '.join(conditions)} ORDER BY RAND() LIMIT :limit"""), params).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json")) if item.get("options_json") else []
            items.append(item)
        return items

    def questions_for_practice(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        binds = ", ".join(f":id_{index}" for index in range(len(ids)))
        params = {f"id_{index}": int(value) for index, value in enumerate(ids)}
        with self.engine.connect() as conn:
            rows=conn.execute(text(f"""SELECT id,content,subject,question_type,knowledge_point,answer,options_json,difficulty
                FROM questions WHERE status='published' AND id IN ({binds})"""),params).mappings().all()
        by_id = {int(row["id"]): dict(row) for row in rows}
        return [by_id[question_id] for question_id in ids if question_id in by_id]

    def review_question_ids(self, student_id: int, *, grade_band: str, limit: int) -> list[int]:
        """返回至少间隔七天的历史题目；只作为每日练习的复习候选。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT ar.question_id
                FROM answer_records ar JOIN questions q ON q.id=ar.question_id
                WHERE ar.student_id=:student AND q.status='published'
                  AND (:grade='' OR q.grade_band=:grade OR q.grade_band IS NULL OR q.grade_band='')
                  AND ar.created_at < DATE_SUB(NOW(), INTERVAL 7 DAY)
                GROUP BY ar.question_id ORDER BY MAX(ar.created_at) ASC LIMIT :limit"""),
                {"student": int(student_id), "grade": grade_band, "limit": int(limit)}).scalars().all()
        return [int(row) for row in rows]

    def class_learning_stats(self, class_id: int) -> dict:
        """班级级别聚合，不含跨班学生；仅返回教学分析所需字段。"""
        params = {"class_id": int(class_id)}
        with self.engine.connect() as conn:
            knowledge = conn.execute(text("""SELECT q.subject,q.knowledge_point,COUNT(ar.id) attempts,
                AVG(CASE WHEN ar.is_correct IS NOT NULL THEN ar.is_correct END) correct_rate
                FROM answer_records ar JOIN users u ON u.id=ar.student_id JOIN questions q ON q.id=ar.question_id
                WHERE u.class_id=:class_id AND ar.is_correct IS NOT NULL AND NULLIF(TRIM(q.knowledge_point),'') IS NOT NULL
                GROUP BY q.subject,q.knowledge_point"""), params).mappings().all()
            questions = conn.execute(text("""SELECT q.id,q.content,q.subject,q.knowledge_point,COUNT(ar.id) attempts,
                AVG(CASE WHEN ar.is_correct IS NOT NULL THEN ar.is_correct END) correct_rate
                FROM answer_records ar JOIN users u ON u.id=ar.student_id JOIN questions q ON q.id=ar.question_id
                WHERE u.class_id=:class_id AND ar.is_correct IS NOT NULL
                GROUP BY q.id,q.content,q.subject,q.knowledge_point HAVING COUNT(ar.id) > 0
                ORDER BY correct_rate ASC,attempts DESC LIMIT 10"""), params).mappings().all()
            completion = conn.execute(text("""SELECT COUNT(DISTINCT a.id) assignments, COUNT(DISTINCT u.id) students,
                COUNT(DISTINCT CASE WHEN s.status IN ('submitted','checked') THEN CONCAT(s.assignment_id,'-',s.student_id) END) submitted
                FROM assignments a LEFT JOIN users u ON u.class_id=a.class_id AND u.role='student'
                LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_id=u.id
                WHERE a.class_id=:class_id"""), params).mappings().first()
        data = dict(completion or {})
        expected = int(data.get("assignments") or 0) * int(data.get("students") or 0)
        data["expected"] = expected
        data["completion_rate"] = round(int(data.get("submitted") or 0) / expected, 4) if expected else None
        return {"knowledge": [dict(row) for row in knowledge], "questions": [dict(row) for row in questions],
                "completion": data}

    def save_consolidation_plan(self, student_id: int, plan_type: str, snapshot: dict,
                                question_ids: list[int]) -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""INSERT INTO consolidation_plan_runs
                (student_id,plan_type,input_snapshot_json,question_ids_json)
                VALUES (:student,:plan_type,:snapshot,:question_ids)"""),
                {"student": int(student_id), "plan_type": plan_type,
                 "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str),
                 "question_ids": json.dumps(question_ids, ensure_ascii=False)})
        return int(row.lastrowid)

    def save_practice(self, student_id: int, records: list[dict], *, source: str) -> None:
        with self.engine.begin() as conn:
            for item in records:
                conn.execute(text("""INSERT INTO answer_records
                    (submission_id,student_id,question_id,answer,is_correct,source,created_at)
                    VALUES (NULL,:student,:question,:answer,:correct,:source,:created)"""), {
                        "student":int(student_id),"question":int(item["question_id"]),"answer":item.get("answer"),
                        "correct":None if item.get("is_correct") is None else int(bool(item["is_correct"])),
                        "source":source,"created":datetime.now(),
                    })

    def mastery_records(self, student_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT q.subject,q.knowledge_point,ar.is_correct,ar.source,ar.created_at
                FROM answer_records ar JOIN questions q ON q.id=ar.question_id
                WHERE ar.student_id=:student AND NULLIF(TRIM(q.knowledge_point),'') IS NOT NULL
                ORDER BY ar.created_at,ar.id"""), {"student": int(student_id)}).mappings().all()
        return [dict(row) for row in rows]

    def save_mastery(self, student_id: int, profiles: list[dict]) -> None:
        with self.engine.begin() as conn:
            for item in profiles:
                conn.execute(text("""INSERT INTO student_knowledge_mastery
                    (student_id,subject,knowledge_point,attempt_count,correct_rate,recent_correct_rate,mastery_score,
                     profile_confidence,consecutive_wrong,redo_success_rate,last_practiced_at,recommended_difficulty)
                    VALUES (:student,:subject,:knowledge,:attempts,:correct,:recent,:score,:confidence,:streak,:redo,:last,:difficulty)
                    ON DUPLICATE KEY UPDATE attempt_count=VALUES(attempt_count),correct_rate=VALUES(correct_rate),
                    recent_correct_rate=VALUES(recent_correct_rate),mastery_score=VALUES(mastery_score),
                    profile_confidence=VALUES(profile_confidence),consecutive_wrong=VALUES(consecutive_wrong),
                    redo_success_rate=VALUES(redo_success_rate),last_practiced_at=VALUES(last_practiced_at),recommended_difficulty=VALUES(recommended_difficulty)"""),
                    {"student":int(student_id),"subject":item["subject"],"knowledge":item["knowledge_point"],
                     "attempts":item["attempt_count"],"correct":item["correct_rate"],"recent":item["recent_correct_rate"],
                     "score":item["mastery_score"],"confidence":item["profile_confidence"],"streak":item["consecutive_wrong"],
                     "redo":item["redo_success_rate"],"last":item["last_practiced_at"],"difficulty":item["recommended_difficulty"]})
            conn.execute(text("INSERT INTO student_profile_snapshots (student_id,snapshot_json) VALUES (:student,:snapshot)"),
                         {"student":int(student_id),"snapshot":json.dumps(profiles,ensure_ascii=False)})

    def recommendation_data(self, student_id: int, *, grade_band: str = "") -> tuple[list[dict], list[dict], set[int]]:
        with self.engine.connect() as conn:
            profiles = conn.execute(text("SELECT * FROM student_knowledge_mastery WHERE student_id=:student ORDER BY mastery_score LIMIT 5"), {"student":int(student_id)}).mappings().all()
            question_sql = "SELECT id,content,subject,question_type,knowledge_point,difficulty FROM questions WHERE status='published'"
            params: dict[str, object] = {}
            if grade_band:
                question_sql += " AND (grade_band=:grade_band OR grade_band IS NULL OR grade_band='')"
                params["grade_band"] = grade_band
            questions = conn.execute(text(question_sql), params).mappings().all()
            seen = conn.execute(text("SELECT DISTINCT question_id FROM answer_records WHERE student_id=:student AND created_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)"), {"student":int(student_id)}).scalars().all()
        return [dict(row) for row in profiles], [dict(row) for row in questions], {int(item) for item in seen}

    def save_recommendation_run(self, student_id: int, profiles: list[dict], candidates: list[dict], selected: list[dict]) -> int:
        with self.engine.begin() as conn:
            row=conn.execute(text("""INSERT INTO recommendation_runs (student_id,strategy,input_snapshot_json,candidates_json,selected_json)
                VALUES (:student,'weakness_rank_v1',:profiles,:candidates,:selected)"""), {"student":int(student_id),"profiles":json.dumps(profiles,ensure_ascii=False,default=str),"candidates":json.dumps(candidates,ensure_ascii=False,default=str),"selected":json.dumps(selected,ensure_ascii=False,default=str)})
            return int(row.lastrowid)

    def recommendation_metrics(self, student_id: int) -> dict:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT selected_json FROM recommendation_runs
                WHERE student_id=:student ORDER BY id DESC LIMIT 20"""), {"student": int(student_id)}).scalars().all()
        items = [item for row in rows for item in (json.loads(row) if isinstance(row, str) else row or [])]
        if not items:
            return {"runs": 0, "items": 0, "knowledge_match_rate": None, "within_run_repeat_rate": None}
        exact = sum(item.get("knowledge_point") == item.get("profile") for item in items)
        repeats = 0
        for row in rows:
            selected = json.loads(row) if isinstance(row, str) else row or []
            ids = [item.get("id") for item in selected]
            repeats += len(ids) - len(set(ids))
        return {"runs": len(rows), "items": len(items), "knowledge_match_rate": round(exact / len(items), 4),
                "within_run_repeat_rate": round(repeats / len(items), 4)}


class RagKnowledgeBaseStore:
    """R1 知识库事实源：版本、资料、分块与入库任务。

    向量库不在此处作为事实源；所有激活与回滚先以 MySQL 状态为准。
    """

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    @staticmethod
    def _serialize(row) -> dict | None:
        if not row:
            return None
        data = dict(row)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.strftime("%Y-%m-%d %H:%M:%S")
            elif key.endswith("_json"):
                data[key[:-5]] = json.loads(value) if isinstance(value, str) and value else value
                data.pop(key)
        return data

    def create_version(self, version: str, *, created_by: int,
                       description: str = "") -> int:
        version = str(version or "").strip()
        if not version or len(version) > 64:
            raise ValueError("知识库版本号不能为空且不能超过 64 个字符")
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO rag_kb_versions (version, description, created_by)
                VALUES (:version, :description, :created_by)
            """), {"version": version, "description": description.strip() or None,
                   "created_by": int(created_by)})
            return int(row.lastrowid)

    def get_version(self, version_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM rag_kb_versions WHERE id = :id"),
                               {"id": int(version_id)}).mappings().first()
        return self._serialize(row)

    def get_version_by_name(self, version: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM rag_kb_versions WHERE version = :version"),
                               {"version": version}).mappings().first()
        return self._serialize(row)

    def list_versions(self, *, created_by: int | None = None) -> list[dict]:
        clause, params = "", {}
        if created_by is not None:
            clause, params = "WHERE created_by = :created_by", {"created_by": int(created_by)}
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT * FROM rag_kb_versions {clause} ORDER BY id DESC"), params).mappings().all()
        return [self._serialize(row) for row in rows]

    def get_owned_version(self, version_id: int, created_by: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM rag_kb_versions WHERE id = :id AND created_by = :created_by
            """), {"id": int(version_id), "created_by": int(created_by)}).mappings().first()
        return self._serialize(row)

    def delete_version(self, version_id: int, *, created_by: int) -> dict:
        """永久删除非激活知识库版本及其资料关系数据。

        ACTIVE 版本和当前指针指向的版本始终拒绝删除。审计记录及历史问答
        仍保留版本名称，不随版本删除。
        """
        with self.engine.begin() as conn:
            version = conn.execute(text("""
                SELECT id, status FROM rag_kb_versions
                WHERE id = :id AND created_by = :created_by FOR UPDATE
            """), {"id": int(version_id), "created_by": int(created_by)}).mappings().first()
            if not version:
                raise ValueError("知识库版本不存在或无权删除")
            pointed = conn.execute(text("""
                SELECT COUNT(*) FROM rag_active_kb_pointer
                WHERE active_version_id = :id
            """), {"id": int(version_id)}).scalar_one()
            if version["status"] == "ACTIVE" or pointed:
                raise ValueError("当前激活的知识库版本不能删除，请先激活其他版本")

            documents = conn.execute(text("""
                SELECT id, storage_key FROM rag_documents WHERE kb_version_id = :id
            """), {"id": int(version_id)}).mappings().all()
            chunk_ids = [int(row["id"]) for row in conn.execute(text("""
                SELECT id FROM rag_document_chunks WHERE kb_version_id = :id
            """), {"id": int(version_id)}).mappings().all()]
            storage_keys = {row["storage_key"] for row in documents if row["storage_key"]}
            conn.execute(text("DELETE FROM rag_ingestion_jobs WHERE kb_version_id = :id"), {"id": int(version_id)})
            conn.execute(text("DELETE FROM rag_document_chunks WHERE kb_version_id = :id"), {"id": int(version_id)})
            conn.execute(text("DELETE FROM rag_documents WHERE kb_version_id = :id"), {"id": int(version_id)})
            conn.execute(text("DELETE FROM rag_kb_versions WHERE id = :id"), {"id": int(version_id)})
            deletable_storage = [key for key in storage_keys if not conn.execute(text("""
                SELECT COUNT(*) FROM rag_documents WHERE storage_key = :key
            """), {"key": key}).scalar_one()]
        return {"chunk_ids": chunk_ids, "storage_keys": deletable_storage,
                "document_count": len(documents)}

    def set_quality_report(self, version_id: int, report: dict) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE rag_kb_versions SET quality_report_json = :report
                WHERE id = :id AND status = 'STAGED'
            """), {"id": int(version_id), "report": json.dumps(report, ensure_ascii=False)})
            return row.rowcount > 0

    def activate_version(self, version_id: int) -> dict:
        """原子切换 ACTIVE 指针；仅质量通过的 STAGED/ARCHIVED 版本可激活。"""
        with self.engine.begin() as conn:
            target = conn.execute(text("""
                SELECT id, status, quality_report_json FROM rag_kb_versions
                WHERE id = :id FOR UPDATE
            """), {"id": int(version_id)}).mappings().first()
            if not target:
                raise ValueError("知识库版本不存在")
            if target["status"] not in ("STAGED", "ARCHIVED"):
                raise ValueError("仅 STAGED 或 ARCHIVED 版本可激活")
            report = json.loads(target["quality_report_json"] or "{}")
            if report.get("passed") is not True:
                raise ValueError("知识库版本未通过入库质量检查，不能激活")
            conn.execute(text("""
                UPDATE rag_kb_versions
                SET status = 'ARCHIVED', archived_at = NOW()
                WHERE status = 'ACTIVE'
            """))
            conn.execute(text("""
                UPDATE rag_kb_versions
                SET status = 'ACTIVE', activated_at = NOW(), archived_at = NULL
                WHERE id = :id
            """), {"id": int(version_id)})
            conn.execute(text("""
                INSERT INTO rag_active_kb_pointer (id, active_version_id)
                VALUES (1, :id)
                ON DUPLICATE KEY UPDATE active_version_id = VALUES(active_version_id)
            """), {"id": int(version_id)})
        return self.get_version(version_id) or {}

    def get_active_version(self) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT v.* FROM rag_active_kb_pointer p
                JOIN rag_kb_versions v ON v.id = p.active_version_id
                WHERE p.id = 1 AND v.status = 'ACTIVE'
            """)).mappings().first()
        return self._serialize(row)

    def create_document(self, *, kb_version_id: int, created_by: int,
                        source_name: str, source_type: str, content_hash: str,
                        storage_key: str | None = None, mime_type: str | None = None,
                        file_size: int | None = None, subject: str | None = None,
                        grade_band: str | None = None, grade: str | None = None,
                        knowledge_node_id: int | None = None,
                        allowed_roles: list[str] | None = None) -> int:
        if not source_name.strip() or len(content_hash) != 64:
            raise ValueError("资料名称不能为空，content_hash 必须为 SHA-256")
        roles = allowed_roles or ["student", "teacher", "admin"]
        with self.engine.begin() as conn:
            version = conn.execute(text("""
                SELECT id FROM rag_kb_versions WHERE id = :id FOR UPDATE
            """), {"id": int(kb_version_id)}).mappings().first()
            if not version:
                raise ValueError("知识库版本不存在")
            row = conn.execute(text("""
                INSERT INTO rag_documents
                    (kb_version_id, created_by, source_name, source_type, storage_key,
                     mime_type, file_size, content_hash, subject, grade_band, grade,
                     knowledge_node_id, allowed_roles_json)
                VALUES (:version, :created_by, :source_name, :source_type, :storage_key,
                        :mime_type, :file_size, :content_hash, :subject, :grade_band, :grade,
                        :knowledge_node_id, :allowed_roles)
            """), {
                "version": int(kb_version_id), "created_by": int(created_by),
                "source_name": source_name.strip(), "source_type": source_type.strip().lower(),
                "storage_key": storage_key, "mime_type": mime_type, "file_size": file_size,
                "content_hash": content_hash, "subject": subject, "grade_band": grade_band,
                "grade": grade, "knowledge_node_id": knowledge_node_id,
                "allowed_roles": json.dumps(roles, ensure_ascii=False),
            })
            return int(row.lastrowid)

    def find_reusable_document(self, *, created_by: int, content_hash: str) -> dict | None:
        """同一教师的相同文件可复用；不同教师不共享资料记录。"""
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM rag_documents
                WHERE created_by = :created_by AND content_hash = :content_hash
                  AND status IN ('PROCESSED', 'PUBLISHED')
                ORDER BY id DESC LIMIT 1
            """), {"created_by": int(created_by), "content_hash": content_hash}).mappings().first()
        return self._serialize(row)

    def get_document(self, document_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM rag_documents WHERE id = :id"),
                               {"id": int(document_id)}).mappings().first()
        return self._serialize(row)

    def list_documents(self, *, created_by: int, kb_version_id: int | None = None) -> list[dict]:
        clause, params = "WHERE created_by = :created_by", {"created_by": int(created_by)}
        if kb_version_id is not None:
            clause += " AND kb_version_id = :kb_version_id"
            params["kb_version_id"] = int(kb_version_id)
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT * FROM rag_documents {clause} ORDER BY id DESC"), params).mappings().all()
        return [self._serialize(row) for row in rows]

    def get_owned_document(self, document_id: int, created_by: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM rag_documents WHERE id = :id AND created_by = :created_by
            """), {"id": int(document_id), "created_by": int(created_by)}).mappings().first()
        return self._serialize(row)

    def update_document_status(self, document_id: int, *, status: str,
                               failure_reason: str | None = None) -> bool:
        statuses = {"STAGED", "PROCESSING", "PROCESSED", "PUBLISHED", "UNPUBLISHED", "FAILED", "ARCHIVED"}
        if status not in statuses:
            raise ValueError("非法资料状态")
        fields = ["status = :status", "failure_reason = :failure_reason"]
        if status == "PUBLISHED":
            fields.append("published_at = NOW()")
        if status == "ARCHIVED":
            fields.append("archived_at = NOW()")
        with self.engine.begin() as conn:
            row = conn.execute(text(
                f"UPDATE rag_documents SET {', '.join(fields)} WHERE id = :id"),
                {"id": int(document_id), "status": status, "failure_reason": failure_reason})
            return row.rowcount > 0

    def _ocr_review_state(self, conn, document: dict) -> dict:
        """Bind human review to the latest job and exact stored chunks, not a UI flag."""
        job = conn.execute(text("""
            SELECT id, status, metrics_json FROM rag_ingestion_jobs
            WHERE document_id = :id ORDER BY id DESC LIMIT 1 FOR UPDATE
        """), {"id": int(document["id"])}).mappings().first()
        if not job:
            return {"status": "BLOCKED" if document["source_type"] == "pdf" else "NOT_REQUIRED"}
        try:
            metrics = json.loads(job["metrics_json"] or "{}")
            if not isinstance(metrics, dict):
                raise ValueError("invalid metrics")
        except (ValueError, TypeError):
            return {"status": "BLOCKED"}
        if job["status"] != "SUCCEEDED":
            return {"status": "BLOCKED"}
        if "ocr_pages" in metrics and (
            not isinstance(metrics["ocr_pages"], list)
            or any(type(page) is not int or page < 1 for page in metrics["ocr_pages"])
        ):
            return {"status": "BLOCKED"}
        # Old PDF jobs lacking OCR provenance require conservative manual review.
        if not metrics.get("requires_review") and not metrics.get("ocr_pages") and not (
            document["source_type"] == "pdf" and "ocr_pages" not in metrics
        ):
            return {"status": "NOT_REQUIRED"}
        chunks = conn.execute(text("""
            SELECT id, content, chunk_kind, order_no, parent_chunk_id
            FROM rag_document_chunks WHERE document_id = :id ORDER BY id
        """), {"id": int(document["id"])}).mappings().all()
        snapshot = [document["content_hash"], int(job["id"]), [dict(row) for row in chunks]]
        token = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        review = metrics.get("ocr_review") or {}
        valid = isinstance(review, dict) and review.get("token") == token
        return {"status": review.get("status", "PENDING") if valid else "PENDING",
                "token": token, "job_id": int(job["id"]), "ocr_pages": metrics.get("ocr_pages", []),
                "parent_texts": [row["content"] for row in chunks if row["chunk_kind"] == "parent"],
                "correction_provenance": metrics.get("correction_provenance"),
                "automatic_corrections": metrics.get("ocr_corrections", []),
                "review": review if valid else None}

    def get_ocr_review(self, document_id: int, *, created_by: int) -> dict:
        with self.engine.begin() as conn:
            doc = conn.execute(text("""
                SELECT * FROM rag_documents WHERE id = :id AND created_by = :owner FOR UPDATE
            """), {"id": int(document_id), "owner": int(created_by)}).mappings().first()
            if not doc:
                raise ValueError("资料不存在")
            return self._ocr_review_state(conn, doc)

    def review_ocr_document(self, document_id: int, *, created_by: int,
                            token: str, approved: bool, note: str) -> dict:
        if type(approved) is not bool or not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise ValueError("请填写复核结论及说明（1–2000 字）")
        with self.engine.begin() as conn:
            doc = conn.execute(text("""
                SELECT * FROM rag_documents WHERE id = :id AND created_by = :owner FOR UPDATE
            """), {"id": int(document_id), "owner": int(created_by)}).mappings().first()
            if not doc:
                raise ValueError("资料不存在")
            if doc["status"] not in ("PROCESSED", "UNPUBLISHED"):
                raise ValueError("仅处理完成或已下架资料可复核")
            state = self._ocr_review_state(conn, doc)
            if state["status"] in ("BLOCKED", "NOT_REQUIRED") or token != state.get("token"):
                raise ValueError("资料未就绪或复核内容已变化，请重新预览")
            review = {"status": "APPROVED" if approved else "REJECTED", "token": token,
                      "reviewed_by": int(created_by), "reviewed_at": datetime.now().isoformat(),
                      "note": note.strip()}
            conn.execute(text("""
                UPDATE rag_ingestion_jobs SET metrics_json = JSON_SET(
                    COALESCE(metrics_json, JSON_OBJECT()), '$.ocr_review', CAST(:review AS JSON))
                WHERE id = :id
            """), {"id": state["job_id"], "review": json.dumps(review, ensure_ascii=False)})
            return {**state, "status": review["status"], "review": review}

    def set_document_publication(self, document_id: int, *, created_by: int,
                                 published: bool) -> dict:
        """资料与切片发布状态同步；教师仅能操作自己的已处理资料。"""
        with self.engine.begin() as conn:
            document = conn.execute(text("""
                SELECT id, kb_version_id, status, source_type, content_hash FROM rag_documents
                WHERE id = :id AND created_by = :created_by FOR UPDATE
            """), {"id": int(document_id), "created_by": int(created_by)}).mappings().first()
            if not document:
                raise ValueError("资料不存在")
            if published:
                if document["status"] not in ("PROCESSED", "UNPUBLISHED"):
                    raise ValueError("仅处理成功或已下架资料可发布")
                review = self._ocr_review_state(conn, document)
                if review["status"] not in ("NOT_REQUIRED", "APPROVED"):
                    raise ValueError("OCR 资料尚未通过人工复核，或入库任务未完成；请先预览复核")
                new_status, chunk_status = "PUBLISHED", "PUBLISHED"
                conn.execute(text("""
                    UPDATE rag_documents SET status = :status, failure_reason = NULL,
                        published_at = NOW() WHERE id = :id
                """), {"id": int(document_id), "status": new_status})
            else:
                if document["status"] != "PUBLISHED":
                    raise ValueError("仅已发布资料可下架")
                new_status, chunk_status = "UNPUBLISHED", "STAGED"
                conn.execute(text("UPDATE rag_documents SET status = :status WHERE id = :id"),
                             {"id": int(document_id), "status": new_status})
            conn.execute(text("""
                UPDATE rag_document_chunks SET status = :chunk_status
                WHERE document_id = :document_id AND kb_version_id = :kb_version_id
            """), {"document_id": int(document_id), "kb_version_id": int(document["kb_version_id"]),
                   "chunk_status": chunk_status})
        return self.get_document(document_id) or {}

    def archive_document(self, document_id: int, *, created_by: int) -> dict:
        """逻辑删除资料：退出检索范围但保留版本、审计与回滚证据。"""
        with self.engine.begin() as conn:
            document = conn.execute(text("""
                SELECT id, kb_version_id, status FROM rag_documents
                WHERE id = :id AND created_by = :created_by FOR UPDATE
            """), {"id": int(document_id), "created_by": int(created_by)}).mappings().first()
            if not document:
                raise ValueError("资料不存在")
            if document["status"] == "ARCHIVED":
                raise ValueError("资料已归档")
            conn.execute(text("""
                UPDATE rag_documents SET status = 'ARCHIVED', archived_at = NOW()
                WHERE id = :id
            """), {"id": int(document_id)})
            conn.execute(text("""
                UPDATE rag_document_chunks SET status = 'ARCHIVED', valid_to = NOW()
                WHERE document_id = :document_id AND kb_version_id = :kb_version_id
            """), {"document_id": int(document_id), "kb_version_id": int(document["kb_version_id"])})
        return self.get_document(document_id) or {}

    def delete_document(self, document_id: int, *, created_by: int) -> dict:
        """永久删除教师自己的资料记录及其关系数据。

        返回待清理的向量 ID 和本地源文件是否仍被其他资料记录引用；向量和文件的
        实际清理由应用层执行，避免 Store 层依赖外部服务或文件系统。
        """
        with self.engine.begin() as conn:
            document = conn.execute(text("""
                SELECT id, storage_key FROM rag_documents
                WHERE id = :id AND created_by = :created_by FOR UPDATE
            """), {"id": int(document_id), "created_by": int(created_by)}).mappings().first()
            if not document:
                raise ValueError("资料不存在或无权删除")
            chunk_ids = [int(row["id"]) for row in conn.execute(text("""
                SELECT id FROM rag_document_chunks WHERE document_id = :document_id
            """), {"document_id": int(document_id)}).mappings().all()]
            conn.execute(text("DELETE FROM rag_ingestion_jobs WHERE document_id = :document_id"),
                         {"document_id": int(document_id)})
            conn.execute(text("DELETE FROM rag_document_chunks WHERE document_id = :document_id"),
                         {"document_id": int(document_id)})
            conn.execute(text("DELETE FROM rag_documents WHERE id = :id"), {"id": int(document_id)})
            storage_key = document["storage_key"]
            has_other_reference = bool(storage_key and conn.execute(text("""
                SELECT COUNT(*) FROM rag_documents WHERE storage_key = :storage_key
            """), {"storage_key": storage_key}).scalar_one())
        return {"chunk_ids": chunk_ids, "storage_key": storage_key,
                "delete_storage": bool(storage_key) and not has_other_reference}

    def build_quality_report(self, version_id: int, *, created_by: int) -> dict:
        """为激活提供最小质量门槛：至少一份已处理/发布资料、无失败资料、子块完整。"""
        if not self.get_owned_version(version_id, created_by):
            raise ValueError("知识库版本不存在")
        with self.engine.connect() as conn:
            counts = conn.execute(text("""
                SELECT status, COUNT(*) AS total FROM rag_documents
                WHERE kb_version_id = :version_id AND created_by = :created_by
                GROUP BY status
            """), {"version_id": int(version_id), "created_by": int(created_by)}).mappings().all()
            child_count = conn.execute(text("""
                SELECT COUNT(*) AS total FROM rag_document_chunks c
                JOIN rag_documents d ON d.id = c.document_id
                WHERE c.kb_version_id = :version_id AND c.chunk_kind = 'child'
                  AND d.created_by = :created_by
            """), {"version_id": int(version_id), "created_by": int(created_by)}).scalar_one()
        by_status = {row["status"]: int(row["total"]) for row in counts}
        usable = by_status.get("PROCESSED", 0) + by_status.get("PUBLISHED", 0) + by_status.get("UNPUBLISHED", 0)
        report = {"document_count": sum(by_status.values()), "status_counts": by_status,
                  "child_chunk_count": int(child_count), "passed": usable > 0 and int(child_count) > 0
                  and by_status.get("FAILED", 0) == 0}
        if not report["passed"]:
            report["reason"] = "至少需要一份处理成功且存在子块的资料，并处理所有失败资料"
        return report

    def sync_published_questions(self) -> dict:
        """将已发布题目镜像为本地知识源；全程仅 MySQL 读写，不调用任何模型。"""
        with self.engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT id, content, options_json, answer, analysis, subject, question_type,
                       knowledge_point, grade_band, grade
                FROM questions WHERE status = 'published' ORDER BY id
            """)).mappings().all()
            for row in rows:
                payload = {
                    "question_text": row["content"], "options_json": row["options_json"],
                    "answer": row["answer"], "analysis": row["analysis"], "subject": row["subject"],
                    "question_type": row["question_type"], "knowledge_point": row["knowledge_point"],
                    "grade_band": row["grade_band"], "grade": row["grade"],
                }
                source_hash = text_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
                conn.execute(text("""
                    INSERT INTO rag_question_knowledge
                        (question_id, question_text, options_json, answer, analysis, subject, question_type,
                         knowledge_point, grade_band, grade, source_hash)
                    VALUES (:question_id, :question_text, :options_json, :answer, :analysis, :subject,
                            :question_type, :knowledge_point, :grade_band, :grade, :source_hash)
                    ON DUPLICATE KEY UPDATE question_text = VALUES(question_text),
                        options_json = VALUES(options_json), answer = VALUES(answer), analysis = VALUES(analysis),
                        subject = VALUES(subject), question_type = VALUES(question_type),
                        knowledge_point = VALUES(knowledge_point), grade_band = VALUES(grade_band),
                        grade = VALUES(grade), source_hash = VALUES(source_hash)
                """), {"question_id": int(row["id"]), **payload, "source_hash": source_hash})
            removed = conn.execute(text("""
                DELETE k FROM rag_question_knowledge k
                LEFT JOIN questions q ON q.id = k.question_id
                WHERE q.id IS NULL OR q.status <> 'published'
            """)).rowcount
        return {"synced": len(rows), "removed": int(removed or 0)}

    def question_knowledge_status(self) -> dict:
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM rag_question_knowledge")).scalar_one()
            latest = conn.execute(text("SELECT MAX(synced_at) FROM rag_question_knowledge")).scalar_one()
        return {"total": int(total), "latest_synced_at": latest.strftime("%Y-%m-%d %H:%M:%S") if latest else None}

    def list_local_question_knowledge(self, *, subject: str | None = None,
                                      grade_band: str | None = None, grade: str | None = None) -> list[dict]:
        conditions, params = ["1 = 1"], {}
        if subject:
            conditions.append("subject = :subject")
            params["subject"] = subject
        if grade_band:
            conditions.append("(grade_band = :grade_band OR grade_band IS NULL OR grade_band = '')")
            params["grade_band"] = grade_band
        if grade:
            conditions.append("(grade = :grade OR grade IS NULL OR grade = '')")
            params["grade"] = grade
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT question_id, question_text, options_json, answer, analysis, subject, question_type,
                       knowledge_point, grade_band, grade
                FROM rag_question_knowledge WHERE {' AND '.join(conditions)}
                ORDER BY question_id DESC LIMIT 2000
            """), params).mappings().all()
        return [self._serialize(row) or {} for row in rows]

    def list_document_chunks(self, document_id: int, *, chunk_kind: str | None = None) -> list[dict]:
        clause, params = "WHERE document_id = :document_id", {"document_id": int(document_id)}
        if chunk_kind is not None:
            clause += " AND chunk_kind = :chunk_kind"
            params["chunk_kind"] = chunk_kind
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT * FROM rag_document_chunks {clause} ORDER BY chunk_kind, order_no"), params).mappings().all()
        return [self._serialize(row) for row in rows]

    def get_retrieval_chunks(self, chunk_ids: list[int] | None, *, kb_version_id: int,
                             role: str, subject: str | None = None,
                             grade_band: str | None = None, grade: str | None = None,
                             knowledge_node_id: int | None = None,
                             corpus_limit: int = 5000) -> list[dict]:
        """MySQL 侧二次硬过滤，向量库命中绝不能直接作为可见内容。"""
        ids = [int(chunk_id) for chunk_id in dict.fromkeys(chunk_ids or [])]
        if chunk_ids is not None and not ids:
            return []
        if corpus_limit < 1:
            raise ValueError("语料上限必须为正数")
        placeholders = ", ".join(f":id_{index}" for index in range(len(ids)))
        conditions = ["c.kb_version_id = :kb_version_id",
                      "c.chunk_kind = 'child'", "c.status = 'PUBLISHED'", "d.status = 'PUBLISHED'",
                      "JSON_CONTAINS(d.allowed_roles_json, JSON_QUOTE(:role))"]
        if chunk_ids is not None:
            conditions.append(f"c.id IN ({placeholders})")
        params: dict[str, object] = {f"id_{index}": chunk_id for index, chunk_id in enumerate(ids)}
        params.update({"kb_version_id": int(kb_version_id), "role": role})
        suffix = ""
        if chunk_ids is None:
            suffix = "ORDER BY c.id LIMIT :corpus_limit"
            params["corpus_limit"] = corpus_limit + 1
        for field, value in (("subject", subject), ("grade_band", grade_band), ("grade", grade),
                             ("knowledge_node_id", knowledge_node_id)):
            if value is not None:
                conditions.append(f"d.{field} = :{field}")
                params[field] = value
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT c.*, d.source_name, d.subject, d.grade_band, d.grade, d.knowledge_node_id,
                       d.allowed_roles_json, v.version AS kb_version
                FROM rag_document_chunks c
                JOIN rag_documents d ON d.id = c.document_id
                JOIN rag_kb_versions v ON v.id = c.kb_version_id
                WHERE {' AND '.join(conditions)} {suffix}
            """), params).mappings().all()
        if chunk_ids is None and len(rows) > corpus_limit:
            raise ValueError("授权语料超出本地 BM25 容量上限，请使用独立关键词索引")
        return [self._serialize(row) for row in rows]

    def create_session(self, user_id: int, role: str, title: str | None = None) -> int:
        with self.engine.begin() as conn:
            return int(conn.execute(text("INSERT INTO rag_sessions (user_id, role, title) VALUES (:u,:r,:t)"),
                {"u": int(user_id), "r": role, "t": title}).lastrowid)

    def list_sessions(self, user_id: int, role: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM rag_sessions WHERE user_id=:u AND role=:r ORDER BY updated_at DESC"),
                {"u": int(user_id), "r": role}).mappings().all()
        return [self._serialize(row) for row in rows]

    def add_message(self, session_id: int, role: str, content: str, *, citations: list | None = None,
                    refused: bool = False) -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""INSERT INTO rag_messages (session_id,role,content,citations_json,refused)
                VALUES (:s,:r,:c,:j,:x)"""), {"s":int(session_id),"r":role,"c":content,
                "j":json.dumps(citations, ensure_ascii=False) if citations else None,"x":int(refused)})
            conn.execute(text("UPDATE rag_sessions SET updated_at=NOW() WHERE id=:s"), {"s":int(session_id)})
            return int(row.lastrowid)

    def get_owned_session(self, session_id: int, user_id: int, role: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT * FROM rag_sessions
                WHERE id=:s AND user_id=:u AND role=:r"""),
                {"s": int(session_id), "u": int(user_id), "r": role}).mappings().first()
        return self._serialize(row) if row else None

    def messages(self, session_id: int, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT m.* FROM rag_messages m JOIN rag_sessions s ON s.id=m.session_id
                WHERE m.session_id=:s AND s.user_id=:u ORDER BY m.id"""), {"s":int(session_id),"u":int(user_id)}).mappings().all()
        return [self._serialize(row) for row in rows]

    def recent_student_messages(self, session_id: int, user_id: int) -> list[dict]:
        """仅取本人学生会话末尾 8 条，正文限长；供追问主题补全使用。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT m.id,m.role,LEFT(m.content,400) AS content,m.refused
                FROM rag_messages m JOIN rag_sessions s ON s.id=m.session_id
                WHERE m.session_id=:s AND s.user_id=:u AND s.role='student'
                ORDER BY m.id DESC LIMIT 8"""),
                {"s": int(session_id), "u": int(user_id)}).mappings().all()
        return [dict(row) for row in reversed(rows)]

    def owns_message(self, message_id: int, user_id: int) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT m.id FROM rag_messages m
                JOIN rag_sessions s ON s.id=m.session_id
                WHERE m.id=:m AND s.user_id=:u AND m.role='assistant'"""),
                {"m": int(message_id), "u": int(user_id)}).first()
        return row is not None

    def clear_session(self, session_id: int, user_id: int) -> bool:
        with self.engine.begin() as conn:
            owned = conn.execute(text("SELECT id FROM rag_sessions WHERE id=:s AND user_id=:u FOR UPDATE"), {"s":int(session_id),"u":int(user_id)}).first()
            if not owned:
                return False
            conn.execute(text("DELETE FROM rag_messages WHERE session_id=:s"), {"s":int(session_id)})
            return True

    def delete_session(self, session_id: int, user_id: int, role: str) -> bool:
        """删除用户自己的完整会话，以及其消息、反馈和检索轨迹。"""
        params = {"s": int(session_id), "u": int(user_id), "r": role}
        with self.engine.begin() as conn:
            owned = conn.execute(text("""SELECT id FROM rag_sessions
                WHERE id=:s AND user_id=:u AND role=:r FOR UPDATE"""), params).first()
            if not owned:
                return False
            conn.execute(text("""DELETE f FROM rag_feedback f
                JOIN rag_messages m ON m.id=f.message_id WHERE m.session_id=:s"""), params)
            conn.execute(text("DELETE FROM rag_query_traces WHERE session_id=:s"), params)
            conn.execute(text("DELETE FROM rag_messages WHERE session_id=:s"), params)
            conn.execute(text("DELETE FROM rag_sessions WHERE id=:s"), params)
            return True

    def feedback(self, message_id: int, user_id: int, rating: int, correction: str | None = None) -> None:
        if rating not in (-1, 1):
            raise ValueError("rating 仅支持 1 或 -1")
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO rag_feedback (message_id,user_id,rating,correction) VALUES (:m,:u,:r,:c)
                ON DUPLICATE KEY UPDATE rating=VALUES(rating), correction=VALUES(correction)"""),
                {"m":int(message_id),"u":int(user_id),"r":rating,"c":correction})

    def add_query_trace(self, *, session_id: int | None, user_id: int, kb_version: str | None,
                        query_text: str, candidates: list[dict] | None, latency_ms: int,
                        failure_stage: str | None = None) -> int:
        """记录可审计的检索摘要；不写入用户画像或模型提示词。"""
        with self.engine.begin() as conn:
            row = conn.execute(text("""INSERT INTO rag_query_traces
                (session_id,user_id,kb_version,query_text,candidates_json,latency_ms,failure_stage)
                VALUES (:s,:u,:v,:q,:c,:l,:f)"""), {
                    "s": session_id, "u": int(user_id), "v": kb_version, "q": query_text,
                    "c": json.dumps(candidates or [], ensure_ascii=False), "l": int(latency_ms),
                    "f": failure_stage,
                })
            return int(row.lastrowid)

    def create_job(self, *, document_id: int, kb_version_id: int,
                   requested_by: int) -> int:
        with self.engine.begin() as conn:
            document = conn.execute(text("""
                SELECT id FROM rag_documents
                WHERE id = :document_id AND kb_version_id = :kb_version_id
                FOR UPDATE
            """), {"document_id": int(document_id), "kb_version_id": int(kb_version_id)}).mappings().first()
            if not document:
                raise ValueError("资料不存在或不属于指定知识库版本")
            row = conn.execute(text("""
                INSERT INTO rag_ingestion_jobs (document_id, kb_version_id, requested_by)
                VALUES (:document_id, :kb_version_id, :requested_by)
            """), {"document_id": int(document_id), "kb_version_id": int(kb_version_id),
                   "requested_by": int(requested_by)})
            return int(row.lastrowid)

    def update_job(self, job_id: int, *, status: str, attempt_no: int | None = None,
                   error_code: str | None = None, error_message: str | None = None,
                   metrics: dict | None = None) -> bool:
        statuses = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"}
        if status not in statuses:
            raise ValueError("非法入库任务状态")
        fields = ["status = :status", "error_code = :error_code", "error_message = :error_message",
                  "metrics_json = :metrics"]
        params = {"id": int(job_id), "status": status, "error_code": error_code,
                  "error_message": error_message,
                  "metrics": json.dumps(metrics, ensure_ascii=False) if metrics else None}
        if attempt_no is not None:
            fields.append("attempt_no = :attempt_no")
            params["attempt_no"] = int(attempt_no)
        if status == "RUNNING":
            fields.append("started_at = NOW()")
        if status in {"SUCCEEDED", "FAILED", "CANCELED"}:
            fields.append("finished_at = NOW()")
        with self.engine.begin() as conn:
            row = conn.execute(text(
                f"UPDATE rag_ingestion_jobs SET {', '.join(fields)} WHERE id = :id"), params)
            return row.rowcount > 0

    def replace_chunks(self, document_id: int, kb_version_id: int,
                       chunks: list[dict]) -> list[int]:
        """R1.4 使用：仅替换尚未发布文档的分块，避免改写历史版本。"""
        with self.engine.begin() as conn:
            document = conn.execute(text("""
                SELECT kb_version_id, status FROM rag_documents WHERE id = :id FOR UPDATE
            """), {"id": int(document_id)}).mappings().first()
            if not document:
                raise ValueError("资料不存在")
            if int(document["kb_version_id"]) != int(kb_version_id):
                raise ValueError("资料不属于指定知识库版本")
            if document["status"] in ("PUBLISHED", "ARCHIVED"):
                raise ValueError("已发布或已归档资料不能覆盖分块")
            conn.execute(text("""
                DELETE FROM rag_document_chunks
                WHERE document_id = :document_id AND kb_version_id = :kb_version_id
            """), {"document_id": int(document_id), "kb_version_id": int(kb_version_id)})
            chunk_ids: list[int] = []
            parent_ids: dict[int, int] = {}
            for item in chunks:
                parent_chunk_id = item.get("parent_chunk_id")
                parent_order_no = item.get("parent_order_no")
                if parent_chunk_id is None and parent_order_no is not None:
                    parent_chunk_id = parent_ids.get(int(parent_order_no))
                    if parent_chunk_id is None:
                        raise ValueError("子块必须位于其父块之后写入")
                row = conn.execute(text("""
                    INSERT INTO rag_document_chunks
                        (document_id, kb_version_id, parent_chunk_id, chunk_kind, order_no,
                         content, content_hash, chapter, page_number, char_start, char_end,
                         metadata_json, status)
                    VALUES (:document_id, :kb_version_id, :parent_chunk_id, :chunk_kind, :order_no,
                            :content, :content_hash, :chapter, :page_number, :char_start, :char_end,
                            :metadata_json, :status)
                """), {
                    "document_id": int(document_id), "kb_version_id": int(kb_version_id),
                    "parent_chunk_id": parent_chunk_id,
                    "chunk_kind": item["chunk_kind"], "order_no": int(item["order_no"]),
                    "content": item["content"], "content_hash": item["content_hash"],
                    "chapter": item.get("chapter"), "page_number": item.get("page_number"),
                    "char_start": item.get("char_start"), "char_end": item.get("char_end"),
                    "metadata_json": json.dumps(item.get("metadata", {}), ensure_ascii=False),
                    "status": item.get("status", "STAGED"),
                })
                chunk_id = int(row.lastrowid)
                chunk_ids.append(chunk_id)
                if item["chunk_kind"] == "parent":
                    parent_ids[int(item["order_no"])] = chunk_id
        return chunk_ids


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
        self.learning = LearningStore(self.engine)
        self.rag = RagKnowledgeBaseStore(self.engine)
