import { useEffect, useState } from 'react';
import MarkdownMessage from './MarkdownMessage';
import SpreadsheetArtifactTable from './SpreadsheetArtifactTable';
import { getArtifactFileUrl, getFileExtension } from './artifactFiles';
import { loadSpreadsheetPreview, type SpreadsheetPreviewData } from './spreadsheetLoader';
import './ArtifactViewer.css';
import './SpreadsheetArtifactTable.css';

export type ArtifactViewerMode =
    | 'loading'
    | 'error'
    | 'iframe'
    | 'image'
    | 'video'
    | 'markdown'
    | 'text'
    | 'word-html'
    | 'spreadsheet';

interface ArtifactViewerProps {
    token: string;
    filename: string;
    serverUrl?: string | null;
}

function formatTextContent(raw: string, extension: string): string {
    if (extension === 'json') {
        try {
            return JSON.stringify(JSON.parse(raw), null, 2);
        } catch {
            return raw;
        }
    }
    return raw;
}

function resolveViewerMode(extension: string): ArtifactViewerMode | 'fetch' {
    if (['pdf', 'html', 'htm'].includes(extension)) return 'iframe';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(extension)) return 'image';
    if (['mp4', 'webm', 'mov'].includes(extension)) return 'video';
    if (['md', 'markdown'].includes(extension)) return 'fetch';
    if (['txt', 'json', 'xml', 'yaml', 'yml'].includes(extension)) return 'fetch';
    if (['docx', 'doc'].includes(extension)) return 'fetch';
    if (['csv', 'xlsx', 'xls'].includes(extension)) return 'fetch';
    return 'error';
}

export default function ArtifactViewer({ token, filename, serverUrl }: ArtifactViewerProps) {
    const fileUrl = getArtifactFileUrl(token, serverUrl);
    const extension = getFileExtension(filename);
    const staticMode = resolveViewerMode(extension);

    const [mode, setMode] = useState<ArtifactViewerMode>(
        staticMode === 'fetch' ? 'loading' : staticMode
    );
    const [textContent, setTextContent] = useState<string | null>(null);
    const [wordHtml, setWordHtml] = useState<string | null>(null);
    const [spreadsheetData, setSpreadsheetData] = useState<SpreadsheetPreviewData | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    useEffect(() => {
        if (staticMode !== 'fetch') {
            setMode(staticMode);
            return;
        }

        let cancelled = false;

        const load = async () => {
            setMode('loading');
            setErrorMessage(null);
            setTextContent(null);
            setWordHtml(null);
            setSpreadsheetData(null);

            try {
                if (['csv', 'xlsx', 'xls'].includes(extension)) {
                    const data = await loadSpreadsheetPreview(fileUrl, extension);
                    if (cancelled) return;
                    setSpreadsheetData(data);
                    setMode('spreadsheet');
                    return;
                }

                const response = await fetch(fileUrl);
                if (!response.ok) {
                    throw new Error(`Failed to load file (${response.status})`);
                }

                if (['docx', 'doc'].includes(extension)) {
                    const arrayBuffer = await response.arrayBuffer();
                    // Lazy-load mammoth only when a Word doc is actually viewed
                    const mammoth = (await import('mammoth')).default;
                    const result = await mammoth.convertToHtml({ arrayBuffer });
                    if (cancelled) return;
                    setWordHtml(result.value);
                    setMode('word-html');
                    return;
                }

                const raw = await response.text();
                if (cancelled) return;

                if (['md', 'markdown'].includes(extension)) {
                    setTextContent(raw);
                    setMode('markdown');
                } else {
                    setTextContent(formatTextContent(raw, extension));
                    setMode('text');
                }
            } catch (err) {
                if (cancelled) return;
                console.error('ArtifactViewer load failed:', err);
                setErrorMessage(
                    err instanceof Error ? err.message : 'Could not load preview.'
                );
                setMode('error');
            }
        };

        load();

        return () => {
            cancelled = true;
        };
    }, [fileUrl, extension, staticMode]);

    if (mode === 'loading') {
        return <div className="artifact-viewer artifact-viewer--loading">Loading preview…</div>;
    }

    if (mode === 'error') {
        return (
            <div className="artifact-viewer artifact-viewer--error">
                {errorMessage ?? 'Preview not available for this file type.'}
            </div>
        );
    }

    if (mode === 'iframe') {
        return (
            <div className="artifact-viewer artifact-viewer--iframe">
                <iframe
                    src={fileUrl}
                    title={filename}
                    className="artifact-viewer__iframe"
                    sandbox="allow-scripts allow-same-origin"
                />
            </div>
        );
    }

    if (mode === 'image') {
        return (
            <div className="artifact-viewer artifact-viewer--image">
                <img
                    src={fileUrl}
                    alt={filename}
                    className="artifact-viewer__image"
                    onError={() => {
                        setErrorMessage('Could not load image.');
                        setMode('error');
                    }}
                />
            </div>
        );
    }

    if (mode === 'video') {
        return (
            <div className="artifact-viewer artifact-viewer--video">
                <video
                    src={fileUrl}
                    controls
                    autoPlay
                    loop
                    playsInline
                    className="artifact-viewer__video"
                >
                    Your browser does not support video playback.
                </video>
            </div>
        );
    }

    if (mode === 'markdown' && textContent !== null) {
        return (
            <div className="artifact-viewer artifact-viewer--markdown">
                <MarkdownMessage content={textContent} />
            </div>
        );
    }

    if (mode === 'text' && textContent !== null) {
        return (
            <div className="artifact-viewer artifact-viewer--text">
                <pre className="artifact-viewer__pre">{textContent}</pre>
            </div>
        );
    }

    if (mode === 'word-html' && wordHtml !== null) {
        return (
            <div
                className="artifact-viewer artifact-viewer--word"
                dangerouslySetInnerHTML={{ __html: wordHtml }}
            />
        );
    }

    if (mode === 'spreadsheet' && spreadsheetData !== null) {
        return <SpreadsheetArtifactTable data={spreadsheetData} />;
    }

    return (
        <div className="artifact-viewer artifact-viewer--error">Preview not available.</div>
    );
}
