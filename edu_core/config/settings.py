"""全局配置 — pydantic-settings 单例。

设计原则（对齐 knowforge-rag-platform）：

- 项目根目录由本文件位置推导（edu_core/config/settings.py -> 上两级），
  任何代码不得硬编码盘符路径；旧版 config.yaml 中的硬编码路径已废弃；
- 全部配置项可用环境变量覆盖，前缀 ``EDU_``，例如 ``EDU_MYSQL_HOST``；
- 本机运行用根目录 ``.env``（从 .env.example 复制），Docker 运行由 compose 注入；
- settings 进程级单例，业务代码统一通过 get_settings() 获取。

历史字段兼容：旧版 config.yaml 仍被 legacy/ 代码读取，新代码一律不读 yaml。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：edu_core/config/settings.py 的上两级
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用全局配置。所有字段都有安全默认值，可被 .env / 环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EDU_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "Edu Hierarchical Classifier"
    api_host: str = "127.0.0.1"
    api_port: int = 7860
    # CORS 白名单（逗号分隔），禁止使用 "*"
    cors_allow_origins: str = "http://127.0.0.1:7860,http://localhost:7860,http://127.0.0.1:8000,http://localhost:8000"
    # 单客户端每分钟最大请求数（简单滑动窗口限流）
    rate_limit_per_minute: int = 120

    # ---------- 路径（相对项目根） ----------
    data_processed_dir: str = "data/processed"
    backbone_dir: str = "models/pretrained_backbone/bert-base-chinese"
    model_versions_dir: str = "models/versions"
    eval_sets_dir: str = "eval_sets"
    reports_dir: str = "reports"
    logs_dir: str = "logs"
    static_dir: str = "static"

    # ---------- 模型 ----------
    max_seq_length: int = 256
    freeze_ratio: float = 0.75
    # CPU 推理动态量化（GPU 环境自动关闭）
    use_dynamic_quantization: bool = True
    # 推理输入缓存上限（进程内 LRU，修复旧版实例 lru_cache 强引用 self 的问题）
    predict_cache_size: int = 512
    # 知识点学科 mask（改进计划 WP-G1）：按预测学科屏蔽非法知识点的 logits。
    # 实测（v0.1-base 全量测试集 1039 条）：开启后级联持平、知识点 F1 微降
    # （学科预测错误的样本被 mask 连坐），故默认关闭；学科感知增强（WP-G2）落地后可重测开启
    knowledge_mask_enabled: bool = False
    # 学科感知知识头（改进计划 WP-G2）：>0 时学科 embedding 与 pooler 拼接进知识点头，
    # 写入版本 manifest.architecture，predictor 按声明构建（0 = 旧结构，兼容旧版本）
    subject_embedding_dim: int = 64

    # ---------- 分类接口 ----------
    classify_max_chars: int = 4000
    # 置信度分级阈值（三级平均置信度）
    confidence_high: float = 0.80
    confidence_medium: float = 0.60

    # ---------- 认证与权限（改进计划 WP-D） ----------
    # 总开关：仅限本机开发调试临时关闭鉴权，生产必须 false
    auth_disabled: bool = False
    # JWT 签名密钥：鉴权开启时必须为 ≥16 字符的非示例值（preflight 强校验）
    jwt_secret: str = ""
    # token 有效期（分钟）
    token_expire_minutes: int = 480
    # 首次启动且 users 表为空时自动创建的 admin 初始密码（创建后可删除该配置）
    admin_bootstrap_password: str = ""

    # ---------- MySQL（业务主库） ----------
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "root123"
    mysql_db: str = "edu_classifier"

    # ---------- Milvus（题目语义查重，增强组件） ----------
    milvus_enabled: bool = True
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "edu_question_dedup"
    # BERT [CLS] 池化向量维度
    dedup_vector_dim: int = 768
    dedup_top_k: int = 3
    # 相似度超过该值提示疑似重复题
    dedup_similarity_threshold: float = 0.95

    # ---------- 评测与质量门禁 ----------
    golden_set_size: int = 300
    gate_min_subject_acc: float = 0.85
    gate_min_type_f1: float = 0.85
    gate_min_knowledge_f1: float = 0.40
    gate_min_cascade_acc: float = 0.65
    gate_max_latency_ms: float = 1500.0

    # ---------- 反馈/Bad Case ----------
    # 低置信度阈值：低于该值的分类在 Bad Case 导出时优先入选
    badcase_low_confidence: float = 0.70

    # ---------- 派生路径 ----------
    def root(self, *parts: str) -> Path:
        """项目根目录下拼接相对路径。"""
        return PROJECT_ROOT.joinpath(*parts)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def mysql_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    def abs_path(self, relative: str) -> Path:
        """把配置里的项目相对路径转成绝对路径。"""
        p = Path(relative)
        return p if p.is_absolute() else self.root(p)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级配置单例。"""
    return Settings()
