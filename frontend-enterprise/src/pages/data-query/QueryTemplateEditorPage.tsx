import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import AppHeader from '@/components/AppHeader';
import { Button } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { EnterpriseRoute } from '@/enums/routes';
import type { EnterpriseAuthUser } from '@/auth';
import {
  queryTemplatesApi,
  type QueryTemplate,
} from '@/api/data-query';
import QueryTemplateEditor from './QueryTemplateEditor';
import IconArrowRight from '@/assets/icons/arrow-right.svg?react';

interface Props {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}

export default function QueryTemplateEditorPage({ currentUser, onLogout }: Props) {
  const navigate = useNavigate();
  const { templateId } = useParams();

  const isNew = !templateId || templateId === 'new';
  const [template, setTemplate] = useState<QueryTemplate | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // 编辑器内容变更后标记脏状态；加载完成/保存成功后复位
  const handleTemplateChange = (next: QueryTemplate | null) => {
    setTemplate(next);
    setDirty(true);
  };

  // 有未保存变更时，关闭/刷新页面前提示
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // 编辑模式：加载模板数据
  useEffect(() => {
    if (isNew || !templateId) {
      setTemplate(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    queryTemplatesApi
      .get(templateId)
      .then((data) => {
        if (cancelled) return;
        setTemplate(data);
      })
      .catch((error) => {
        if (cancelled) return;
        notify.error(error instanceof Error ? error.message : '加载查询模板失败');
        navigate(EnterpriseRoute.DataQuery + '?tab=query-templates');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isNew, templateId, navigate]);

  const handleBack = () => {
    // 有历史记录则返回上一页，否则回列表页
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate(EnterpriseRoute.DataQuery + '?tab=query-templates');
    }
  };

  const handleSave = async () => {
    if (!template) {
      notify.error('模板数据为空');
      return;
    }

    // 校验
    if (!template.name.trim()) {
      notify.error('请填写名称');
      return;
    }
    if (!template.data_source_id) {
      notify.error('请选择数据源');
      return;
    }
    if (!template.query_content.trim()) {
      notify.error('请填写查询内容');
      return;
    }

    setSaving(true);
    try {
      // 构建请求体，确保字段类型正确
      const payload = {
        name: template.name.trim(),
        description: template.description,
        data_source_id: template.data_source_id,
        query_type: template.query_type,
        query_content: template.query_content,
        params_json: Array.isArray(template.params_json) ? template.params_json : [],
        output_config_json:
          template.output_config_json && typeof template.output_config_json === 'object'
            ? template.output_config_json
            : {},
        cache_ttl: Number(template.cache_ttl) || 0,
        timeout_seconds: Math.max(1, Number(template.timeout_seconds) || 1),
        max_rows: Math.max(1, Number(template.max_rows) || 1),
        status: template.status || 'draft',
      };

      if (isNew) {
        const created = await queryTemplatesApi.create(payload);
        notify.success('模板已创建');
        // 替换 URL 为新模板 ID，避免刷新回到新建页
        navigate(`${EnterpriseRoute.DataQuery}/templates/${created.id}`, { replace: true });
        setTemplate(created);
        setDirty(false);
      } else if (templateId) {
        const updated = await queryTemplatesApi.update(templateId, payload);
        notify.success('保存成功');
        setTemplate(updated);
        setDirty(false);
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const pageTitle = isNew ? '新建查询模板' : '编辑查询模板';
  const pageDescription = isNew
    ? '配置查询语句、参数与输出格式，让数字员工可以直接调用业务数据。'
    : '修改查询模板的配置信息。';

  return (
    <div className="flex h-full flex-col">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title={pageTitle}
        description={pageDescription}
      />
      <div className="flex items-center justify-between px-[24px] pb-[12px] pt-[8px]">
        <Button
          variant="outline"
          onClick={handleBack}
          className="h-8 gap-1 rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-4 text-[12px] font-normal text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a]"
        >
          <IconArrowRight className="size-3.5 rotate-180" />
          返回
        </Button>
        <Button
          onClick={handleSave}
          disabled={saving || (loading && !isNew)}
          className="h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]"
        >
          {saving ? '保存中...' : '保存'}
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto px-[24px] pb-[24px]">
        {loading && !isNew ? (
          <div className="flex h-full items-center justify-center text-[#858b9c]">
            加载中...
          </div>
        ) : (
          <QueryTemplateEditor
            template={template}
            onChange={handleTemplateChange}
            isNew={isNew}
          />
        )}
      </div>
    </div>
  );
}
