-- 训练阶段的内部标签采用“学科::知识点”；题库与本地 RAG 使用独立学科列，
-- 因此持久化数据只保留人可读的知识点，避免教师端出现“数学::三角函数”。
UPDATE questions
SET knowledge_point = TRIM(BOTH '：' FROM TRIM(BOTH ':' FROM
    REPLACE(REPLACE(knowledge_point, CONCAT(subject, '::'), ''), CONCAT(subject, '：'), '')
))
WHERE subject <> ''
  AND (knowledge_point LIKE CONCAT(subject, '::%') OR knowledge_point LIKE CONCAT(subject, '：%'));

-- 已同步的题库知识源同样规范；下次“同步题库知识源”也会按 questions 再次覆盖。
UPDATE rag_question_knowledge
SET knowledge_point = TRIM(BOTH '：' FROM TRIM(BOTH ':' FROM
    REPLACE(REPLACE(knowledge_point, CONCAT(subject, '::'), ''), CONCAT(subject, '：'), '')
))
WHERE subject <> ''
  AND (knowledge_point LIKE CONCAT(subject, '::%') OR knowledge_point LIKE CONCAT(subject, '：%'));
