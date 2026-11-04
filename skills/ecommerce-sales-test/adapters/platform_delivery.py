"""复用平台既有交付与分享接口，仅发布技能测试副本自己的文件。"""

import json
from datetime import UTC, datetime


def ensure_delivery_session(db, record):
    from app.api.chat import create_chat_session
    from app.db.models import ChatSession, User
    from app.session.session_schema import ChatSessionCreateRequest

    owner = db.get(User, record['owner_id'])
    if not owner or owner.tenant_id != record['tenant_id']:
        raise ValueError('测试副本的创建用户不可用')
    if record.get('delivery_session_id'):
        session = db.get(ChatSession, record['delivery_session_id'])
        if not session or (session.tenant_id, session.user_id, session.agent_id) != (
                record['tenant_id'], record['owner_id'], record['agent_id']):
            raise ValueError('报告交付会话的所有者不匹配')
        return session.id
    created = create_chat_session(ChatSessionCreateRequest(
        tenant_id=record['tenant_id'], agent_id=record['agent_id'],
        title='电商销售技能 · 完整预览报告'), current_user=owner, db=db)
    return created.id


def report_workspace(db, record, frame_id):
    from app.core.harness_session_cleanup import harness_task_workspace_path
    workspace = harness_task_workspace_path(tenant_id=record['tenant_id'],
        session_id=ensure_delivery_session(db, record), task_frame_id=frame_id, db=db)
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


def publish(db, record, frame_id, workspace, snapshot):
    from app.api.chat import ShareLinkRequest, mint_artifact_share
    from app.db.models import HarnessTaskFrameRecord, Message, User, new_id
    from app.harness.artifacts import publish_harness_artifacts

    session_id = ensure_delivery_session(db, record)
    artifacts = publish_harness_artifacts(workspace, frame_id,
        ['reports/ecommerce-sales-test/dashboard.html', 'reports/ecommerce-sales-test/snapshot.json'],
        operation='ecommerce_sales_skill_preview')
    for item in artifacts:
        item.update({'owner_kind': 'harness_frame', 'owner_id': frame_id,
                     'content_type': 'text/html' if item['path'].endswith('.html') else 'application/json',
                     'display_name': snapshot['title'] + ('.html' if item['path'].endswith('.html') else '.json')})
    db.add(HarnessTaskFrameRecord(tenant_id=record['tenant_id'], session_id=session_id,
        task_id=frame_id, source_turn_id=new_id('sales_preview'), status='completed',
        user_intent='测试技能生成并发布独立销售预览报告'))
    db.add(Message(tenant_id=record['tenant_id'], session_id=session_id, role='assistant',
        content=f'已生成{snapshot["date"]}销售技能完整报告；仅预览，未发送飞书。',
        metadata_json={'harness_artifacts': artifacts, 'source': 'ecommerce-sales-test',
                       'test_only': True, 'kpis': snapshot['kpis']}))
    db.commit()
    shared = mint_artifact_share(ShareLinkRequest(
        tenant_id=record['tenant_id'], session_id=session_id, task_frame_id=frame_id,
        path='reports/ecommerce-sales-test/dashboard.html', days=7, title=snapshot['title']),
        current_user=db.get(User, record['owner_id']), db=db)
    return {'status': 'success', 'session_id': session_id, 'frame_id': frame_id,
            'share_link_id': shared.link_id, 'url': shared.url, 'expires_at': shared.expires_at,
            'html_sha256': artifacts[0]['sha256']}


def reusable(db, record, result):
    import hashlib

    from app.core.artifact_owners import resolve_artifact_owner
    from app.db.models import ArtifactShareLink
    publication = result.get('publication', {})
    link = db.get(ArtifactShareLink, publication.get('share_link_id', ''))
    now = datetime.now(UTC).timestamp()
    if not (link and not link.is_revoked and link.tenant_id == record['tenant_id']
                and link.session_id == record['delivery_session_id']
                and publication.get('expires_at', 0) > now
                and link.expires_at.replace(tzinfo=UTC).timestamp() > now):
        return False
    resolved = resolve_artifact_owner(db, owner_kind=link.owner_kind, tenant_id=link.tenant_id,
        session_id=link.session_id, owner_id=link.owner_id, path=link.path)
    if not resolved:
        return False
    file = resolved.workspace_root / resolved.path
    return bool(file.is_file() and not file.is_symlink()
                and file.resolve().is_relative_to(resolved.workspace_root.resolve())
                and hashlib.sha256(file.read_bytes()).hexdigest() == resolved.sha256)


def input_fingerprint(summary, dabo):
    """排除耗时和缓存标识，保留每行全部业务值与上游时间。"""
    import hashlib
    datasets = []
    for payload in (summary, dabo):
        datasets.append({'template_id': payload['template_id'],
            'row_count': payload.get('row_count', len(payload['rows'])),
            'truncated': bool(payload.get('truncated')), 'success': payload.get('success', True),
            'rows': sorted(
            json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
            for row in payload['rows'])})
    return hashlib.sha256(json.dumps(datasets, ensure_ascii=False).encode('utf-8')).hexdigest()
