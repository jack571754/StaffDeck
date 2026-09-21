# Implementation Plan - 腾讯云 SkillHub 技能商店管理主管（SkillHub Supervisor Agent）

## 概述

本项目旨在实现一个企业级“**技能商店管理主管**”（`SkillHub Supervisor Agent`），结合 StaffDeck 的微核架构与数字员工技能体系，负责：
1. **持续监控与抓取**：对接腾讯云 SkillHub（`skillhub.cloud.tencent.com` / `api.skillhub.cn`），支持按关键词（文档解析、数据统计、代码开发等）、行业领域或指定技能 URL 抓取官方与社区优质技能。
2. **合规与深度安全审计**：解析 `SKILL.md` 的 YAML Frontmatter，进行 AST 语法树和敏感模式安全扫描（反向 Shell、未鉴权外发、破坏性系统命令如 `rm -rf` 等），高危项目直接打标为 `quarantined` 并阻断落盘。
3. **规范落盘与版本幂等**：标准化写入 `{PROJECT_ROOT}/skills/tencent_skillhub/{skill_id}/`，保持原生目录结构，附带 `store_meta.json` 记录审核指标；基于 SemVer 进行防覆盖检测，新版本自动归档备份旧版。
4. **项目本地化安装与读取集成**：
   - 将本地技能库无缝接入 StaffDeck 现有的 `GeneralSkill` 企业技能系统与技能市场 API（`/api/enterprise/general-skills/market/items`）。
   - 前端管理后台支持浏览本地腾讯云技能源、查看安全审核状态与一键安装到数字员工。
5. **定时采集与更新调度**：接入 StaffDeck 的 `scheduled_tasks` 定时任务后台机制，并提供 CLI 工具（`scripts/sync_tencent_skillhub.py`），支持自动化周期同步与更新。
6. **结构化执行汇报**：以 Markdown 结构化表格呈现同步与审核清单。

---

## User Review Required

> [!IMPORTANT]
> - **安全隔离策略**：外部抓取的 Python 脚本将在 StaffDeck 现有的通用技能沙箱虚拟环境（`backend/.runtime_venv`）中隔离运行，不污染主服务依赖环境。
> - **外部接口依赖**：已探明腾讯云 SkillHub 生产环境搜索与下载接口（`https://api.skillhub.cn/api/v1/search` 及 `download` 端点），并设计了 COS 镜像备份回退方案，确保在各种网络条件下的高可用性。

---

## Open Questions

> [!NOTE]
> 1. **隔离区（Quarantine）文件存储**：
>    - 方案 A（推荐）：检测出高危漏洞的技能保存到 `{PROJECT_ROOT}/skills/tencent_skillhub/.quarantine/{skill_id}/`，附带 `audit_report.json`，禁止进入正式技能市场，方便管理员复核审查。
>    - 方案 B：直接在内存中丢弃，不落地任何文件，仅在汇报表格和日志中标记 `quarantined`。
> 2. **更新策略与租户覆盖**：
>    - 当本地库检测到 SkillHub 技能发布了新版本，归档旧版本后，对系统中已经安装（绑定）给数字员工的技能实例，是提供“一键更新提示”还是由后台自动同步升级？（推荐：先更新本地基础库并提供更新提示，避免影响正在运行的任务）。

---

## Proposed Changes

### 1. 核心 Supervisor Agent 与安全审计模块

创建 `backend/app/skills/tencent_skillhub/` 专用模块：

#### [NEW] [models.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/skills/tencent_skillhub/models.py)
- 定义核心数据结构：
  - `SkillMarketMeta`: 技能名称、版本、slug、描述、分类、标签、依赖要求等。
  - `SecurityCheckResult`: 扫描级别（`SAFE` / `WARNING` / `QUARANTINED`）、触发的规则列表、命中代码片段。
  - `StoreMeta`: 本地持久化元数据（来源、抓取时间、版本、安全检查结果、脚本列表、文件散列哈希）。
  - `SyncReport`: 包含同步总量、新增、升级、跳过、隔离技能的汇报结构。

#### [NEW] [security_scanner.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/skills/tencent_skillhub/security_scanner.py)
- **安全审计引擎**：
  - AST 语法树静态分析（针对 Python 脚本）：扫描危险系统调用（`os.system`, `subprocess.Popen(shell=True)`, `eval`, `exec`）、反向 Shell（`socket.connect` + 重定向）、文件系统越权访问（敏感系统目录 `/etc/`, `C:\Windows\`）、未鉴权网络上传与外发。
  - 文本/Shell 脚本规则检测：扫描 `rm -rf`, `mkfs`, `format`, `chmod 777`, `curl ... | bash` 等破坏性命令。
  - Frontmatter 合规性校验：核查 YAML 规范，校验 `name`, `description` 以及指令 schema 完整性。

#### [NEW] [client.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/skills/tencent_skillhub/client.py)
- **SkillHub 采集客户端**：
  - `fetch_skillhub_catalog(keyword: str, limit: int = 20)`：调用 `https://api.skillhub.cn/api/v1/search` 获取技能列表，支持智能降级。
  - `download_skill_payload(skill_id: str)`：拉取原生技能包（支持 `api.skillhub.cn/api/v1/download` 与 COS 镜像），解压为内存中的标准文件树。

#### [NEW] [supervisor.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/skills/tencent_skillhub/supervisor.py)
- **Supervisor Agent 主控逻辑**：
  - **指令分解**：解析用户输入关键词、预设领域标签或直接 URL。
  - **采集与幂等更新控制**：
    - 读取本地版本；进行 SemVer 比较（`packaging.version`）。
    - 同版本跳过（`skipped`）；
    - 新版本自动将旧版本归档至 `{PROJECT_ROOT}/skills/tencent_skillhub/.archive/{skill_id}_{old_version}/` 后覆盖更新；
    - 首次安装直接落盘。
  - **规范落盘**：
    - 写入 `{PROJECT_ROOT}/skills/tencent_skillhub/{skill_id}/`，生成原生 `SKILL.md`、`scripts/`、`references/` 与 `store_meta.json`。
    - 维护全局技能索引文件 `{PROJECT_ROOT}/skills/tencent_skillhub/catalog.json`。
  - **执行结果汇报**：生成符合要求的 Markdown 结构化汇报表格。

---

### 2. 本地项目读取与安装接入（StaffDeck 内部商店集成）

结合 StaffDeck 现有的通用技能（`GeneralSkill`）体系与市场服务：

#### [MODIFY] [backend/app/api/general_skills.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/api/general_skills.py)
- 在 `GET /api/enterprise/general-skills/market/items` 中：
  - 支持 `source="tencent_skillhub"`（以及在 `source="all"` 中自动联合本地腾讯云技能资产）。
  - 读取 `{PROJECT_ROOT}/skills/tencent_skillhub/` 下的 `catalog.json` 与各技能 `store_meta.json`。
  - 标记 `origin: "tencent_skillhub"`，返回安全审计状态及已安装标记。
- 在 `POST /api/enterprise/general-skills/market/install` 中：
  - 当安装来自 `tencent_skillhub` 的技能时，直接从本地持久化目录读取文件结构并入库 `GeneralSkill`，绑定至目标数字员工或发布至企业技能广场。
- 新增 `POST /api/enterprise/general-skills/market/tencent-sync` 接口：
  - 允许企业管理员在前端控制台点击“立即同步腾讯云技能”或设置同步参数。

#### [NEW] [scheduled_sync.py](file:///d:/project/02-开源项目/StaffDeck/backend/app/skills/tencent_skillhub/scheduled_sync.py)
- 对接 StaffDeck 的 `app.scheduled_tasks`，允许系统管理员在“定时任务”页面创建定期抓取任务（如每天凌晨同步指定关键词领域）。

---

### 3. CLI 运维调度脚本

#### [NEW] [scripts/sync_tencent_skillhub.py](file:///d:/project/02-开源项目/StaffDeck/scripts/sync_tencent_skillhub.py)
- 独立运行的命令行管理工具，支持参数：
  ```bash
  python scripts/sync_tencent_skillhub.py --keywords "文档解析,数据统计,代码开发" --limit 15 --scheduled
  ```
- 也可以指定 URL 抓取：
  ```bash
  python scripts/sync_tencent_skillhub.py --url "https://skillhub.cloud.tencent.com/skills/kuaidi100-skill"
  ```

---

### 4. 前端企业控制台呈现（SkillMarketDialog）

#### [MODIFY] [frontend-enterprise/src/pages/general-skills/SkillMarketDialog.tsx](file:///d:/project/02-开源项目/StaffDeck/frontend-enterprise/src/pages/general-skills/SkillMarketDialog.tsx)
- 在市场弹窗中：
  - 数据源选项增加 `tencent_skillhub`（"腾讯云 SkillHub 本地库"）。
  - `ORIGIN_LABEL_MAP` 添加 `tencent_skillhub: '腾讯云 SkillHub'`。
  - 列表增加“安全审计”标签（绿色“已审计合规” / 橙色“有警告”）。
  - 增加“同步技能”触发入口按钮。

---

## Verification Plan

### Automated Tests
1. **安全审计扫描测试**：
   - 验证对正常技能（包含常规 python/shell）的合规通过。
   - 验证对恶意行为（反弹 shell、`rm -rf /`、网络外发凭证等）的精准拦截与 `quarantined` 标记。
2. **采集与 SemVer 幂等测试**：
   - 模拟重复抓取验证跳过逻辑（不修改已有文件）。
   - 模拟版本递增（1.0.0 -> 1.1.0）验证自动归档到 `.archive/` 与原地升级。
3. **StaffDeck 市场接口集成测试**：
   - 测试 `/api/enterprise/general-skills/market/items?source=tencent_skillhub` 能否正确读取并返回本地目录内容。
   - 测试调用 `/api/enterprise/general-skills/market/install` 能否将本地技能正确安装入库为 StaffDeck `GeneralSkill` 并可以在 Agent Harness 中运行。

### Manual Verification
1. 运行 `python scripts/sync_tencent_skillhub.py --keywords "文档,数据"`，观察控制台输出的 Markdown 结构化汇报表格。
2. 检查本地 `{PROJECT_ROOT}/skills/tencent_skillhub/` 下目录结构、`store_meta.json` 与 `catalog.json` 生成是否符合规范。
3. 打开 StaffDeck 前端“企业技能”->“技能市场”，切换到“腾讯云 SkillHub 本地库”，预览技能并点击一键安装，验证 Agent 是否能够正常调用该本地技能。
