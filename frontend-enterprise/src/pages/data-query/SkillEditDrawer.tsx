import { useEffect, useState, useMemo } from 'react';
import {
  Database,
  FileCode2,
  Layers,
  Loader2,
  Save,
  Sparkles,
  X,
  History,
} from 'lucide-react';

import {
  Button,
  Input,
  Label,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  Textarea,
  notify,
} from '@/components/ui';
import {
  queryTemplatesApi,
  type QueryTemplate,
  type QueryTemplateVersion,
} from '@/api/data-query';
import SqlEditor from './SqlEditor';
import ParamsConfigPanel, { type QueryParam } from './ParamsConfigPanel';
import TestRunPanel from './TestRunPanel';
import SchemaExplorer from './SchemaExplorer';

export interface SkillEditDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  template?: QueryTemplate | null;
  dataSourceId?: string | null;
  toolId?: string | null;
  onSaved?: (savedTemplate: QueryTemplate) => void;
}

export default function SkillEditDrawer({
  open,
  onOpenChange,
  template,
  dataSourceId,
  toolId,
  onSaved,
}: SkillEditDrawerProps) {
  const isEdit = Boolean(template);
  const effectiveDsId = template?.data_source_id || dataSourceId || '';

  // 表单状态
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [businessNotes, setBusinessNotes] = useState('');
  const [queryContent, setQueryContent] = useState('');
  const [params, setParams] = useState<QueryParam[]>([]);
  const [saving, setSaving] = useState(false);

  // 侧栏 SchemaExplorer 展开状态
  const [showSchema, setShowSchema] = useState(false);

  // 版本历史列表
  const [versions, setVersions] = useState<QueryTemplateVersion[]>([]);
  const [showVersions, setShowVersions] = useState(false);
  const [loadingVersions, setLoadingVersions] = useState(false);

  // 初始化或切换模板时同步表单
  useEffect(() => {
    if (!open) return;
    if (template) {
      setName(template.name);
      setDescription(template.description || '');
      setBusinessNotes(template.business_notes || '');
      setQueryContent(template.query_content || '');
      setParams(
        (template.params_json || []).map((p: Record<string, unknown>) => ({
          name: String(p.name || ''),
          type: String(p.type || 'string'),
          required: Boolean(p.required),
          default: p.default ?? '',
          description: String(p.description || ''),
          enum: Array.isArray(p.enum) ? (p.enum as string[]) : null,
        }))
      );
      // 加载历史版本
      setLoadingVersions(true);
      queryTemplatesApi
        .getVersions(template.id)
        .then(setVersions)
        .catch(() => setVersions([]))
        .finally(() => setLoadingVersions(false));
    } else {
      setName('');
      setDescription('');
      setBusinessNotes('');
      setQueryContent('SELECT * FROM \nWHERE 1=1\nLIMIT 20');
      setParams([]);
      setVersions([]);
    }
  }, [open, template]);

  const isActiveSkill = template?.status === 'active';

  // 保存处理
  const handleSave = async () => {
    if (!name.trim()) {
      notify.error('请输入技能名称');
      return;
    }
    if (!effectiveDsId) {
      notify.error('未指定关联数据源');
      return;
    }
    if (!queryContent.trim()) {
      notify.error('请输入 SQL 查询内容');
      return;
    }

    setSaving(true);
    try {
      let saved: QueryTemplate;
      if (isEdit && template) {
        saved = await queryTemplatesApi.update(template.id, {
          name: name.trim(),
          description: description.trim() || undefined,
          business_notes: businessNotes.trim() || undefined,
          query_content: queryContent,
          params_json: params.map((p) => ({
            name: p.name,
            type: p.type,
            required: p.required,
            default: p.default,
            description: p.description,
            enum: p.enum,
          })),
        });

        if (isActiveSkill && saved.id !== template.id) {
          notify.success(
            `已对原启用技能生成草稿副本 (${saved.name})，进入待审池等待转正审批。原版本继续保持稳定在线。`
          );
        } else {
          notify.success('技能保存成功');
        }
      } else {
        saved = await queryTemplatesApi.create({
          name: name.trim(),
          description: description.trim() || undefined,
          data_source_id: effectiveDsId,
          query_type: 'sql',
          query_content: queryContent,
          status: 'draft',
          params_json: params.map((p) => ({
            name: p.name,
            type: p.type,
            required: p.required,
            default: p.default,
            description: p.description,
            enum: p.enum,
          })),
        });
        notify.success('技能草稿创建成功');
      }

      onSaved?.(saved);
      onOpenChange(false);
    } catch (err) {
      notify.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-4xl lg:max-w-5xl p-0 flex flex-col h-full bg-[#fcfcfd]"
      >
        {/* 头部导航与标题 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#eceef1] bg-white">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-[#0066cc] shrink-0">
              <FileCode2 className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <SheetTitle className="text-base font-semibold text-[#18181a] truncate">
                  {isEdit ? `编辑技能: ${name || template?.name}` : '新建数据查询技能'}
                </SheetTitle>
                {template && (
                  <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-[#e8f0fe] text-[#1a73e8]">
                    v{template.evolution_version || 1}
                  </span>
                )}
                {isActiveSkill && (
                  <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded">
                    在线服务中 · 编辑将落草稿副本
                  </span>
                )}
              </div>
              <SheetDescription className="text-xs text-muted-foreground mt-0.5">
                支持参数化 SQL 编写、库表实时预览与无保存直接试跑
              </SheetDescription>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {effectiveDsId && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSchema(!showSchema)}
                className={`text-xs gap-1.5 ${showSchema ? 'border-blue-500 bg-blue-50 text-blue-700' : ''}`}
              >
                <Database className="h-3.5 w-3.5" />
                <span>{showSchema ? '收起表结构' : '浏览库表结构'}</span>
              </Button>
            )}
            {isEdit && versions.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowVersions(!showVersions)}
                className="text-xs gap-1 text-muted-foreground"
                title="查看版本历史"
              >
                <History className="h-3.5 w-3.5" />
                <span>版本 ({versions.length})</span>
              </Button>
            )}
          </div>
        </div>

        {/* 主编辑区 */}
        <div className="flex-1 flex overflow-hidden">
          {/* 中间主要表单与代码区 */}
          <div className="flex-1 overflow-y-auto p-6 space-y-5">
            {/* 基础信息 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-white p-4 rounded-xl border border-[#eceef1]">
              <div className="space-y-1.5">
                <Label className="text-xs font-medium text-[#18181a]">
                  技能名称 <span className="text-red-500">*</span>
                </Label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="如: daily_sales_by_store"
                  className="h-8 text-xs font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs font-medium text-[#18181a]">业务口径说明</Label>
                <Input
                  value={businessNotes}
                  onChange={(e) => setBusinessNotes(e.target.value)}
                  placeholder="如: 统计各门店支付净额，排除退款与优惠券"
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1.5 md:col-span-2">
                <Label className="text-xs font-medium text-[#18181a]">描述</Label>
                <Textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  placeholder="请输入该查询技能的详细说明，供数字员工意图理解..."
                  className="text-xs"
                />
              </div>
            </div>

            {/* SQL 编辑器 */}
            <SqlEditor
              queryType="sql"
              queryContent={queryContent}
              onChange={setQueryContent}
            />

            {/* 参数识别与配置面板 */}
            <ParamsConfigPanel
              params={params}
              onChange={setParams}
              queryContent={queryContent}
              queryType="sql"
            />

            {/* 测试运行面板（双模式，支持未保存试跑） */}
            <TestRunPanel
              templateId={isEdit && !isActiveSkill ? template?.id : null}
              params={params}
              dataSourceId={effectiveDsId}
              queryContent={queryContent}
              queryType="sql"
            />
          </div>

          {/* 右侧抽屉内嵌 Schema 浏览器 */}
          {showSchema && effectiveDsId && (
            <div className="w-80 border-l border-[#eceef1] bg-white p-3 flex flex-col h-full animate-in slide-in-from-right duration-150">
              <SchemaExplorer
                dataSourceId={effectiveDsId}
                onSelectTable={(tname) => {
                  setQueryContent((prev) => `${prev} ${tname}`);
                  notify.info(`已将表名 ${tname} 追加至 SQL`);
                }}
                className="h-full border-0"
              />
            </div>
          )}
        </div>

        {/* 底部操作栏 */}
        <div className="px-6 py-3.5 border-t border-[#eceef1] bg-white flex items-center justify-between">
          <div className="text-xs text-muted-foreground">
            {isActiveSkill ? (
              <span className="text-amber-600 font-medium">
                当前技能处于在线活跃状态，保存将安全派生新版本草稿进入演化待审池。
              </span>
            ) : (
              <span>点击「保存技能」将更新或创建草稿。</span>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={handleSave}
              disabled={saving}
              className="gap-1.5 bg-[#0066cc] hover:bg-[#0052a3]"
            >
              {saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  保存中...
                </>
              ) : (
                <>
                  <Save className="h-3.5 w-3.5" />
                  {isActiveSkill ? '保存为草稿副本' : '保存技能'}
                </>
              )}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
