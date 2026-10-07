-- T13.4（总体设计 §9.4「项目级」权限粒度 + 附录 C「本人项目」列）：项目成员表——
-- 角色级 RBAC 之上的资源级可见性：项目 owner 与成员可见该项目及其会话（会话隶属项目，
-- 可见性随项目，不引入会话级独立 ACL——PHASE-013 §2.4 口径）。
-- 建表即回填既有项目 owner（projects.owner_id 天然 owner）。
CREATE TABLE project_members (
    project_id  uuid        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    role        varchar(32) NOT NULL DEFAULT 'member',  -- owner | member
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX idx_project_members_user ON project_members (user_id);

INSERT INTO project_members (project_id, user_id, role)
SELECT id, owner_id, 'owner' FROM projects WHERE owner_id IS NOT NULL
ON CONFLICT (project_id, user_id) DO NOTHING;
