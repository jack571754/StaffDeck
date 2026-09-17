## Task 1: 加密模块 + 数据模型 — 完成报告

### 完成的文件列表

**新建文件：**
- `backend/app/data_query/__init__.py` — 模块导出入口
- `backend/app/data_query/security.py` — AES-256-GCM 加密解密工具
- `backend/app/data_query/models.py` — DataSource / QueryTemplate 模型及 CRUD schema
- `backend/app/db/utils.py` — new_id / utc_now 工具函数（从 models.py 抽出，解耦循环依赖）
- `backend/tests/test_data_query_security.py` — 11 个单元测试

**修改文件：**
- `backend/app/db/models.py` — new_id / utc_now 改为从 app.db.utils 导入
- `backend/app/db/database.py` — init_db() 中注册 data_query 模型到 SQLModel.metadata

### 接口清单

**加密工具（app.data_query.security）：**
- `encrypt_value(plaintext: str, key: bytes | None = None) -> str` — 返回 `enc:` 前缀密文
- `decrypt_value(ciphertext: str, key: bytes | None = None) -> str` — 非 `enc:` 前缀原样返回
- `encrypt_config(config: dict, sensitive_keys: list[str], key: bytes | None = None) -> dict`
- `decrypt_config(config: dict, sensitive_keys: list[str], key: bytes | None = None) -> dict`

**模型（app.data_query.models）：**
- `DataSource` / `DataSourceCreate` / `DataSourceUpdate` / `DataSourceRead`
- `QueryTemplate` / `QueryTemplateCreate` / `QueryTemplateUpdate` / `QueryTemplateRead`
- `QueryExecuteRequest` / `QueryExecuteResult`

### 关键实现细节

- **加密算法**：AES-256-GCM，密钥从 APP_SECRET 经 SHA-256 派生（32 字节）
- **密文格式**：`enc:` + base64(nonce + ciphertext+tag)，nonce 12 字节随机
- **DataSourceRead**：不返回 config_json，防止凭证泄露
- **唯一约束**：`(tenant_id, name)` 联合唯一
- **表名**：`data_sources`、`query_templates`（蛇形复数）
- **ID 前缀**：ds_ / qt_

### 测试结果

```
11 passed in 1.23s
```

测试覆盖：
1. 加密解密往返（`test_encrypt_decrypt_roundtrip`）
2. 不同 nonce 产生不同密文（`test_different_nonce_produces_different_ciphertext`）
3. 错误密钥解密失败（`test_wrong_key_decrypt_fails`）
4. 非加密值原样返回（`test_non_encrypted_value_passthrough`）
5. config 加密解密（敏感字段加密，非敏感字段不变）（`test_config_encrypt_decrypt_sensitive_keys`）
6. 缺失的敏感 key 静默跳过（`test_config_missing_sensitive_keys_ignored`）
7. 模型注册与建表（`test_data_query_models_register_and_create`）
8. DataSourceRead 不含 config_json（`test_datasource_read_excludes_config_json`）
9. 非法密钥长度抛错（`test_invalid_key_length_raises`）
10. 空字符串加密（`test_encrypted_empty_string`）
11. Unicode 值往返（`test_unicode_values`）

### 遇到的问题

**循环依赖问题**：最初按 brief 将 data_query 模型 import 放在 `models.py` 末尾用于注册 metadata，导致 `app.data_query.models → app.db.models → app.data_query.models` 循环导入。

**解决方案**：将 `new_id` / `utc_now` 从 `models.py` 抽出到独立的 `app/db/utils.py`，data_query 模型从 utils 导入；同时将 data_query 模型的注册移到 `database.py` 的 `init_db()` 函数内（在 `create_all` 之前导入），打破循环依赖链。

### Commit

- **Hash**: `062df51`
- **Message**: `feat(data-query): add data source & query template models with encryption`
- **Files changed**: 8 files, 614 insertions(+), 11 deletions(-)
