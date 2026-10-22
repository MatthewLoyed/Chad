import { getServerUrl, toHttpBase } from '../../transport/transportConfig';

export function getFileExtension(filename: string): string {
    return filename.split('.').pop()?.toLowerCase() ?? '';
}

export function getArtifactFileUrl(token: string, serverUrl?: string | null): string {
    const sUrl = serverUrl || getServerUrl();
    if (sUrl) {
        const base = toHttpBase(sUrl);
        return `${base}/files/${token}`;
    }
    return `/files/${token}`;
}

export function downloadArtifact(
    token: string,
    filename: string,
    serverUrl?: string | null
): void {
    const api = (window as { pywebview?: { api?: { download_artifact?: (t: string) => void } } })
        .pywebview?.api;
    if (api?.download_artifact) {
        api.download_artifact(token);
        return;
    }

    const url = getArtifactFileUrl(token, serverUrl);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
}
