import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Database, FlaskConical, Globe, Loader2, ShieldCheck } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
} from '@/components/ui';
import { Button } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { dataSourcesApi, type DataSource } from '@/api/data-query';

interface DataSourceDialogProps {
  open: boolean;
  dataSource?: DataSource | null;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

interface BaseForm {
  name: string;
  description: string;
  type: 'mysql' | 'http_api';
  read_only: boolean;
  status: 'active' | 'inactive';
  allowed_tables: string;
}

interface MysqlForm {
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  charset: string;
}

interface HttpApiForm {
  base_url: string;
  headers: string;
  timeout: string;
}

const DEFAULT_BASE: BaseForm = {
  name: '',
  description: '',
  type: 'mysql',
  read_only: true,
  status: 'active',
  allowed_tables: '',
};

const DEFAULT_MYSQL: MysqlForm = {
  host: '127.0.0.1',
  port: '3306',
  database: '',
  username: '',
  password: '',
  charset: 'utf8mb4',
};

const DEFAULT_HTTP: HttpApiForm = {
  base_url: '',
  headers: '',
  timeout: '30',
};

export default function DataSourceDialog({
  open,
  dataSource,
  onOpenChange,
  onSaved,
}: DataSourceDialogProps) {
  const isEdit = Boolean(dataSource);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [baseForm, setBaseForm] = useState<BaseForm>(DEFAULT_BASE);
  const [mysqlForm, setMysqlForm] = useState<MysqlForm>(DEFAULT_MYSQL);
  const [httpForm, setHttpForm] = useState<HttpApiForm>(DEFAULT_HTTP);

  useEffect(() => {
    if (!open) return;
    if (dataSource) {
      setBaseForm({
        name: dataSource.name,
        description: dataSource.description || '',
        type: dataSource.type as 'mysql' | 'http_api',
        read_only: dataSource.read_only,
        status: dataSource.status as 'active' | 'inactive',
        allowed_tables: (dataSource.allowed_tables_json || []).join(', '),
      });

      // config_json is not returned by the API for security reasons
      setMysqlForm(DEFAULT_MYSQL);
      setHttpForm(DEFAULT_HTTP);
    } else {
      setBaseForm(DEFAULT_BASE);
      setMysqlForm(DEFAULT_MYSQL);
      setHttpForm(DEFAULT_HTTP);
    }
  }, [open, dataSource]);

  function updateBase<K extends keyof BaseForm>(key: K, value: BaseForm[K]) {
    setBaseForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateMysql<K extends keyof MysqlForm>(key: K, value: MysqlForm[K]) {
    setMysqlForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateHttp<K extends keyof HttpApiForm>(key: K, value: HttpApiForm[K]) {
    setHttpForm((prev) => ({ ...prev, [key]: value }));
  }

  function buildConfigJson(): Record<string, unknown> | null {
    if (baseForm.type === 'mysql') {
      if (!mysqlForm.database.trim()) {
        notify.error('请填写数据库名');
        return null;
      }
      const config: Record<string, unknown> = {
        host: mysqlForm.host.trim() || '127.0.0.1',
        port: Number(mysqlForm.port) || 3306,
        database: mysqlForm.database.trim(),
        username: mysqlForm.username.trim(),
        charset: mysqlForm.charset.trim() || 'utf8mb4',
      };
      // Only include password if it's non-empty (edit mode: leave blank to keep existing)
      if (mysqlForm.password) {
        config.password = mysqlForm.password;
      } else if (!isEdit) {
        config.password = '';
      }
      return config;
    }

    // http_api
    if (!httpForm.base_url.trim()) {
      notify.error('请填写基础 URL');
      return null;
    }
    const config: Record<string, unknown> = {
      base_url: httpForm.base_url.trim(),
      timeout: Number(httpForm.timeout) || 30,
    };
    if (httpForm.headers.trim()) {
      try {
        config.headers = JSON.parse(httpForm.headers.trim());
      } catch {
        notify.error('请求头必须是合法的 JSON 格式');
        return null;
      }
    }
    return config;
  }

  async function handleTestConnection() {
    if (!dataSource) return;
    setTesting(true);
    try {
      const result = await dataSourcesApi.test(dataSource.id);
      if (result.success) {
        notify.success('连接成功');
      } else {
        notify.error(result.message || '连接失败');
      }
    } catch (err) {
      notify.error(err instanceof Error ? err.message : '测试连接失败');
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!baseForm.name.trim()) {
      notify.error('请填写名称');
      return;
    }

    const configJson = buildConfigJson();
    if (!configJson) return;

    const allowedTables = baseForm.allowed_tables
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

    setSaving(true);
    try {
      if (isEdit && dataSource) {
        await dataSourcesApi.update(dataSource.id, {
          name: baseForm.name.trim(),
          description: baseForm.description.trim() || undefined,
          type: baseForm.type,
          config_json: configJson,
          read_only: baseForm.read_only,
          status: baseForm.status,
          allowed_tables_json: allowedTables,
        });
      } else {
        await dataSourcesApi.create({
          name: baseForm.name.trim(),
          description: baseForm.description.trim() || undefined,
          type: baseForm.type,
          config_json: configJson,
          read_only: baseForm.read_only,
          status: baseForm.status,
          allowed_tables_json: allowedTables,
        });
      }
      notify.success(isEdit ? '保存成功' : '创建成功');

      onSaved();
      onOpenChange(false);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  const TypeIcon = baseForm.type === 'mysql' ? Database : Globe;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        aria-describedby={undefined}
        className="flex max-h-[90vh] w-[50vw] max-w-[50vw] sm:max-w-[50vw] md:max-w-[50vw] lg:max-w-[50vw] flex-col overflow-hidden rounded-[16px] p-0 shadow-2xl"
        style={{ width: '50vw', maxWidth: '50vw' }}
      >
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between border-b border-[#eceef1] bg-[#fafbfc] px-[24px] py-[16px]">
          <div className="flex items-center gap-[10px]">
            <div className="flex size-[32px] items-center justify-center rounded-[8px] bg-[#f1f3f7] text-[#18181a]">
              <TypeIcon className="size-[16px]" />
            </div>
            <div>
              <div className="flex items-center gap-[8px]">
                <DialogTitle className="text-[15px] font-semibold text-[#18181a]">
                  {isEdit ? '编辑数据源' : '新建数据源'}
                </DialogTitle>
                {isEdit && dataSource && (
                  <span className="font-mono text-[11px] text-[#858b9c]">
                    ({dataSource.id})
                  </span>
                )}
              </div>
              <p className="mt-[2px] text-[12px] text-[#858b9c]">
                配置底层数据库连接信息与访问限制规则
              </p>
            </div>
          </div>
        </div>

        {/* 内容主体：左右双栏布局 */}
        <div className="flex-1 overflow-y-auto px-[24px] py-[20px]">
          <div className="grid grid-cols-1 items-start gap-[24px] md:grid-cols-2">
            {/* 左侧：基础信息 */}
            <div className="flex flex-col gap-[16px] rounded-[12px] border border-[#f0f2f5] bg-[#fafbfc]/60 p-[18px]">
              <SectionTitle>基础信息</SectionTitle>

              <LabeledField label="名称" required>
                <Input
                  value={baseForm.name}
                  maxLength={100}
                  placeholder="请输入数据源名称"
                  onChange={(e) => updateBase('name', e.target.value)}
                />
              </LabeledField>

              <div className="grid grid-cols-2 gap-[12px]">
                <LabeledField label="类型">
                  <Select
                    value={baseForm.type}
                    onValueChange={(v) => updateBase('type', v as 'mysql' | 'http_api')}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="mysql">MySQL</SelectItem>
                      <SelectItem value="http_api">HTTP API</SelectItem>
                    </SelectContent>
                  </Select>
                </LabeledField>

                <LabeledField label="状态">
                  <Select
                    value={baseForm.status}
                    onValueChange={(v) => updateBase('status', v as 'active' | 'inactive')}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="active">正常</SelectItem>
                      <SelectItem value="inactive">停用</SelectItem>
                    </SelectContent>
                  </Select>
                </LabeledField>
              </div>

              <LabeledField label="描述">
                <Textarea
                  value={baseForm.description}
                  maxLength={500}
                  rows={4}
                  placeholder="请输入描述（选填）"
                  onChange={(e) => updateBase('description', e.target.value)}
                />
              </LabeledField>

              <LabeledField label="只读模式">
                <div className="flex items-center justify-between rounded-[8px] border border-[#eceef1] bg-white p-[10px]">
                  <div className="flex flex-col">
                    <span className="text-[12px] font-medium text-[#18181a]">
                      只读保护模式
                    </span>
                    <span className="text-[11px] text-[#858b9c]">
                      开启后仅允许 SELECT 查询，防止误修改数据
                    </span>
                  </div>
                  <div className="flex items-center gap-[8px]">
                    <Switch
                      checked={baseForm.read_only}
                      onCheckedChange={(v) => updateBase('read_only', v)}
                    />
                    <span className="text-[12px] text-[#858b9c]">
                      {baseForm.read_only ? '已开启' : '已关闭'}
                    </span>
                  </div>
                </div>
              </LabeledField>

              {baseForm.type === 'mysql' && (
                <LabeledField label="数据表白名单（逗号分隔，选填）">
                  <Input
                    value={baseForm.allowed_tables}
                    placeholder="如 orders, order_items（留空表示允许全部表）"
                    onChange={(e) => updateBase('allowed_tables', e.target.value)}
                  />
                  <span className="text-[11px] text-[#858b9c]">
                    指定此数据源允许查询的数据表清单，留空则不限制
                  </span>
                </LabeledField>
              )}
            </div>

            {/* 右侧：连接配置 */}
            <div className="flex flex-col gap-[16px] rounded-[12px] border border-[#f0f2f5] bg-[#fafbfc]/60 p-[18px]">
              {baseForm.type === 'mysql' && (
                <>
                  <SectionTitle>MySQL 配置</SectionTitle>

                  <div className="grid grid-cols-2 gap-[12px]">
                    <LabeledField label="主机" required>
                      <Input
                        value={mysqlForm.host}
                        placeholder="127.0.0.1"
                        onChange={(e) => updateMysql('host', e.target.value)}
                      />
                    </LabeledField>

                    <LabeledField label="端口" required>
                      <Input
                        type="number"
                        value={mysqlForm.port}
                        placeholder="3306"
                        onChange={(e) => updateMysql('port', e.target.value)}
                      />
                    </LabeledField>
                  </div>

                  <div className="grid grid-cols-2 gap-[12px]">
                    <LabeledField label="数据库" required>
                      <Input
                        value={mysqlForm.database}
                        placeholder="请输入数据库名"
                        onChange={(e) => updateMysql('database', e.target.value)}
                      />
                    </LabeledField>

                    <LabeledField label="字符集">
                      <Input
                        value={mysqlForm.charset}
                        placeholder="utf8mb4"
                        onChange={(e) => updateMysql('charset', e.target.value)}
                      />
                    </LabeledField>
                  </div>

                  <div className="grid grid-cols-2 gap-[12px]">
                    <LabeledField label="用户名" required>
                      <Input
                        value={mysqlForm.username}
                        placeholder="请输入用户名"
                        onChange={(e) => updateMysql('username', e.target.value)}
                      />
                    </LabeledField>

                    <LabeledField label="密码">
                      <Input
                        type="password"
                        value={mysqlForm.password}
                        placeholder={isEdit ? '不修改请留空' : '请输入密码'}
                        onChange={(e) => updateMysql('password', e.target.value)}
                      />
                    </LabeledField>
                  </div>

                  <div className="flex items-start gap-[8px] rounded-[8px] bg-[#f0fdf4] p-[10px] text-[11px] text-[#166534]">
                    <ShieldCheck className="mt-[1px] size-[14px] shrink-0 text-[#16a34a]" />
                    <span>
                      安全提示：数据库凭据通过平台安全加密存储，仅在执行查询时由后端安全加载。
                    </span>
                  </div>
                </>
              )}

              {baseForm.type === 'http_api' && (
                <>
                  <SectionTitle>HTTP API 配置</SectionTitle>

                  <LabeledField label="基础 URL" required>
                    <Input
                      value={httpForm.base_url}
                      placeholder="https://api.example.com"
                      onChange={(e) => updateHttp('base_url', e.target.value)}
                    />
                  </LabeledField>

                  <LabeledField label="请求头">
                    <Textarea
                      value={httpForm.headers}
                      rows={5}
                      placeholder='{"Authorization": "Bearer xxx"}'
                      onChange={(e) => updateHttp('headers', e.target.value)}
                    />
                    <span className="text-[11px] text-[#858b9c]">
                      JSON 格式，选填
                    </span>
                  </LabeledField>

                  <LabeledField label="超时时间（秒）">
                    <Input
                      type="number"
                      value={httpForm.timeout}
                      placeholder="30"
                      onChange={(e) => updateHttp('timeout', e.target.value)}
                    />
                  </LabeledField>
                </>
              )}
            </div>
          </div>
        </div>

        {/* 底部操作栏 */}
        <div className="flex items-center justify-between border-t border-[#eceef1] bg-[#fafbfc] px-[24px] py-[14px]">
          <div>
            {isEdit && dataSource && (
              <Button
                variant="outline"
                size="sm"
                type="button"
                disabled={testing || saving}
                onClick={() => void handleTestConnection()}
                className="h-[32px] gap-[4px] text-[12px] text-[#464c5e]"
              >
                {testing ? (
                  <Loader2 className="size-[12px] animate-spin" />
                ) : (
                  <FlaskConical className="size-[12px]" />
                )}
                测试连接
              </Button>
            )}
          </div>

          <div className="flex items-center gap-[10px]">
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => onOpenChange(false)}
              className="h-[32px] w-[80px] rounded-[10px] border-[#e3e7f1] bg-white px-[12px] text-[14px] font-normal text-[#464c5e] hover:border-[#e3e7f1] hover:bg-[#f6f6f6] hover:text-[#18181a]"
            >
              取消
            </Button>
            <Button
              disabled={saving}
              onClick={() => void handleSave()}
              className="h-[32px] min-w-[80px] rounded-[10px] bg-[#18181a] px-[16px] text-[14px] font-normal text-white hover:bg-[#303030]"
            >
              {saving ? '保存中...' : '保存'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="text-[13px] font-semibold text-[#18181a]">{children}</div>
  );
}

function LabeledField({
  label,
  children,
  required,
}: {
  label: string;
  children: ReactNode;
  required?: boolean;
}) {
  return (
    <label className="flex flex-col gap-[6px]">
      <span className="text-[12px] font-medium text-[#464c5e]">
        {label}
        {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
      </span>
      {children}
    </label>
  );
}
