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
    """题库（持久化，替代旧版内存 question_db）。"""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    def insert(self, content: str, subject: str, question_type: str,
               knowledge_point: str, source: str = "manual") -> int:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO questions (content, subject, question_type, knowledge_point, source, text_hash)
                VALUES (:c, :s, :t, :k, :src, :h)
            """), {
                "c": content, "s": subject, "t": question_type,
                "k": knowledge_point, "src": source, "h": text_hash(content),
            })
            return row.lastrowid

    def delete(self, question_id: int) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "DELETE FROM questions WHERE id = :i"), {"i": question_id})
            return row.rowcount > 0

    def list(self, subject: str = "", question_type: str = "", keyword: str = "",
             limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """筛选列表 + 总数。keyword 匹配题干或知识点。"""
        conditions, params = [], {"limit": int(limit), "offset": int(offset)}
        if subject:
            conditions.append("subject = :subject")
            params["subject"] = subject
        if question_type:
            conditions.append("question_type = :qtype")
            params["qtype"] = question_type
        if keyword:
            conditions.append("(content LIKE :kw OR knowledge_point LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.engine.connect() as conn:
            total = conn.execute(text(f"SELECT COUNT(*) FROM questions {where}"), params).scalar_one()
            rows = conn.execute(text(f"""
                SELECT id, content, subject, question_type, knowledge_point, source, status, created_at
                FROM questions {where} ORDER BY id DESC LIMIT :limit OFFSET :offset
            """), params).mappings().all()
        items = [dict(r) | {"created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")} for r in rows]
        return items, int(total)

    def count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one())


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


class StoreBundle:
    """全部 Store 的聚合容器，service 层一次注入。"""

    def __init__(self, engine: Engine | None = None, settings: Settings | None = None):
        self.engine = engine or get_engine(settings)
        self.model_versions = ModelVersionStore(self.engine)
        self.classifications = ClassificationStore(self.engine)
        self.questions = QuestionStore(self.engine)
        self.feedback = FeedbackStore(self.engine)
        self.stats = StatsStore(self.engine)
