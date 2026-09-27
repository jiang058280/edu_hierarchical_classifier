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
from pydantic import Field
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

    # ---------- RAG 教育知识库（R1） ----------
    # 默认关闭：资料入库与问答均须由教师审核、配置完成后再显式启用。
    rag_enabled: bool = False
    rag_upload_dir: str = "data/rag_uploads"
    rag_documents_collection: str = "edu_rag_documents"
    rag_questions_collection: str = "edu_rag_questions"
    rag_faq_collection: str = "edu_rag_faq"
    rag_embedding_provider: str = "openai_compatible"
    rag_embedding_base_url: str = ""
    rag_embedding_api_key: str = ""
    rag_embedding_model: str = ""
    rag_embedding_dimension: int = 1024
    # 百炼 text-embedding-v4 的 OpenAI 兼容接口单次最多接受 10 条输入。
    # 保持此默认值也能兼容常见的 OpenAI-compatible 向量服务。
    rag_embedding_batch_size: int = 10
    # 本地题库知识源命中此阈值后直接返回答案/解析，不调用外部模型。
    rag_local_question_min_score: float = 0.75
    rag_reranker_provider: str = "disabled"
    rag_reranker_base_url: str = ""
    rag_reranker_api_key: str = ""
    rag_reranker_model: str = ""
    # 不复用向量阈值；启用模型时须显式配置经评测的独立阈值。
    rag_reranker_min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    rag_reranker_max_candidates: int = Field(default=24, ge=1, le=100)
    rag_llm_provider: str = "openai_compatible"
    rag_native_stream_enabled: bool = False
    rag_grounding_check_enabled: bool = False
    rag_conversation_memory_enabled: bool = False
    rag_llm_base_url: str = ""
    rag_llm_api_key: str = ""
    rag_llm_model: str = ""
    rag_top_k: int = 12
    # 默认关闭，先完成本地评测再启用；不改已有部署的检索行为。
    rag_hybrid_enabled: bool = False
    rag_bm25_max_chunks: int = 5000
    rag_rrf_k: int = 60
    rag_rerank_top_k: int = 5
    rag_min_evidence_score: float = 0.55
    rag_max_context_chars: int = 6000
    rag_parent_chunk_chars: int = 1000
    rag_child_chunk_chars: int = 350
    rag_chunk_overlap_chars: int = 60
    rag_request_timeout_seconds: float = 30.0
    rag_max_upload_bytes: int = 25 * 1024 * 1024
    rag_ocr_enabled: bool = False
    rag_ocr_backend: str = Field(default="native", pattern=r"^(native|tesseract_js)$")
    rag_ocr_layout_mode: str = Field(default="page", pattern=r"^(page|lines_tables)$")
    rag_ocr_node_command: str = "node"
    rag_ocr_js_module_dir: str = ""
    rag_ocr_tessdata_dir: str = ""
    rag_ocr_pdftoppm_command: str = "pdftoppm"
    rag_ocr_tesseract_command: str = "tesseract"
    rag_ocr_languages: str = Field(default="chi_sim+eng", pattern=r"^[a-zA-Z0-9_]+(\+[a-zA-Z0-9_]+)*$")
    rag_ocr_min_text_chars: int = Field(default=20, ge=1, le=500)
    rag_ocr_max_pages: int = Field(default=20, ge=1, le=100)
    rag_ocr_max_side_pixels: int = Field(default=3000, ge=1000, le=5000)
    rag_ocr_timeout_seconds: float = Field(default=60, gt=0, le=300, allow_inf_nan=False)
    rag_max_documents_per_teacher: int = 200
    rag_ingestion_max_attempts: int = 3

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
