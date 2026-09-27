-- V13__normalize_knowledge_point_colon.sql
-- V12 已清理 '学科::' 与 '学科：' 前缀；本迁移补齐半角冒号变体（如 '数学:绝对值的意义'）。
-- 教师命名中包含全角冒号的细粒度标签（如 '少年有梦：梦想的意义与实现路径'）不属前缀残留，不受影响。

UPDATE questions
SET knowledge_point = TRIM(BOTH ':' FROM
    REPLACE(knowledge_point, CONCAT(subject, ':'), ''))
WHERE subject <> ''
  AND knowledge_point LIKE CONCAT(subject, ':%');

UPDATE rag_question_knowledge
SET knowledge_point = TRIM(BOTH ':' FROM
    REPLACE(knowledge_point, CONCAT(subject, ':'), ''))
WHERE subject <> ''
  AND knowledge_point LIKE CONCAT(subject, ':%');
