import { useMemo, useState } from 'react';

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
  notify,
} from '@/components/ui';
import {
  ChevronDown,
  ChevronUp,
  Plus,
  Sparkles,
  Trash2,
  Pencil,
} from 'lucide-react';

const CARD_CLASS = 'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';
const FIELD_LABEL_CLASS = 'text-[13px] font-medium text-[#18181a]';
const HINT_CLASS = 'text-[12px] leading-[1.55] text-[#858b9c]';

// 参数类型选项
const TYPE_OPTIONS = [
  { value: 'string', label: 'string' },
  { value: 'int', label: 'int' },
  { value: 'float', label: 'float' },
  { value: 'bool', label: 'bool' },
  { value: 'date', label: 'date' },
];

// 支持枚举值的类型
const ENUM_SUPPORTED_TYPES = new Set(['string', 'int', 'float']);

// 类型标签配色
const TYPE_BADGE_CLASS: Record<string, string> = {
  string: 'bg-[#e8f0ff] text-[#1a71ff]',
  int: 'bg-[#fff2e5] text-[#ff7f00]',
  float: 'bg-[#fff2e5] text-[#ff7f00]',
  bool: 'bg-[#e9f7ef] text-[#2cb360]',
  date: 'bg-[#f3e8ff] text-[#9333ea]',
};

export interface QueryParam {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
  description: string;
  enum: string[] | null;
}

export interface ParamsConfigPanelProps {
  params: QueryParam[];
  onChange: (params: QueryParam[]) => void;
  queryContent?: string;
  queryType?: string;
}

function createEmptyParam(): QueryParam {
  return {
    name: '',
    type: 'string',
    required: false,
    default: '',
    description: '',
    enum: null,
  };
}

// 从查询内容中提取参数名
function extractParamNames(
  queryContent: string,
  queryType: string,
): string[] {
  const names = new Set<string>();
  if (!queryContent) return [];

  if (queryType === 'sql') {
    // SQL 命名参数：:param_name（排除 PostgreSQL 的 ::type 类型转换语法）
    const regex = /(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)/g;
    let match;
    while ((match = regex.exec(queryContent)) !== null) {
      names.add(match[1]);
    }
  } else if (queryType === 'http') {
    // URL 路径参数：{param_name}
    const pathRegex = /\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g;
    let match;
    while ((match = pathRegex.exec(queryContent)) !== null) {
      names.add(match[1]);
    }
    // Query string 中的 :param 格式
    const queryRegex = /:([a-zA-Z_][a-zA-Z0-9_]*)/g;
    while ((match = queryRegex.exec(queryContent)) !== null) {
      names.add(match[1]);
    }
  }

  return Array.from(names);
}

export default function ParamsConfigPanel({
  params,
  onChange,
  queryContent,
  queryType,
}: ParamsConfigPanelProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [formData, setFormData] = useState<QueryParam>(createEmptyParam());
  const [enumText, setEnumText] = useState('');

  const paramCount = params.length;

  const existingNames = useMemo(
    () => new Set(params.map((p) => p.name)),
    [params],
  );

  // 打开新增对话框
  const handleAdd = () => {
    setEditingIndex(null);
    setFormData(createEmptyParam());
    setEnumText('');
    setDialogOpen(true);
  };

  // 打开编辑对话框
  const handleEdit = (index: number) => {
    const param = params[index];
    setEditingIndex(index);
    setFormData({ ...param });
    setEnumText(param.enum ? param.enum.join('\n') : '');
    setDialogOpen(true);
  };

  // 保存参数
  const handleSave = () => {
    const name = formData.name.trim();

    // 校验参数名
    if (!name) {
      notify.error('请输入参数名');
      return;
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) {
      notify.error('参数名仅允许字母、数字、下划线，且首字母不能是数字');
      return;
    }

    // 检查重名
    const isDuplicate = params.some(
      (p, idx) => p.name === name && idx !== editingIndex,
    );
    if (isDuplicate) {
      notify.error(`参数名 "${name}" 已存在`);
      return;
    }

    // 处理枚举值
    let enumValues: string[] | null = null;
    if (ENUM_SUPPORTED_TYPES.has(formData.type) && enumText.trim()) {
      enumValues = enumText
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      if (enumValues.length === 0) enumValues = null;
    }

    // 处理默认值，确保类型与参数类型一致
    let defaultVal: unknown = formData.default;
    if (formData.type === 'int') {
      defaultVal = formData.default === '' ? '' : Number(formData.default);
    } else if (formData.type === 'float') {
      defaultVal = formData.default === '' ? '' : Number(formData.default);
    } else if (formData.type === 'bool') {
      // bool 类型的 default 已经是布尔值（Select 组件已转换），直接使用
      defaultVal = Boolean(formData.default);
    }

    const newParam: QueryParam = {
      ...formData,
      name,
      default: defaultVal,
      enum: enumValues,
    };

    const next = [...params];
    if (editingIndex !== null) {
      next[editingIndex] = newParam;
    } else {
      next.push(newParam);
    }

    onChange(next);
    setDialogOpen(false);
    notify.success(
      editingIndex !== null ? '参数已更新' : '参数已添加',
    );
  };

  // 删除参数
  const handleDelete = (index: number) => {
    const param = params[index];
    const next = params.filter((_, idx) => idx !== index);
    onChange(next);
    notify.success(`已删除参数 "${param.name}"`);
  };

  // 上移
  const handleMoveUp = (index: number) => {
    if (index <= 0) return;
    const next = [...params];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    onChange(next);
  };

  // 下移
  const handleMoveDown = (index: number) => {
    if (index >= params.length - 1) return;
    const next = [...params];
    [next[index], next[index + 1]] = [next[index + 1], next[index]];
    onChange(next);
  };

  // 从 SQL/HTTP 自动提取参数
  const handleExtract = () => {
    if (!queryContent) {
      notify.error('查询内容为空，无法提取参数');
      return;
    }

    const extracted = extractParamNames(queryContent, queryType || 'sql');
    if (extracted.length === 0) {
      notify.info('未检测到参数占位符');
      return;
    }

    // 跳过已存在的
    const newNames = extracted.filter((name) => !existingNames.has(name));
    if (newNames.length === 0) {
      notify.info('所有参数已存在于列表中');
      return;
    }

    const newParams: QueryParam[] = newNames.map((name) => ({
      name,
      type: 'string',
      required: false,
      default: '',
      description: '',
      enum: null,
    }));

    onChange([...params, ...newParams]);
    notify.success(`提取到 ${newParams.length} 个新参数`);
  };

  // 格式化默认值显示
  const formatDefault = (param: QueryParam): string => {
    if (param.default === '' || param.default === null || param.default === undefined) {
      return '—';
    }
    if (param.type === 'bool') {
      return param.default ? 'true' : 'false';
    }
    return String(param.default);
  };

  return (
    <section className={CARD_CLASS + ' overflow-hidden'}>
      {/* 标题栏 */}
      <div className="flex min-h-[54px] items-center justify-between gap-[12px] border-b border-[#eceef1] px-[20px] py-[10px]">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-[8px] text-left"
        >
          {collapsed ? (
            <ChevronDown className="size-4 shrink-0 text-[#858b9c]" />
          ) : (
            <ChevronUp className="size-4 shrink-0 text-[#858b9c]" />
          )}
          <span className={'truncate ' + CARD_TITLE_CLASS}>
            参数配置
          </span>
          <Badge variant="secondary" className="shrink-0">
            {paramCount}
          </Badge>
        </button>
        <div className="flex shrink-0 items-center gap-[6px]">
          <Button
            variant="outline"
            size="xs"
            onClick={handleExtract}
            disabled={!queryContent}
            title="从查询内容中自动提取参数"
          >
            <Sparkles className="size-3.5" />
            <span className="hidden sm:inline">提取参数</span>
          </Button>
          <Button
            variant="default"
            size="xs"
            onClick={handleAdd}
          >
            <Plus className="size-3.5" />
            <span className="hidden sm:inline">添加参数</span>
          </Button>
        </div>
      </div>

      {/* 参数列表 */}
      {!collapsed && (
        <div className="flex flex-col gap-[8px] p-[12px]">
          {params.length === 0 ? (
            <div className="py-[24px] text-center text-[12px] text-[#858b9c]">
              暂无参数，点击「添加参数」或「提取参数」开始
            </div>
          ) : (
            <div className="flex flex-col gap-[6px]">
              {params.map((param, index) => (
                <div
                  key={param.name + '-' + index}
                  className="flex items-start gap-[8px] rounded-[10px] border border-[#eceef1] bg-[#fafbfc] p-[10px]"
                >
                  {/* 上移/下移按钮 */}
                  <div className="flex shrink-0 flex-col gap-[2px]">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleMoveUp(index)}
                      disabled={index === 0}
                      className="text-[#858b9c]"
                      title="上移"
                    >
                      <ChevronUp className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleMoveDown(index)}
                      disabled={index === params.length - 1}
                      className="text-[#858b9c]"
                      title="下移"
                    >
                      <ChevronDown className="size-3.5" />
                    </Button>
                  </div>

                  {/* 参数信息 */}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-[6px]">
                      <span className="text-[13px] font-medium text-[#18181a]">
                        {param.name}
                      </span>
                      {param.required && (
                        <span className="text-[13px] text-[#d20b0b]">*</span>
                      )}
                      <Badge
                        variant="outline"
                        className={
                          TYPE_BADGE_CLASS[param.type] ||
                          'bg-[#f2f3f7] text-[#858b9c]'
                        }
                      >
                        {param.type}
                      </Badge>
                    </div>
                    <div className="mt-[4px] text-[11px] text-[#858b9c]">
                      默认值：{formatDefault(param)}
                    </div>
                    {param.description && (
                      <div className="mt-[2px] truncate text-[11px] text-[#858b9c]">
                        {param.description}
                      </div>
                    )}
                    {param.enum && param.enum.length > 0 && (
                      <div className="mt-[2px] truncate text-[11px] text-[#858b9c]">
                        枚举：{param.enum.join(', ')}
                      </div>
                    )}
                  </div>

                  {/* 操作按钮 */}
                  <div className="flex shrink-0 items-center gap-[4px]">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleEdit(index)}
                      className="text-[#858b9c] hover:text-[#1a71ff]"
                      title="编辑"
                    >
                      <Pencil className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleDelete(index)}
                      className="text-[#858b9c] hover:text-[#d20b0b]"
                      title="删除"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 编辑/新增对话框 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[440px]">
          <DialogHeader>
            <DialogTitle>
              {editingIndex !== null ? '编辑参数' : '添加参数'}
            </DialogTitle>
          </DialogHeader>

          <div className="flex flex-col gap-[14px]">
            {/* 参数名 */}
            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="param-name" className={FIELD_LABEL_CLASS}>
                参数名 <span className="text-[#d20b0b]">*</span>
              </Label>
              <Input
                id="param-name"
                value={formData.name}
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, name: e.target.value }))
                }
                placeholder="例如：user_id"
                autoFocus
              />
              <span className={HINT_CLASS}>
                仅允许字母、数字、下划线，首字母不能是数字
              </span>
            </div>

            {/* 类型 + 必填 */}
            <div className="grid grid-cols-2 gap-[12px]">
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="param-type" className={FIELD_LABEL_CLASS}>
                  类型
                </Label>
                <Select
                  value={formData.type}
                  onValueChange={(value) =>
                    setFormData((prev) => {
                      const next = { ...prev, type: value };
                      // 切换到不支持枚举的类型时清空枚举
                      if (!ENUM_SUPPORTED_TYPES.has(value)) {
                        next.enum = null;
                      }
                      // 切换类型时重置默认值
                      if (value === 'bool') {
                        next.default = false;
                      } else {
                        next.default = '';
                      }
                      return next;
                    })
                  }
                >
                  <SelectTrigger id="param-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPE_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-[6px]">
                <Label className={FIELD_LABEL_CLASS}>是否必填</Label>
                <div className="flex h-[32px] items-center">
                  <Switch
                    checked={formData.required}
                    onCheckedChange={(checked) =>
                      setFormData((prev) => ({
                        ...prev,
                        required: checked,
                      }))
                    }
                  />
                  <span className="ml-[8px] text-[12px] text-[#858b9c]">
                    {formData.required ? '是' : '否'}
                  </span>
                </div>
              </div>
            </div>

            {/* 默认值 */}
            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="param-default" className={FIELD_LABEL_CLASS}>
                默认值
              </Label>
              {formData.type === 'bool' ? (
                <Select
                  value={formData.default ? 'true' : 'false'}
                  onValueChange={(value) =>
                    setFormData((prev) => ({
                      ...prev,
                      default: value === 'true',
                    }))
                  }
                >
                  <SelectTrigger id="param-default">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">true</SelectItem>
                    <SelectItem value="false">false</SelectItem>
                  </SelectContent>
                </Select>
              ) : formData.type === 'date' ? (
                <Input
                  id="param-default"
                  type="date"
                  value={String(formData.default || '')}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      default: e.target.value,
                    }))
                  }
                />
              ) : formData.type === 'int' || formData.type === 'float' ? (
                <Input
                  id="param-default"
                  type="number"
                  step={formData.type === 'float' ? 'any' : '1'}
                  value={formData.default as number | string}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      default: e.target.value,
                    }))
                  }
                  placeholder="留空表示无默认值"
                />
              ) : (
                <Input
                  id="param-default"
                  value={String(formData.default || '')}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      default: e.target.value,
                    }))
                  }
                  placeholder="留空表示无默认值"
                />
              )}
            </div>

            {/* 描述 */}
            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="param-desc" className={FIELD_LABEL_CLASS}>
                描述
              </Label>
              <Input
                id="param-desc"
                value={formData.description}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    description: e.target.value,
                  }))
                }
                placeholder="可选，参数的说明文字"
              />
            </div>

            {/* 枚举值 */}
            {ENUM_SUPPORTED_TYPES.has(formData.type) && (
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="param-enum" className={FIELD_LABEL_CLASS}>
                  枚举值
                </Label>
                <Textarea
                  id="param-enum"
                  rows={3}
                  value={enumText}
                  onChange={(e) => setEnumText(e.target.value)}
                  placeholder="每行一个可选值"
                />
                <span className={HINT_CLASS}>
                  限定参数的可选值范围，留空表示不限制
                </span>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button variant="default" onClick={handleSave}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
