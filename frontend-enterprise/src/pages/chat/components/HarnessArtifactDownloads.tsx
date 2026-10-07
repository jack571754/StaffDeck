import { useEffect, useState } from 'react';

import StaffdeckIcon from '@/components/StaffdeckIcon';
import { notify } from '@/components/ui/app-toast';
import { api } from '@/api/client';
import { copyTextToClipboard } from '@/lib/clipboard';
import type { HarnessWorkspaceArtifact } from '@/types';

import {
  CHAT_ARTIFACTS_CLASS,
  CHAT_ARTIFACT_ACTION_CLASS,
  CHAT_ARTIFACT_ACTIONS_CLASS,
  CHAT_ARTIFACT_BUTTON_CLASS,
  CHAT_ARTIFACT_COPY_CLASS,
  CHAT_ARTIFACT_HEADING_CLASS,
  CHAT_ARTIFACT_ICON_CLASS,
  CHAT_ARTIFACT_IMAGE_CARD_CLASS,
  CHAT_ARTIFACT_IMAGE_CLASS,
  CHAT_ARTIFACT_IMAGE_DOWNLOAD_CLASS,
  CHAT_ARTIFACT_IMAGE_FOOTER_CLASS,
  CHAT_ARTIFACT_IMAGE_LINK_CLASS,
  CHAT_ARTIFACT_IMAGE_PLACEHOLDER_CLASS,
  CHAT_ARTIFACT_LIST_CLASS,
  CHAT_ARTIFACT_META_CLASS,
  CHAT_ARTIFACT_NAME_CLASS,
  CHAT_ARTIFACT_ROW_CLASS,
} from '../chatPageStyles';

type HarnessArtifactDownloadsProps = {
  artifacts: HarnessWorkspaceArtifact[];
  tenantId: string;
  sessionId: string;
};

type ArtifactShareLink = {
  url?: string;
  token?: string;
  expires_at?: number;
};

export default function HarnessArtifactDownloads({
  artifacts,
  tenantId,
  sessionId,
}: HarnessArtifactDownloadsProps) {
  const [downloading, setDownloading] = useState('');
  const [previewing, setPreviewing] = useState('');
  const [sharing, setSharing] = useState('');

  if (artifacts.length === 0) return null;

  async function downloadArtifact(artifact: HarnessWorkspaceArtifact) {
    const identity = artifactIdentity(artifact);
    const filename = artifactFilename(artifact.display_name || artifact.path);
    setDownloading(identity);
    try {
      const blob = await api.blob(artifactApiPath(artifact, tenantId, sessionId));
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
      notify.success(`已下载文件：${filename}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '文件下载失败');
    } finally {
      setDownloading('');
    }
  }

  /**
   * Mint a short-lived signed link for one published artifact.
   *
   * The backend already owns this capability (`POST /api/chat/artifacts/share-link`)
   * and signs `(owner_kind, owner_id, path)`; the console only needs to ask for it.
   */
  async function mintArtifactShareLink(
    artifact: HarnessWorkspaceArtifact,
  ): Promise<ArtifactShareLink> {
    return api.post<ArtifactShareLink>('/api/chat/artifacts/share-link', {
      tenant_id: tenantId,
      session_id: sessionId,
      task_frame_id: artifact.task_frame_id,
      path: artifact.path,
    });
  }

  async function previewArtifact(artifact: HarnessWorkspaceArtifact) {
    const identity = artifactIdentity(artifact);
    setPreviewing(identity);
    try {
      const share = await mintArtifactShareLink(artifact);
      if (!share.token) throw new Error('分享链接生成失败');
      // Deliberately relative: the console and the API share one origin here (single
      // port app, or the Vite dev proxy), so the report opens whichever address the
      // user actually uses — TOOL_BASE_URL may hard-code localhost:5173.
      window.open(
        `/api/chat/artifacts/view/${encodeURIComponent(share.token)}`,
        '_blank',
        'noopener,noreferrer',
      );
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '报告预览失败');
    } finally {
      setPreviewing('');
    }
  }

  async function shareArtifact(artifact: HarnessWorkspaceArtifact) {
    const identity = artifactIdentity(artifact);
    setSharing(identity);
    try {
      const share = await mintArtifactShareLink(artifact);
      if (!share.token) throw new Error('分享链接生成失败');
      const link = share.url
        || `${window.location.origin}/api/chat/artifacts/view/${encodeURIComponent(share.token)}`;
      await copyTextToClipboard(link);
      notify.success('分享链接已复制');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '分享链接生成失败');
    } finally {
      setSharing('');
    }
  }

  return (
    <div className={CHAT_ARTIFACTS_CLASS} aria-label="生成文件">
      <div className={CHAT_ARTIFACT_HEADING_CLASS}>
        <StaffdeckIcon name="folder" size={14} />
        <span>生成文件</span>
      </div>
      <div className={CHAT_ARTIFACT_LIST_CLASS}>
        {artifacts.map((artifact) => {
          const identity = artifactIdentity(artifact);
          const filename = artifactFilename(artifact.display_name || artifact.path);
          const isDownloading = downloading === identity;
          if (isImageArtifact(artifact)) {
            return (
              <ArtifactImagePreview
                artifact={artifact}
                identity={identity}
                filename={filename}
                isDownloading={isDownloading}
                key={identity}
                tenantId={tenantId}
                sessionId={sessionId}
                onDownload={() => void downloadArtifact(artifact)}
              />
            );
          }
          return (
            <div className={CHAT_ARTIFACT_ROW_CLASS} key={identity}>
              <button
                type="button"
                className={CHAT_ARTIFACT_BUTTON_CLASS}
                disabled={isDownloading || !sessionId || !tenantId}
                aria-label={`下载文件 ${filename}`}
                aria-busy={isDownloading}
                onClick={() => void downloadArtifact(artifact)}
              >
                <span className={CHAT_ARTIFACT_ICON_CLASS}>
                  <StaffdeckIcon name="file" size={17} />
                </span>
                <span className={CHAT_ARTIFACT_COPY_CLASS}>
                  <span className={CHAT_ARTIFACT_NAME_CLASS} data-i18n-ignore>
                    {filename}
                  </span>
                  <span className={CHAT_ARTIFACT_META_CLASS}>
                    {isDownloading ? '下载中' : artifactMeta(artifact)}
                  </span>
                </span>
                <StaffdeckIcon name="download" size={16} />
              </button>
              {isHtmlArtifact(artifact) ? (
                <div className={CHAT_ARTIFACT_ACTIONS_CLASS}>
                  <button
                    type="button"
                    className={CHAT_ARTIFACT_ACTION_CLASS}
                    disabled={!sessionId || !tenantId || previewing === identity}
                    aria-label={`预览文件 ${filename}`}
                    aria-busy={previewing === identity}
                    onClick={() => void previewArtifact(artifact)}
                  >
                    <StaffdeckIcon name="eye" size={14} />
                    <span>{previewing === identity ? '生成中' : '预览'}</span>
                  </button>
                  <button
                    type="button"
                    className={CHAT_ARTIFACT_ACTION_CLASS}
                    disabled={!sessionId || !tenantId || sharing === identity}
                    aria-label={`分享文件 ${filename}`}
                    aria-busy={sharing === identity}
                    onClick={() => void shareArtifact(artifact)}
                  >
                    <StaffdeckIcon name="globe" size={14} />
                    <span>{sharing === identity ? '生成中' : '分享'}</span>
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

type ArtifactImagePreviewProps = {
  artifact: HarnessWorkspaceArtifact;
  identity: string;
  filename: string;
  isDownloading: boolean;
  tenantId: string;
  sessionId: string;
  onDownload: () => void;
};

function ArtifactImagePreview({
  artifact,
  identity,
  filename,
  isDownloading,
  tenantId,
  sessionId,
  onDownload,
}: ArtifactImagePreviewProps) {
  const [previewUrl, setPreviewUrl] = useState('');
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    if (!tenantId || !sessionId) return undefined;
    let disposed = false;
    let objectUrl = '';
    setPreviewUrl('');
    setPreviewFailed(false);

    void api.blob(artifactApiPath(artifact, tenantId, sessionId))
      .then((blob) => {
        if (disposed) return;
        objectUrl = window.URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch(() => {
        if (!disposed) setPreviewFailed(true);
      });

    return () => {
      disposed = true;
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.path, artifact.task_frame_id, identity, sessionId, tenantId]);

  return (
    <figure className={CHAT_ARTIFACT_IMAGE_CARD_CLASS}>
      {previewUrl ? (
        <a
          className={CHAT_ARTIFACT_IMAGE_LINK_CLASS}
          href={previewUrl}
          target="_blank"
          rel="noreferrer"
          aria-label={`查看图片 ${filename}`}
        >
          <img
            className={CHAT_ARTIFACT_IMAGE_CLASS}
            src={previewUrl}
            alt={filename}
            loading="lazy"
            decoding="async"
          />
        </a>
      ) : (
        <div className={CHAT_ARTIFACT_IMAGE_PLACEHOLDER_CLASS} aria-live="polite">
          {previewFailed ? '图片预览不可用，可下载查看' : '正在加载图片…'}
        </div>
      )}
      <figcaption className={CHAT_ARTIFACT_IMAGE_FOOTER_CLASS}>
        <span className={CHAT_ARTIFACT_COPY_CLASS}>
          <span className={CHAT_ARTIFACT_NAME_CLASS} data-i18n-ignore>{filename}</span>
          <span className={CHAT_ARTIFACT_META_CLASS}>{artifactMeta(artifact)}</span>
        </span>
        <button
          type="button"
          className={CHAT_ARTIFACT_IMAGE_DOWNLOAD_CLASS}
          disabled={isDownloading || !sessionId || !tenantId}
          aria-label={`下载图片 ${filename}`}
          aria-busy={isDownloading}
          onClick={onDownload}
        >
          <StaffdeckIcon name="download" size={16} />
        </button>
      </figcaption>
    </figure>
  );
}

function artifactApiPath(
  artifact: HarnessWorkspaceArtifact,
  tenantId: string,
  sessionId: string,
): string {
  const query = new URLSearchParams({
    tenant_id: tenantId,
    path: artifact.path,
  });
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/artifacts/`
    + `${encodeURIComponent(artifact.task_frame_id)}?${query.toString()}`;
}

function artifactIdentity(artifact: HarnessWorkspaceArtifact): string {
  return `${artifact.task_frame_id}\u001f${artifact.path}`;
}

function isHtmlArtifact(artifact: HarnessWorkspaceArtifact): boolean {
  const contentType = artifact.content_type?.toLowerCase().split(';')[0].trim();
  if (contentType === 'text/html') return true;
  return /\.html?$/i.test(artifact.display_name || artifact.path);
}

function artifactFilename(path: string): string {
  const filename = path.replace(/\\/g, '/').split('/').pop()?.trim() || '';
  return filename.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 180) || 'artifact';
}

function isImageArtifact(artifact: HarnessWorkspaceArtifact): boolean {
  const contentType = artifact.content_type?.toLowerCase().split(';')[0].trim();
  if (contentType?.startsWith('image/')) return true;
  return /\.(?:apng|avif|bmp|gif|jpe?g|png|svg|webp)$/i.test(
    artifact.display_name || artifact.path,
  );
}

function artifactMeta(artifact: HarnessWorkspaceArtifact): string {
  const size = formatArtifactSize(artifact.size);
  const description = artifact.description?.trim();
  if (description && size) return `${description} · ${size}`;
  if (description) return description;
  return size ? `生成文件 · ${size}` : '生成文件';
}

function formatArtifactSize(size: number | null | undefined): string {
  if (typeof size !== 'number' || !Number.isFinite(size) || size < 0) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
