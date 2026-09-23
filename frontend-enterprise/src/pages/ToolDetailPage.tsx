import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  ExternalLink,
  FileCode2,
  Layers,
  MessageSquare,
  Play,
  RefreshCw,
  Sparkles,
  Archive,
  Edit,
} from 'lucide-react';

import { api, TENANT_ID } from '../api/client';
import { isEnterpriseAdmin, type EnterpriseAuthUser } from '../auth';
import AppHeader from '@/components/AppHeader';
import { CapabilityScopeBadge } from '@/components/CapabilityScopeControl';
import { StatusBadge } from '@/pages/scheduled-tasks/StatusBadge';
import { Button } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  dataSourcesApi,
  queryTemplatesApi,
  type DataSource,
  type QueryTemplate,
} from '@/api/data-query';
import type { QueryParam } from './data-query/ParamsConfigPanel';
import TestRunPanel from './data-query/TestRunPanel';
import SkillEditDrawer from './data-query/SkillEditDrawer';
import SchemaExplorer from './data-query/SchemaExplorer';
import type { ToolRead } from '../types';
import { cn } from '@/lib/utils';
import { formatDateTime } from '@/lib/enterprise-ui';


interface ToolDetailPageProps {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}

export default function ToolDetailPage({ currentUser, onLogout }: ToolDetailPageProps) {
  const { toolId } = useParams<{ toolId: string }>();
  const navigate = useNavigate();

  const [tool, setTool] = useState<ToolRead | null>(null);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [loadingTool, setLoadingTool] = useState(true);

  const [activeTab, setActiveTab] = useState<'skills' | 'inbox'>('skills');
  const [activeSkills, setActiveSkills] = useState<QueryTemplate[]>([]);
  const [draftSkills, setDraftSkills] = useState<QueryTemplate[]>([]);
  const [loadingSkills, setLoadingSkills] = useState(false);

  // 技能试跑弹窗状态
  const [testModalTemplate, setTestModalTemplate] = useState<QueryTemplate | null>(null);
  // 技能编辑抽屉状态
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<QueryTemplate | null>(null);
  // 库表结构全屏浏览器弹窗状态
  const [schemaExplorerOpen, setSchemaExplorerOpen] = useState(false);

  const isAdmin = useMemo(() => isEnterpriseAdmin(currentUser), [currentUser]);


  // 加载工具与绑定数据源
  const loadTool = async () => {
    if (!toolId) return;
    setLoadingTool(true);
    try {
      const toolData = await api.get<ToolRead>(
        `/api/enterprise/tools/${encodeURIComponent(toolId)}?tenant_id=${encodeURIComponent(TENANT_ID)}`
      );
      setTool(toolData);

      const dsId = toolData.data_source_id || (toolData.mcp_config?.data_source_id as string | undefined);
      if (dsId) {
        try {
          const dsData = await dataSourcesApi.get(dsId);
          setDataSource(dsData);
        } catch {
          // 数据源信息获取失败静默忽略
        }
      }
    } catch (err) {
      notify.error(`加载工具失败: ${String(err)}`);
    } finally {
      setLoadingTool(false);
    }
  };

  // 加载活跃与待审技能
  const loadSkills = async () => {
    if (!toolId) return;
    setLoadingSkills(true);
    try {
      const [actives, drafts] = await Promise.all([
        queryTemplatesApi.listByTool(toolId, 'active'),
        queryTemplatesApi.listByTool(toolId, 'draft'),
      ]);
      setActiveSkills(actives);
      setDraftSkills(drafts);
    } catch (err) {
      notify.error(`加载技能列表失败: ${String(err)}`);
    } finally {
      setLoadingSkills(false);
    }
  };

  useEffect(() => {
    loadTool();
    loadSkills();
  }, [toolId]);

  // 一键审核通过转正
  const handleApprove = async (skill: QueryTemplate) => {
    try {
      await queryTemplatesApi.update(skill.id, { status: 'active' });
      notify.success(`技能已成功转正: 「${skill.name}」已生效`);
      await loadSkills();
    } catch (err) {
      notify.error(`转正失败: ${String(err)}`);
    }
  };

  // 归档/忽略
  const handleArchive = async (skill: QueryTemplate) => {
    try {
      await queryTemplatesApi.update(skill.id, { status: 'archived' });
      notify.info(`已归档: 「${skill.name}」`);
      await loadSkills();
    } catch (err) {
      notify.error(`归档失败: ${String(err)}`);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-[#fafbfc]">
      <AppHeader
        title="工具详情"
        description={tool?.display_name || tool?.name || '数据源工具'}
        userName={currentUser?.username}
        onLogout={onLogout}
      />

      <main className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-6 sm:px-6">
        {/* 顶部导航与操作栏 */}
        <div className="mb-4 flex items-center justify-between">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate('/enterprise/tools')}
            className="flex items-center gap-1.5 text-xs text-[#60677c] hover:bg-white hover:text-[#18181a]"
          >
            <ArrowLeft className="size-3.5" />
            返回工具列表
          </Button>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                loadTool();
                loadSkills();
              }}
              className="flex items-center gap-1.5 text-xs bg-white border-[#e3e7f1]"
            >
              <RefreshCw className={cn('size-3.5', (loadingTool || loadingSkills) && 'animate-spin')} />
              刷新
            </Button>
            {dataSource && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate('/enterprise/data-query')}
                className="flex items-center gap-1.5 text-xs bg-white border-[#e3e7f1]"
              >
                <Database className="size-3.5" />
                数据查询中心
                <ExternalLink className="size-3" />
              </Button>
            )}
          </div>
        </div>

        {/* 头部概览卡片 */}
        <div className="relative mb-6 overflow-hidden rounded-[16px] border border-[#e3e7f1] bg-white p-6 shadow-[0_2px_12px_0_rgba(24,24,26,0.04)]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-4">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-[12px] bg-[#eef4ff] text-[#1a71ff]">
                <Database className="size-6" />
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2.5">
                  <h1 className="text-[18px] font-semibold tracking-tight text-[#18181a]">
                    {tool?.display_name || tool?.name || '数据源查询工具'}
                  </h1>
                  <StatusBadge tone="blue">数据源工具</StatusBadge>
                  <StatusBadge tone={tool?.enabled ? 'green' : 'gray'}>
                    {tool?.enabled ? '已启用' : '已停用'}
                  </StatusBadge>
                  {tool?.capability_scope && (
                    <CapabilityScopeBadge value={tool.capability_scope} />
                  )}
                </div>
                <p className="mt-1 text-xs text-[#858b9c]">
                  标识：<code className="rounded bg-[#f4f6fa] px-1.5 py-0.5 font-mono text-[11px] text-[#464c5e]">{tool?.name}</code>
                  {tool?.updated_at && <span className="ml-3">更新于：{formatDateTime(tool.updated_at)}</span>}
                </p>
                <p className="mt-2 text-xs leading-relaxed text-[#555a6d]">
                  {tool?.description || '统一挂载与管理数据库只读 SQL 查询技能，支持智能问答查数与自进化沉淀。'}
                </p>
              </div>
            </div>

            {/* 绑定数据源信息 */}
            {dataSource && (
              <div className="rounded-[12px] border border-[#eef1f6] bg-[#f8fafc] p-3.5 sm:w-[320px]">
                <div className="flex items-center justify-between text-xs font-medium text-[#464c5e]">
                  <span>关联底座数据源</span>
                  <StatusBadge tone="green">只读受控</StatusBadge>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <span className="font-medium text-xs text-[#18181a]">{dataSource.name}</span>
                  <span className="text-[11px] uppercase tracking-wider text-[#858b9c]">({dataSource.type})</span>
                </div>
                <p className="mt-1 line-clamp-1 text-[11px] text-[#858b9c]">
                  {dataSource.description || '当前租户已授权数据连接'}
                </p>
                <div className="mt-2.5 flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => setSchemaExplorerOpen(true)}
                    className="h-6 text-[11px] gap-1 text-[#1a71ff] border-[#d9e7ff] bg-white hover:bg-blue-50"
                  >
                    <Database className="size-3" />
                    浏览库表结构
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => navigate('/enterprise/data-query')}
                    className="h-6 text-[11px] gap-1 text-muted-foreground"
                  >
                    <ExternalLink className="size-3" />
                    数据中心
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>


        {/* Tab 切换栏 */}
        <div className="mb-6 flex border-b border-[#eef0f4]">
          <button
            type="button"
            onClick={() => setActiveTab('skills')}
            className={cn(
              'flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition-colors',
              activeTab === 'skills'
                ? 'border-[#1a71ff] text-[#1a71ff]'
                : 'border-transparent text-[#60677c] hover:text-[#18181a]'
            )}
          >
            <Layers className="size-4" />
            活跃技能库 (Active Skills)
            <span className={cn(
              'ml-1 rounded-full px-2 py-0.5 text-[11px]',
              activeTab === 'skills' ? 'bg-[#eef4ff] text-[#1a71ff]' : 'bg-[#f0f2f5] text-[#858b9c]'
            )}>
              {activeSkills.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('inbox')}
            className={cn(
              'flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition-colors',
              activeTab === 'inbox'
                ? 'border-[#1a71ff] text-[#1a71ff]'
                : 'border-transparent text-[#60677c] hover:text-[#18181a]'
            )}
          >
            <Sparkles className="size-4" />
            自进化待审池 (Evolution Inbox)
            <span className={cn(
              'ml-1 rounded-full px-2 py-0.5 text-[11px]',
              draftSkills.length > 0 ? 'bg-[#fff1e5] text-[#d9730d] font-semibold' : 'bg-[#f0f2f5] text-[#858b9c]'
            )}>
              {draftSkills.length}
            </span>
          </button>
        </div>

        {/* Tab 1: 活跃技能库 */}
        {activeTab === 'skills' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-muted-foreground">
                已启用并对外暴露为确定性子能力的 SQL 查询技能清单 ({activeSkills.length})
              </div>
              <Button
                variant="default"
                size="sm"
                onClick={() => {
                  setEditingTemplate(null);
                  setDrawerOpen(true);
                }}
                className="h-8 text-xs gap-1.5 bg-[#1a71ff] hover:bg-[#155bd5] text-white"
              >
                <FileCode2 className="size-3.5" />
                新建查询技能
              </Button>
            </div>

            {loadingSkills ? (
              <div className="flex items-center justify-center py-12">
                <RefreshCw className="size-6 animate-spin text-[#858b9c]" />
              </div>
            ) : activeSkills.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-[16px] border border-dashed border-[#d8dde8] bg-white py-14 text-center">
                <div className="flex size-12 items-center justify-center rounded-full bg-[#f4f6fa] text-[#858b9c]">
                  <FileCode2 className="size-6" />
                </div>
                <h3 className="mt-3 text-sm font-medium text-[#18181a]">暂无活跃 SQL 技能</h3>
                <p className="mt-1 text-xs text-[#858b9c] max-w-sm">
                  该数据源工具下尚未挂载任何活跃状态的查询技能。您可以在数据查询中心配置，或等待员工自然语言提问自动沉淀。
                </p>
                <Button
                  size="sm"
                  onClick={() => navigate('/enterprise/data-query')}
                  className="mt-4 bg-[#18181a] text-xs text-white"
                >
                  去数据查询中心创建
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {activeSkills.map((skill) => {
                  const paramsList = Array.isArray(skill.params_json) ? skill.params_json : [];
                  const dimensions = Array.isArray(skill.dimensions_json) ? skill.dimensions_json : [];
                  return (
                    <div
                      key={skill.id}
                      className="flex flex-col justify-between rounded-[14px] border border-[#e3e7f1] bg-white p-5 shadow-[0_2px_8px_0_rgba(24,24,26,0.02)] transition-shadow hover:shadow-[0_4px_16px_0_rgba(24,24,26,0.06)]"
                    >
                      <div>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h3 className="truncate text-[14px] font-semibold text-[#18181a]" title={skill.name}>
                              {skill.name}
                            </h3>
                            <p className="mt-0.5 text-[12px] text-[#858b9c] line-clamp-1">
                              {skill.description || '无描述'}
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <StatusBadge tone="green">active</StatusBadge>
                            <span className="rounded bg-[#f0f2f5] px-1.5 py-0.5 font-mono text-[10px] text-[#60677c]">
                              v{skill.evolution_version || 1}
                            </span>
                          </div>
                        </div>

                        {/* 维度与参数预览 */}
                        <div className="mt-3 flex flex-wrap items-center gap-1.5">
                          <span className="text-[11px] text-[#858b9c]">参数:</span>
                          {paramsList.length === 0 ? (
                            <span className="text-[11px] text-[#a0a6b5]">无</span>
                          ) : (
                            paramsList.map((p, idx) => (
                              <span
                                key={idx}
                                className="rounded bg-[#f4f6fa] px-1.5 py-0.5 font-mono text-[11px] text-[#464c5e]"
                              >
                                {String(p.name || '')}
                              </span>
                            ))
                          )}
                          {dimensions.length > 0 && (
                            <>
                              <span className="ml-2 text-[11px] text-[#858b9c]">维度:</span>
                              {dimensions.map((dim, idx) => (
                                <span
                                  key={idx}
                                  className="rounded bg-[#eef4ff] px-1.5 py-0.5 text-[11px] text-[#1a71ff]"
                                >
                                  {dim}
                                </span>
                              ))}
                            </>
                          )}
                        </div>

                        {/* SQL 片段 */}
                        <div className="mt-3 rounded-[8px] bg-[#1a1c23] p-3 font-mono text-[11px] text-[#e0e3eb]">
                          <p className="line-clamp-2 select-all overflow-hidden break-all">
                            {skill.query_content}
                          </p>
                        </div>
                      </div>

                      {/* 卡片底部操作按钮 */}
                      <div className="mt-4 flex items-center justify-end gap-2 border-t border-[#f0f2f5] pt-3">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setTestModalTemplate(skill)}
                          className="flex items-center gap-1 text-xs border-[#e3e7f1] text-[#1a71ff] hover:bg-[#eef4ff]"
                        >
                          <Play className="size-3" />
                          试跑
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setEditingTemplate(skill);
                            setDrawerOpen(true);
                          }}
                          className="flex items-center gap-1 text-xs border-[#e3e7f1] text-[#464c5e] hover:bg-slate-50"
                        >
                          <Edit className="size-3" />
                          编辑 SQL
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}


        {/* Tab 2: 自进化待审池 */}
        {activeTab === 'inbox' && (
          <div className="space-y-4">
            {/* 说明横幅 */}
            <div className="rounded-[12px] border border-[#d9e7ff] bg-[#f0f6ff] p-4 text-xs text-[#3056a0]">
              <div className="flex items-start gap-2.5">
                <Sparkles className="size-4 shrink-0 text-[#1a71ff] mt-0.5" />
                <div className="space-y-1 leading-relaxed">
                  <strong className="font-semibold text-[#18181a]">自进化待审机制说明：</strong>
                  <p>
                    当业务人员在渠道或控制台通过自然语言提问，但未命中已有模板时，自进化引擎会结合表结构与业务口径自动提炼带参 SQL，并在此处生成待审草稿。
                    管理员确认口径无误后点击「一键启用」，即可正式沉淀为对外暴露的高频技能。
                  </p>
                </div>
              </div>
            </div>

            {draftSkills.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-[16px] border border-dashed border-[#d8dde8] bg-white py-14 text-center">
                <div className="flex size-12 items-center justify-center rounded-full bg-[#f4f6fa] text-[#858b9c]">
                  <Sparkles className="size-6" />
                </div>
                <h3 className="mt-3 text-sm font-medium text-[#18181a]">待审池当前为空</h3>
                <p className="mt-1 text-xs text-[#858b9c] max-w-sm">
                  暂无待审核的自进化技能。当系统运行时收到新的查数诉求，提炼的草稿将自动沉淀至此。
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {draftSkills.map((skill) => {
                  const dimensions = Array.isArray(skill.dimensions_json) ? skill.dimensions_json : [];
                  const metrics = Array.isArray(skill.metrics_json) ? skill.metrics_json : [];
                  return (
                    <div
                      key={skill.id}
                      className="rounded-[14px] border border-[#e3e7f1] bg-white p-5 shadow-[0_2px_8px_0_rgba(24,24,26,0.03)]"
                    >
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-[14px] font-semibold text-[#18181a]">{skill.name}</h3>
                            <StatusBadge tone="orange">待审核草稿</StatusBadge>
                            <span className="rounded bg-[#f0f2f5] px-1.5 py-0.5 font-mono text-[10px] text-[#60677c]">
                              v{skill.evolution_version || 1}
                            </span>
                          </div>
                          {skill.description && (
                            <p className="mt-1 text-xs text-[#858b9c]">{skill.description}</p>
                          )}
                        </div>

                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            onClick={() => handleApprove(skill)}
                            className="flex items-center gap-1.5 bg-[#18181a] text-xs text-white hover:bg-black"
                          >
                            <CheckCircle2 className="size-3.5 text-emerald-400" />
                            一键启用 (Approve)
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setEditingTemplate(skill);
                              setDrawerOpen(true);
                            }}
                            className="flex items-center gap-1 text-xs border-[#e3e7f1] text-[#464c5e]"
                          >
                            <Edit className="size-3" />
                            修改口径
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setTestModalTemplate(skill)}
                            className="flex items-center gap-1 text-xs border-[#e3e7f1] text-[#464c5e]"
                          >
                            <Play className="size-3" />
                            试查
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleArchive(skill)}
                            className="flex items-center gap-1 text-xs text-[#e5484d] hover:bg-[#fff0f0]"
                          >
                            <Archive className="size-3" />
                            归档
                          </Button>
                        </div>
                      </div>


                      {/* 来源自然语言问法 */}
                      {skill.origin_nl && (
                        <div className="mt-3 flex items-center gap-2 rounded-[8px] bg-[#f8fafc] px-3 py-2 text-xs text-[#464c5e]">
                          <MessageSquare className="size-3.5 text-[#1a71ff] shrink-0" />
                          <span className="font-medium text-[#18181a]">来源提问：</span>
                          <span className="italic">{skill.origin_nl}</span>
                        </div>
                      )}

                      {/* 业务备注与提炼口径 */}
                      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                        {dimensions.length > 0 && (
                          <div className="flex items-center gap-1">
                            <span className="text-[#858b9c]">分析维度:</span>
                            {dimensions.map((d, i) => (
                              <span key={i} className="rounded bg-[#eef4ff] px-1.5 py-0.5 text-[11px] text-[#1a71ff]">
                                {d}
                              </span>
                            ))}
                          </div>
                        )}
                        {metrics.length > 0 && (
                          <div className="flex items-center gap-1">
                            <span className="text-[#858b9c]">度量指标:</span>
                            {metrics.map((m, i) => (
                              <span key={i} className="rounded bg-[#ecfdf5] px-1.5 py-0.5 text-[11px] text-[#059669]">
                                {m}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* SQL 预览 */}
                      <div className="mt-3 rounded-[8px] bg-[#1a1c23] p-3 font-mono text-xs text-[#e0e3eb]">
                        <pre className="overflow-x-auto whitespace-pre-wrap leading-relaxed">
                          {skill.query_content}
                        </pre>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* 试跑弹窗 */}
        {testModalTemplate && (
          <Dialog open={Boolean(testModalTemplate)} onOpenChange={(open) => !open && setTestModalTemplate(null)}>
            <DialogContent className="max-w-[760px]">
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2 text-sm">
                  <Play className="size-4 text-[#1a71ff]" />
                  技能试跑：{testModalTemplate.name}
                </DialogTitle>
                <DialogDescription className="text-xs text-[#858b9c]">
                  输入测试参数并执行只读查询，即时校验 SQL 返回结果与耗时。
                </DialogDescription>
              </DialogHeader>

              <div className="py-2">
                <TestRunPanel
                  templateId={testModalTemplate.id}
                  params={(Array.isArray(testModalTemplate.params_json) ? testModalTemplate.params_json : []) as unknown as QueryParam[]}
                />
              </div>
            </DialogContent>
          </Dialog>
        )}

        {/* 技能抽屉式工作台 */}
        <SkillEditDrawer
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          template={editingTemplate}
          dataSourceId={dataSource?.id}
          toolId={toolId}
          onSaved={async () => {
            await loadSkills();
          }}
        />

        {/* 库表结构全屏/大弹窗浏览 */}
        {dataSource && (
          <Dialog open={schemaExplorerOpen} onOpenChange={setSchemaExplorerOpen}>
            <DialogContent className="max-w-4xl max-h-[85vh] h-[650px] p-6 flex flex-col">
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2 text-base">
                  <Database className="size-4 text-[#0066cc]" />
                  <span>数据源库表结构: {dataSource.name}</span>
                  <span className="text-xs font-normal text-muted-foreground">
                    ({dataSource.type} · 只读连接)
                  </span>
                </DialogTitle>
                <DialogDescription>
                  浏览该数据源所允许访问的数据表、字段类型及样例数据。
                </DialogDescription>
              </DialogHeader>
              <div className="flex-1 overflow-hidden mt-2">
                <SchemaExplorer dataSourceId={dataSource.id} className="h-full border-0" />
              </div>
            </DialogContent>
          </Dialog>
        )}
      </main>
    </div>
  );
}

