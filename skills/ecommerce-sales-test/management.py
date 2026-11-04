"""本机配置/验收入口；不属于导入员工的技能文件，不修改平台源码。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
STATE = PACKAGE / 'state'
SOURCE_AGENT = 'agent_ecom_realtime_sales'
SOURCE_TASK = 'sched_ecom_realtime_30m'
NAME = '电商实时销售数据（技能测试副本）'
SLUG = 'ecommerce-sales-test'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode('utf-8')).hexdigest()


def source_fingerprint(db):
    from app.data_query.models import QueryTemplate
    from app.db.models import AgentProfile, AgentResourceBinding, ScheduledTask
    from sqlmodel import select
    source = db.get(AgentProfile, SOURCE_AGENT)
    task = db.get(ScheduledTask, SOURCE_TASK)
    task_fields = ['agent_id', 'title', 'prompt', 'description', 'schedule_type',
                   'schedule_json', 'timezone', 'status', 'execution_mode', 'pipeline_steps_json']
    metadata = {k: v for k, v in (task.metadata_json or {}).items()
                if k not in ('last_push_snapshot', 'target_snapshots')}
    bindings = db.exec(select(AgentResourceBinding).where(
        AgentResourceBinding.agent_id == SOURCE_AGENT)).all()
    templates = db.exec(select(QueryTemplate).where(QueryTemplate.tenant_id == source.tenant_id)).all()
    return digest({'agent': source.model_dump(),
                   'task': {k: getattr(task, k) for k in task_fields}, 'task_metadata': metadata,
                   'bindings': sorted([r.model_dump() for r in bindings], key=lambda r: r['id']),
                   'templates': sorted([r.model_dump() for r in templates], key=lambda r: r['id'])})


def source_files():
    paths = [*sorted((REPO / 'backend/app').rglob('*.py')),
             *sorted((REPO / 'frontend-enterprise/src').rglob('*.ts')),
             *sorted((REPO / 'frontend-enterprise/src').rglob('*.tsx'))]
    return digest([(str(p.relative_to(REPO)), hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in paths])


def save(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str),
                              encoding='utf-8')


def install(db):
    from app.agents.schema import AgentProfileCreateRequest
    from app.api.agents import create_agent
    from app.api.general_skills import import_general_skill
    from app.db.models import (
        AgentProfile,
        AgentResourceBinding,
        ScheduledTask,
        Tool,
        User,
    )
    from app.general_skills.schema import GeneralSkillFile, GeneralSkillImportRequest
    from app.scheduled_tasks.schema import (
        ScheduledTaskCreateRequest,
        ScheduledTaskUpdateRequest,
    )
    from app.scheduled_tasks.service import create_scheduled_task, update_scheduled_task
    from sqlmodel import select

    source = db.get(AgentProfile, SOURCE_AGENT)
    if source is None:
        raise ValueError('来源员工不存在')
    original_task = db.get(ScheduledTask, SOURCE_TASK)
    owner_name = (source.metadata_json or {}).get('created_by_username')
    owner = db.exec(select(User).where(User.tenant_id == source.tenant_id,
                                       User.username == owner_name)).first()
    if owner is None:
        raise ValueError('找不到来源员工的创建用户')
    before = source_fingerprint(db)
    code_before = source_files()
    clone = db.exec(select(AgentProfile).where(AgentProfile.tenant_id == source.tenant_id,
                                               AgentProfile.name == NAME)).first()
    persona = (
        '你是电商实时销售数据的技能测试副本。销售报告必须先读取 ecommerce-sales-test 技能，'
        '再调用已绑定的 ecommerce_sales_preview，直接使用其publication中的真实报告入口。'
        '报告已由技能发布，不要再次调用report_generate重建或裁剪报告。'
        '不要自行调用原始SQL查询，不要寻找数据文件；专用工具已在宿主侧完成取数和脚本执行。'
        '本副本只测试预览：禁止发送任何飞书消息、修改生产任务或更新生产分享链接。'
        '禁止exec_command、Shell探索、临时写代码及读取宿主.env。'
        '数据查询失败时如实报告，不编造指标，不能回退到生产流水线。')
    if clone is None:
        result = create_agent(AgentProfileCreateRequest(
            tenant_id=source.tenant_id, name=NAME,
            description='独立技能测试副本：真实销售取数、计算与HTML预览，通知和自动运行关闭。',
            persona_prompt=persona, copy_from_agent_id=source.id, source_mode='copy',
            metadata={'test_copy_of': source.id, 'test_only': True, 'delivery_mode': 'preview'}),
            db=db, current_user=owner)
        clone = db.get(AgentProfile, result.id)
    elif (clone.metadata_json or {}).get('test_copy_of') != source.id:
        raise ValueError('同名员工不是本工具创建，拒绝覆盖')
    else:
        clone.persona_prompt = persona
        db.add(clone)
    # 默认全量模式下inactive数据源绑定表示排除项，平台普通复制会跳过它们。
    source_data_bindings = db.exec(select(AgentResourceBinding).where(
        AgentResourceBinding.agent_id == source.id,
        AgentResourceBinding.resource_type == 'data_source')).all()
    for source_binding in source_data_bindings:
        copied = db.exec(select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == clone.id,
            AgentResourceBinding.resource_type == 'data_source',
            AgentResourceBinding.resource_id == source_binding.resource_id)).first()
        if copied is None:
            copied = AgentResourceBinding(tenant_id=source.tenant_id, agent_id=clone.id,
                resource_type='data_source', resource_id=source_binding.resource_id,
                status=source_binding.status)
        copied.status = source_binding.status
        db.add(copied)
    db.commit()
    from app.data_query.authorization import authorized_data_source_ids
    if authorized_data_source_ids(db, source.tenant_id, clone.id) != authorized_data_source_ids(
            db, source.tenant_id, source.id):
        raise ValueError('副本有效数据权限与来源不同，停止配置')
    # 仅调整新增副本的绑定；继承的飞书技能不进入测试员工能力清单。
    query_tools = {'tool_1928b09b881c4dd9', 'tool_1e1792e49ba34e18',
                   'tool_dabo_realtime_summary', 'tool_dabo_realtime_detail', 'tool_tm_dabo_realtime'}
    for binding in db.exec(select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == clone.id)).all():
        keep = binding.resource_type in {'data_source', 'knowledge_base'}
        if binding.resource_type == 'tool' and binding.resource_id in query_tools:
            tool = db.get(Tool, binding.resource_id)
            keep = bool(tool and ('/api/mock/data-query/' in (tool.url or '')
                                  or (tool.config_json or {}).get('source') == 'data_query'
                                  or (tool.config_json or {}).get('template_id')))
        if not keep:
            binding.status = 'inactive'
            db.add(binding)
    db.commit()
    names = ['SKILL.md', 'config.json', 'scripts/run.py', 'assets/dashboard.html',
             'references/verification.md']
    imported = import_general_skill(GeneralSkillImportRequest(
        tenant_id=source.tenant_id, agent_id=clone.id, slug=SLUG,
        name='电商销售数据技能（测试预览）',
        description='通过授权查询工具与固化脚本生成销售报告，不发送消息。',
        status='published', files=[GeneralSkillFile(path=p, content=(PACKAGE / p).read_text('utf-8'))
                                   for p in names]), db=db, current_user=owner)
    task = db.exec(select(ScheduledTask).where(ScheduledTask.agent_id == clone.id)).first()
    if task is None:
        task = create_scheduled_task(db, ScheduledTaskCreateRequest(
            tenant_id=source.tenant_id, agent_id=clone.id,
            title='电商销售技能验证（暂停，仅手动预览）',
            prompt='读取 general_skill.ecommerce-sales-test 并按其固定流程查询今日销售，'
                   '执行包内 scripts/run.py 生成独立HTML报告和JSON快照；禁止发送消息或更新生产报告。',
            description='复制来源任务的运行间隔，保持暂停，通知关闭，没有接收人和生产报告绑定。',
            schedule_type=original_task.schedule_type,
            schedule=dict(original_task.schedule_json), timezone=original_task.timezone,
            status='paused', execution_mode='agent', pipeline_steps=[],
            metadata={'test_only': True, 'copied_from_task_id': original_task.id,
                      'feishu_notify': {'enabled': False, 'chat_ids': [], 'webhooks': [],
                                        'mobiles': [], 'open_ids': []}}), owner)
    else:
        if (task.metadata_json or {}).get('copied_from_task_id') != original_task.id:
            raise ValueError('副本的任务不是本工具创建，拒绝覆盖')
        task = update_scheduled_task(db, task, ScheduledTaskUpdateRequest(
            tenant_id=source.tenant_id, status='paused',
            metadata={'test_only': True, 'copied_from_task_id': original_task.id,
                      'feishu_notify': {'enabled': False, 'chat_ids': [], 'webhooks': [],
                                        'mobiles': [], 'open_ids': []}}), owner)
    record = {'agent_id': clone.id, 'name': clone.name, 'tenant_id': clone.tenant_id,
              'owner_id': owner.id, 'skill_id': imported.id, 'skill_slug': imported.slug,
              'task_id': task.id, 'source_fingerprint': before, 'source_files': code_before}
    from app.api.tools import create_mcp_server, create_tool
    from app.db.models import MCPServer
    from app.tools.tool_schema import (
        MCPServerConnection,
        MCPServerCreateRequest,
        ToolCreateRequest,
        ToolExecutionPolicy,
    )
    server = db.exec(select(MCPServer).where(MCPServer.tenant_id == source.tenant_id,
                                              MCPServer.name == 'ecommerce-sales-skill-test')).first()
    if server is None:
        created = create_mcp_server(MCPServerCreateRequest(
            tenant_id=source.tenant_id, name='ecommerce-sales-skill-test',
            display_name='电商销售技能只读适配器（测试副本）',
            connection=MCPServerConnection(transport='stdio', command=sys.executable,
                args=[str(PACKAGE / 'adapters/mcp_server.py')], cwd=str(REPO / 'backend'))),
            db=db, current_user=owner)
        server = db.get(MCPServer, created.id)
    tool = db.exec(select(Tool).where(Tool.tenant_id == source.tenant_id,
                                      Tool.name == 'ecommerce_sales_preview')).first()
    if tool is None:
        created = create_tool(ToolCreateRequest(
            tenant_id=source.tenant_id, name='ecommerce_sales_preview',
            display_name='电商销售技能预览', description='只传业务日期，确定性取数并运行销售技能，不发送。',
            tool_type='mcp', method='POST', url='', mcp_config={'tool': 'ecommerce_sales_preview'},
            execution_policy=ToolExecutionPolicy(timeout_seconds=240),
            input_schema={'type': 'object', 'properties': {'date': {'type': 'string'}},
                          'required': ['date'], 'additionalProperties': False}),
            agent_id=clone.id, db=db, current_user=owner)
        tool = db.get(Tool, created.id)
        tool.mcp_server_id = server.id
        db.add(tool)
    # 适配器自带输入指纹复用；关闭平台POST幂等键，避免超时后
    # outcome_unknown 锁死同帧重试（240s 外层超时曾导致整轮验收失败）。
    config = dict(tool.config_json or {})
    config['tool'] = 'ecommerce_sales_preview'
    config['idempotency'] = {'enabled': False}
    tool.config_json = config
    db.add(tool)
    db.commit()
    binding = db.exec(select(AgentResourceBinding).where(
        AgentResourceBinding.agent_id == clone.id, AgentResourceBinding.resource_type == 'tool',
        AgentResourceBinding.resource_id == tool.id)).first()
    if binding is None:
        raise ValueError('专属工具缺少私有绑定')
    binding.status = 'active'
    db.add(binding)
    db.commit()
    record['preview_tool_id'] = tool.id
    record['mcp_server_id'] = server.id
    if (STATE / 'registration.json').exists():
        old = json.loads((STATE / 'registration.json').read_text('utf-8'))
        record['source_fingerprint'] = old['source_fingerprint']
        record['source_files'] = old['source_files']
        if old.get('delivery_session_id'):
            record['delivery_session_id'] = old['delivery_session_id']
    sys.path.insert(0, str(PACKAGE / 'adapters'))
    from platform_delivery import ensure_delivery_session
    record['delivery_session_id'] = ensure_delivery_session(db, record)
    task.prompt = ('读取ecommerce-sales-test技能，调用ecommerce_sales_preview查询今日真实销售，'
                   '使用返回的publication入口打开完整报告；禁止发送、Shell和生产看板更新。')
    db.add(task)
    db.commit()
    save('registration.json', record)
    verify(db, record)
    print(json.dumps({k: v for k, v in record.items() if not k.startswith('source_')},
                     ensure_ascii=False), flush=True)


def verify(db, record):
    from app.db.models import AgentResourceBinding, GeneralSkill, ScheduledTask, Tool
    from sqlmodel import select
    task = db.get(ScheduledTask, record['task_id'])
    assert task.agent_id == record['agent_id'] and task.status == 'paused'
    assert task.execution_mode == 'agent' and task.next_run_at is None
    notify = task.metadata_json['feishu_notify']
    assert notify.get('enabled') is False
    assert not any(notify.get(k) for k in ['chat_ids', 'webhooks', 'mobiles', 'open_ids'])
    assert not task.metadata_json.get('artifact_share_link_id')
    preview_tool = db.get(Tool, record.get('preview_tool_id'))
    assert preview_tool is not None and preview_tool.enabled
    preview_config = preview_tool.config_json or {}
    assert (preview_config.get('idempotency') or {}).get('enabled') is False
    assert float((preview_config.get('execution') or {}).get('timeout_seconds') or 0) >= 240
    skills = db.exec(select(AgentResourceBinding).where(
        AgentResourceBinding.agent_id == record['agent_id'],
        AgentResourceBinding.resource_type == 'general_skill',
        AgentResourceBinding.status == 'active')).all()
    assert len(skills) == 1 and skills[0].resource_id == record['skill_id']
    assert db.get(GeneralSkill, record['skill_id']).status == 'published'
    from app.data_query.authorization import authorized_data_source_ids
    assert authorized_data_source_ids(db, record['tenant_id'], record['agent_id']) <= \
        authorized_data_source_ids(db, record['tenant_id'], SOURCE_AGENT)
    for binding in db.exec(select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == record['agent_id'],
            AgentResourceBinding.status == 'active')).all():
        assert binding.resource_type in {'data_source', 'knowledge_base', 'general_skill', 'tool'}
        if binding.resource_type == 'tool':
            tool = db.get(Tool, binding.resource_id)
            assert tool and (tool.name.startswith('query_') or tool.id == record.get('preview_tool_id'))
    comparison = {'source_config_unchanged': source_fingerprint(db) == record['source_fingerprint'],
                  'source_files_unchanged': source_files() == record['source_files']}
    save('source-comparison.json', comparison)
    print('VERIFIED: 副本技能已绑定；任务暂停、推送关闭。' +
          json.dumps(comparison, ensure_ascii=False), flush=True)
    if not all(comparison.values()):
        print('NOTICE: 创建时基线与当前内容不同；保留并行变化，未回写或恢复来源文件/配置。', flush=True)


def exercise(db, record):
    from app.core.agent_loop import AgentLoop
    from app.session.session_schema import ChatTurnRequest
    print('开始当前Harness v3实际对话验收，使用测试副本，禁止消息推送。', flush=True)
    result = AgentLoop(db).handle_turn(ChatTurnRequest(
        tenant_id=record['tenant_id'], agent_id=record['agent_id'], user_id=record['owner_id'],
        message='请先读取ecommerce-sales-test技能，再调用ecommerce_sales_preview（只传今日日期），'
                '直接使用该工具publication中已发布的完整HTML报告，不要调用report_generate重建报告。'
                '不要自行查询SQL、运行Shell或寻找输入文件。不要发送消息。'
                '完成后给出真实报告入口；差值只能称为较上次成功预览，没有基准则说明首次。'))
    save('chat-result.json', result.model_dump(mode='json'))
    print(json.dumps({'session_id': result.session_id,
                      'runtime_error_code': result.runtime_error_code,
                      'reply_saved': 'state/chat-result.json'}, ensure_ascii=False), flush=True)
    if result.runtime_error_code:
        raise RuntimeError(f'真实对话验收失败：{result.runtime_error_code}')
    from app.db.models import HarnessInvocationRecord
    from sqlmodel import select
    receipts = db.exec(select(HarnessInvocationRecord).where(
        HarnessInvocationRecord.session_id == result.session_id)).all()
    successful = [r for r in receipts if r.status == 'completed'
                  and r.arguments_json.get('tool_id') == record['preview_tool_id']
                  and (r.response_cache_json.get('data') or {}).get('status') == 'success']
    if not successful:
        raise RuntimeError('没有成功的技能脚本执行回执，不能视为通过')
    preview_data = successful[-1].response_cache_json['data']
    if not preview_data.get('published') or preview_data.get('publication', {}).get('status') != 'success':
        raise RuntimeError('完整报告没有成功发布，不能视为交付通过')
    from app.db.models import ArtifactShareLink
    publication = preview_data['publication']
    link = db.get(ArtifactShareLink, publication['share_link_id'])
    if not link or link.is_revoked or link.session_id != record['delivery_session_id']:
        raise RuntimeError('完整报告分享记录不可用')
    report = Path(preview_data['report_path'])
    snapshot_path = Path(preview_data['snapshot_path'])
    workspace = report.parents[2]
    if not report.is_file() or not snapshot_path.is_file():
        raise RuntimeError('实际交付文件缺失')
    snapshot = json.loads(snapshot_path.read_text('utf-8'))
    if snapshot.get('push_status') != 'disabled' or not snapshot.get('sources'):
        raise RuntimeError('报告状态或来源验证失败')
    if any(r.arguments_json.get('tool') == 'exec_command' for r in receipts):
        raise RuntimeError('实际对话偏离技能：调用了Shell')
    save('verification.json', {'session_id': result.session_id, 'workspace': str(workspace),
                              'report': str(report), 'snapshot': str(snapshot_path),
                              'sources': snapshot['sources'], 'kpis': snapshot['kpis'],
                              'delta': snapshot['delta'], 'publication': publication,
                              'section_counts': preview_data['section_counts']})
    verify(db, record)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['install', 'verify', 'exercise'])
    args = parser.parse_args()
    os.chdir(REPO / 'backend')
    sys.path.insert(0, str(REPO / 'backend'))
    sys.path.insert(0, str(REPO / 'backend/src'))
    from staffdeck_harness.distribution.bootstrap import configure_environment
    configure_environment()
    from app.db.database import engine
    from sqlmodel import Session
    with Session(engine) as db:
        if args.operation == 'install':
            install(db)
        else:
            record = json.loads((STATE / 'registration.json').read_text('utf-8'))
            if args.operation == 'verify':
                verify(db, record)
            else:
                exercise(db, record)


if __name__ == '__main__':
    main()
