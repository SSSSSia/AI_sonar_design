-- =====================================================================
-- Sonar-CAD v2 · V2 RBAC 基础播种（T1.1）
-- 依据：PHASE-001 §3.2、P3 附录 C 权限矩阵（开发期最小集，阶段 2/3 随功能补全）
-- 注意：admin 初始口令为开发默认值 admin123（BCrypt），上线前必须修改。
-- =====================================================================

-- ---------- 角色 ----------
INSERT INTO roles (code, name, description) VALUES
    ('admin',    '系统管理员', '全部权限（P3 附录 C）'),
    ('designer', '设计员',     '创建项目/会话、发起设计、编辑参数方案'),
    ('reviewer', '审阅员',     '审阅关①② 裁决与批注'),
    ('viewer',   '查看者',     '只读浏览');

-- ---------- 权限点（最小集） ----------
INSERT INTO permissions (code, name, description) VALUES
    ('project:write',  '项目写',   '创建/修改项目'),
    ('project:read',   '项目读',   '查看项目'),
    ('session:write',  '会话写',   '创建会话/驱动状态机事件'),
    ('session:read',   '会话读',   '查看会话'),
    ('gate:decide',    '审阅裁决', '审阅关①② 通过/驳回（P3 §9.4）'),
    ('kb:read',        '知识库读', '浏览知识库'),
    ('kb:write',       '知识库写', '文档上传与知识治理'),
    ('admin:all',      '系统管理', '用户/角色/配置管理');

-- ---------- 角色-权限矩阵 ----------
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p
  ON (r.code = 'admin') OR
     (r.code = 'designer' AND p.code IN ('project:write','project:read','session:write','session:read','kb:read','kb:write')) OR
     (r.code = 'reviewer' AND p.code IN ('project:read','session:read','gate:decide','kb:read')) OR
     (r.code = 'viewer'   AND p.code IN ('project:read','session:read','kb:read'));

-- ---------- admin 用户（开发默认口令 admin123，上线必改） ----------
INSERT INTO users (username, password_hash, display_name, status)
VALUES ('admin', '$2b$10$EuEzAuZbyJ4NltSNFXvG.OouB5GYZGo19mmTMFrpKc/Uwcv7oyXy6', '系统管理员', 'active');

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u, roles r WHERE u.username = 'admin' AND r.code = 'admin';
