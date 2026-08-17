# RBAC 扩展方案：文档管理管理员化 + 超级管理员分权（✅ 已实施 2026-08-17）

| 项目 | 内容 |
|---|---|
| 编写日期 | 2026-08-17 |
| 状态 | **已实施**（后端 + 迁移 + 前端 + 回归测试 160 全绿），本页为设计记录 |
| 关联 | [PRD.md](./PRD.md) FR-32/33/34（认证/权限/审计）、[P2_PLAN.md](./P2_PLAN.md) |

---

## 1. 需求背景

当前权限模型：`role ∈ {user, admin}`，admin 全权（全部部门文档 + 用户管理 + 审计）。

新需求：
1. **文档管理仅管理员可见**：普通用户不再拥有文档管理（上传/删除/列表）入口与权限，知识库内容由管理员统一维护；
2. **超级管理员分权**：新增超级管理员，可将"管理员"权限授予特定人员，并**指定其负责的部门集合**（部门管理员只能管理自己负责部门的文档）。

## 2. 角色模型（三档）

| 角色 | 定位 | 文档管理 | 问答检索范围 | 用户/管理员管理 | 审计查询 |
|---|---|---|---|---|---|
| `super_admin` | 超级管理员（全局） | 全部部门 | 全部 | ✅ 含角色与部门分配 | ✅ |
| `admin` | 部门管理员（负责 N 个部门） | **仅负责部门** | 仅负责部门 | ❌ | ❌ |
| `user` | 普通用户 | ❌（无入口，API 403） | 本部门 | ❌ | ❌ |

> 说明：admin 的问答检索范围与其文档可见范围一致（只答自己负责部门的内容），避免"管不了却答得了"的不一致。

## 3. 数据模型变更

```python
# User.role 取值扩展
role: str  # "super_admin" | "admin" | "user"（默认 user）

# 新表：部门管理员负责部门集合（多对多）
class AdminDepartment(Base):
    __tablename__ = "admin_departments"
    id: int
    user_id: int  # FK users.id
    department: str  # String(64)
    __table_args__ = (UniqueConstraint("user_id", "department"),)
```

- `User.department` 保留：普通用户归属部门（检索过滤、上传归属）。
- admin 的可见范围 = `AdminDepartment` 全部记录；`super_admin` = None（不限）。

## 4. 后端改造点

### 4.1 deps.py：可见范围统一入口

```python
async def user_visible_departments(user) -> list[str] | None:
    """super_admin → None（全部）；admin → AdminDepartment 列表；user → [department]。"""
```
- 替代现有 `user_departments`（QA 检索 filter 下推，改动点 1）。
- 文档列表/删除/上传的部门过滤统一走该入口（改动点 2、3）。

### 4.2 documents 路由：管理员专属

- 新增依赖 `_require_doc_admin(user)`：`role in (super_admin, admin)` 否则 403（前端隐藏 + 后端校验双保险）。
- 列表：`visible = user_visible_departments(user)`；`visible is None` → 全量；否则 `department in visible`。
- 删除：目标文档 `department ∉ visible` → 403（admin 只能删负责部门）。
- 上传：`department` 必须 ∈ admin 负责部门（或 super_admin 任意）；无部门归属的 admin 默认无权限。
- **connector/worker 不受影响**（服务内部路径，不走该路由）。

### 4.3 admin 路由：仅 super_admin

- `_require_admin` 改为 `_require_super_admin`（`role == "super_admin"`）。
- create_user/update_user：role 白名单扩为三值；校验规则：
  - 不能创建/提升 `super_admin`（仅能由现有 super_admin 手工操作？或允许 super_admin 创建 admin/user，super_admin 只能通过"变更"授予——简化：super_admin 可分配任意角色，但**同一时刻至少保留一个 super_admin** 防锁死）；
  - 不能修改自己的角色/停用自己（防自锁）。
- 新端点：`PUT /api/v1/admin/users/{id}/departments`（设置 admin 负责部门集合，覆盖式）与 `GET`（查询）。
- 审计：创建用户/角色变更/部门分配 → `action="user_admin"` 记录完整 detail。

### 4.4 问答服务

- `stream_answer` 的 departments 参数由 `user_visible_departments` 提供（QA 侧无逻辑改动，仅入口函数替换）。

## 5. 前端改造点

| 页面 | 变更 |
|---|---|
| App.vue 侧边栏 | 按角色渲染菜单：user → 仅"问答工作台"；admin → +"文档管理"；super_admin → +"用户管理" |
| 路由守卫 | `/docs` 仅 super_admin/admin；新增 `/users` 仅 super_admin（前端隐藏 + 后端 403 兜底） |
| DocsView | admin 视角：上传时部门下拉限定"我的负责部门"（多部门时显示全部负责部门）；super_admin 显示全部部门 |
| 用户管理页（新） | super_admin：用户列表（角色/部门徽标）、创建用户（选角色）、角色切换、admin 负责部门多选分配 |
| LoginView | 无变化 |

## 6. 迁移步骤

1. `init_db()` 自动建 `admin_departments` 表（Base.metadata.create_all）；
2. 存量 `role='admin'` 用户 → 角色改为 `super_admin`（**保持现有能力不缩水**；后续可由 super_admin 再降级为部门 admin 并分配部门）；
3. `AdminDepartment` 初始化：为降级后的 admin 补录负责部门（迁移脚本/管理接口）；
4. 回归：148 测试中涉及 admin 的用例按新语义更新（role 值、可见范围函数、403 断言）。

## 7. 验收标准

1. 普通用户：无文档管理入口，直连 API 返回 403；问答仅本部门；
2. 部门 admin：只能看到/上传/删除负责部门文档（多部门集合正确），越权 403；问答范围=负责部门；
3. super_admin：全量文档 + 用户管理 + 可给 admin 分配/调整部门集合，操作全程审计；
4. 至少保留一个 super_admin（自锁防护），自身角色/停用不可操作；
5. 全量测试通过（新增 RBAC 回归用例：三角色 × 文档/问答/管理端点）。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 普通用户失去上传能力（行为变更） | 需求明确；文档由管理员统一维护，Connector 仍可自动摄入 |
| 存量 admin 语义变化 | 迁移为 super_admin 保权；文档中明确新旧角色对应 |
| 前端仅隐藏导致越权 | 后端所有端点做角色校验（双保险），回归用例覆盖 |
| 多部门 admin 检索范围扩大 | 可见范围统一入口，避免散落判断 |

---

*规划稿，确认后按 后端 → 迁移 → 前端 → 回归 的顺序实施。*
