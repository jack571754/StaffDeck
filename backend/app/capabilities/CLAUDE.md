[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **capabilities**

# capabilities — 能力契约体系（Provider 端口/适配器）

## 模块职责

用「端口 + 适配器」把 Agent 运行时（`core/`）与具体能力提供方（本地知识库、通用技能包等）解耦：定义**契约数据类与 Protocol 端口**（`contracts.py`）、**显式注册表与不可变快照**（`registry.py`）、**本地进程绑定**（`local_registry.py`）、**本地实现**（`local_knowledge.py` / `local_general_skill.py`），并提供契约自检工具（`testkit.py`）与 JSON Schema 目录（`schemas/`）。

**边界**：这里只描述“能力长什么样、如何被选择与快照”，不描述“能力清单如何授权给某个员工”（那是 `../core/capability_manifest.py`）。`scope.py` 只是对 `app.capability_scope` 的转发门面。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `contracts.py` | 契约与端口：`CapabilityContext`、`KnowledgeSearchQuery`、`KnowledgeScope`、`KnowledgeHit`、`KnowledgeSearchResult`、`CitationDetail`、`SceneSkillSummary`/`Definition`、`GeneralSkillSummary`/`File`/`Package`、`GeneralSkillResourceRef`；Protocol `KnowledgeRuntime` / `SceneSkillCatalog` / `GeneralSkillCatalog`；`JsonValue`/`JsonObject`/`ExtensionMap` 类型别名 |
| `registry.py` | `CapabilityBinding`（运行时绑定，含 `provider`）/ `DurableCapabilityBinding`（可序列化身份，**不含 live client**）/ `CapabilitySnapshot`（一次 Turn 冻结的 provider 选择，`get`/`require`）/ `CapabilityRegistry`（`register` / `register_rehydrator` / `rehydrate` / `snapshot` / `seal`） |
| `local_registry.py` | `build_local_capability_registry(db, model_config, *, service_factory=KnowledgeService)`：注册 `knowledge.scopes`/`knowledge.search`/`knowledge.citation`（契约 `knowledge.v1`，部署 `local-process`，配置修订 `legacy-local-v1:<model_config id:revision>`），并 `seal()` |
| `local_knowledge.py` | `LocalKnowledgeRuntime(KnowledgeRuntime)`：本地进程知识检索实现，`provider_id="local_knowledge"` |
| `local_general_skill.py` | `LocalGeneralSkillCatalog`；`package_from_row` / `resource_ref_from_row` / `runtime_snapshot_from_package` / `local_runtime_snapshot`（把 `GeneralSkill` 表行转成契约对象）；`LOCAL_GENERAL_SKILL_PROVIDER_ID="local_general_skill"` |
| `errors.py` | `CapabilityErrorInfo`（`assert_provider_error` 校验的形状）、`CapabilityProviderError` |
| `testkit.py` | 契约自检：`assert_knowledge_search_result`、`assert_general_skill_package`、`assert_namespaced_extensions`、`assert_provider_error`；`ContractViolation`；保留命名空间 `{core, staffdeck}`，扩展命名空间须匹配 `^[a-z][a-z0-9_]*$` |
| `scope.py` | 转发 `app.capability_scope`：`CapabilityScope`、`GENERAL_CAPABILITY_SCOPE`、`SOP_SPECIFIC_CAPABILITY_SCOPE`、`normalize_capability_scope` |
| `schemas/` | `knowledge.search.request.v1.json`、`knowledge.search.result.v1.json`、`provider.error.v1.json` |

## 关键不变量

- **注册是配置，不是运行时热插拔**：`CapabilityRegistry.seal()` 之后 `register` / `register_rehydrator` 抛 `RuntimeError("capability registry is sealed")`。启动期注册，运行期只读。
- **快照不可变**：`CapabilitySnapshot.bindings` 是 `MappingProxyType`；`snapshot_id` = 规范化 JSON 的 sha256（`sha256(json.dumps(canonical, separators=(",",":")))`）。改 canonical 字段会改变快照 id。
- **Durable binding 不含 live client**：`DurableCapabilityBinding` 仅存身份（provider/deployment/contract/operation_versions/config_revision），恢复时经 `rehydrate(binding)` 重建 provider。
- **契约版本校验**：`snapshot(supported_contracts=...)` 在契约版本不匹配时抛 `ValueError("unsupported capability contract: ...")`；`local_registry.rehydrate` 在修订退休/身份不可用时抛 `LookupError`。
- **扩展命名空间**：`core` / `staffdeck` 为保留前缀，不可被 provider 扩展占用。

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 契约数据结构（知识命中、技能包、引用） | `contracts.py` |
| 新增/修改能力绑定、快照语义、契约版本校验 | `registry.py` |
| 本地知识能力的绑定与配置修订 | `local_registry.py`、`local_knowledge.py` |
| `GeneralSkill` 表行 → 契约对象的转换 | `local_general_skill.py`（`package_from_row` 等） |
| provider 错误形状 / 校验 | `errors.py`、`testkit.py` |
| JSON Schema（对外契约文件） | `schemas/*.json` |

## 测试与质量

`backend/tests/test_capability_contracts.py`、`test_capability_registry.py`、`test_capability_errors.py`、`test_capability_schemas.py`、`test_capability_scope.py`、`test_capability_testkit.py`、`test_local_registry.py`、`test_local_knowledge_provider.py`、`test_general_skill_provider_runtime.py`。

## 相关文件清单

`contracts.py`、`registry.py`、`local_registry.py`、`local_knowledge.py`、`local_general_skill.py`、`errors.py`、`testkit.py`、`scope.py`、`schemas/*.json`；外部：`app/capability_scope.py`、`app/knowledge/`、`app/core/capability_manifest.py`。

## 变更记录 (Changelog)

- 2026-09-24T09:46 — 初始化架构师新建；登记端口/适配器结构、注册表 seal 不变量与快照 id 算法。
