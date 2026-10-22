import React, { useState } from 'react';
import { Download, Eye, Copy, Check } from 'lucide-react';
import './ArtifactCard.css';
import { downloadArtifact, getArtifactFileUrl, getFileExtension } from './artifactFiles';
import { getFileTypeInfo } from './artifactTypes';
import { openArtifactPreview } from './openArtifactPreview';

export interface ArtifactData {
    filename?: string;
    name?: string;
    download_token?: string;
    token?: string;
    size?: number;
    created_at?: number;
}


interface ArtifactCardProps {
    artifact: ArtifactData;
}

export default function ArtifactCard({ artifact }: ArtifactCardProps) {
    const filename = artifact.filename || artifact.name || 'Generated File';
    const info = getFileTypeInfo(filename, artifact.name);
    const token = artifact.download_token || artifact.token;
    const [copied, setCopied] = useState(false);

    const ext = getFileExtension(filename);
    const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext);

    const handleDownload = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (token) {
            downloadArtifact(token, filename);
        }
    };

    const handlePreview = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (token) {
            openArtifactPreview({ token, filename });
        }
    };

    const handleCopy = async (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!token) return;
        const url = getArtifactFileUrl(token);
        try {
            const resp = await fetch(url);
            const blob = await resp.blob();
            await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            try {
                await navigator.clipboard.writeText(window.location.origin + url);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
            } catch (err) {
                console.error('Failed to copy image:', err);
            }
        }
    };

    if (isImage && token) {
        const imageUrl = getArtifactFileUrl(token);
        return (
            <div className="artifact-card artifact-card--image-rich">
                <div className="artifact-card__image-container" onClick={handlePreview} title="Click to view full size">
                    <img
                        src={imageUrl}
                        alt={filename}
                        className="artifact-card__image-element"
                        loading="lazy"
                    />
                </div>
                <div className="artifact-card__image-footer">
                    <div className="artifact-card__info">
                        <div className="artifact-card__filename" title={filename}>
                            {filename}
                        </div>
                        <div className="artifact-card__meta">{info.label}</div>
                    </div>
                    <div className="artifact-card__actions">
                        <button
                            type="button"
                            className="artifact-card__btn artifact-card__btn--copy"
                            onClick={handleCopy}
                            title="Copy Image"
                        >
                            {copied ? <Check size={16} /> : <Copy size={16} />}
                            <span>{copied ? 'Copied' : 'Copy'}</span>
                        </button>
                        <button
                            type="button"
                            className="artifact-card__btn"
                            onClick={handleDownload}
                            title="Save Image"
                        >
                            <Download size={16} />
                            <span>Save</span>
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div
            className={`artifact-card ${info.colorClass}`}
            onClick={info.canPreview ? handlePreview : undefined}
        >
            <div className="artifact-card__icon-wrapper">{info.icon}</div>
            <div className="artifact-card__info">
                <div className="artifact-card__filename" title={filename}>
                    {filename}
                </div>
                <div className="artifact-card__meta">{info.label}</div>
            </div>
            <div className="artifact-card__actions">
                {info.canPreview && token && (
                    <button
                        type="button"
                        className="artifact-card__btn artifact-card__btn--preview"
                        onClick={handlePreview}
                        title="Preview"
                    >
                        <Eye size={16} />
                    </button>
                )}
                {token && (
                    <button
                        type="button"
                        className="artifact-card__btn"
                        onClick={handleDownload}
                        title="Save File"
                    >
                        <Download size={16} />
                        <span>Save</span>
                    </button>
                )}
            </div>
        </div>
    );
}
