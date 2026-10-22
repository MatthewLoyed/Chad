import React, { useState } from 'react';
import { Globe, ExternalLink, Copy, Check } from 'lucide-react';
import { domainFromUrl } from '../../utils/sources';
import './InlineLinkPill.css';

export interface InlineLinkPillProps {
    href: string;
    title?: string;
    citationIndex?: number;
    children?: React.ReactNode;
    className?: string;
}

export default function InlineLinkPill({
    href,
    title,
    citationIndex,
    children,
    className = '',
}: InlineLinkPillProps) {
    const [copied, setCopied] = useState(false);
    const [imgFailed, setImgFailed] = useState(false);

    const domain = domainFromUrl(href);
    const faviconUrl = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;

    const displayLabel = children && typeof children === 'string' && children.trim() && children !== href
        ? children
        : title || domain;

    const handleOpen = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (window.pywebview?.api?.open_external_url) {
            window.pywebview.api.open_external_url(href);
        } else {
            window.open(href, '_blank', 'noopener,noreferrer');
        }
    };

    const handleCopy = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        navigator.clipboard.writeText(href);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <span className={`inline-pill-container ${className}`}>
            <button
                type="button"
                className="inline-pill"
                onClick={handleOpen}
                title={title || href}
                aria-label={citationIndex !== undefined ? citationIndex.toString() : (title || domain)}
            >
                {citationIndex !== undefined && (
                    <span className="inline-pill__index">{citationIndex}</span>
                )}
                {!imgFailed ? (
                    <img
                        src={faviconUrl}
                        alt=""
                        className="inline-pill__favicon"
                        onError={() => setImgFailed(true)}
                        loading="lazy"
                    />
                ) : (
                    <Globe size={11} className="inline-pill__icon" />
                )}
                <span className="inline-pill__label">{displayLabel}</span>
                <ExternalLink size={10} className="inline-pill__arrow" />
            </button>

            <span className="inline-pill__popover" role="tooltip">
                <span className="inline-pill__popover-header">
                    {!imgFailed ? (
                        <img src={faviconUrl} alt="" className="inline-pill__popover-favicon" />
                    ) : (
                        <Globe size={13} className="inline-pill__popover-icon" />
                    )}
                    <span className="inline-pill__popover-domain">{domain}</span>
                    <button
                        type="button"
                        className="inline-pill__popover-copy-btn"
                        onClick={handleCopy}
                        title="Copy link"
                        aria-label="Copy link"
                    >
                        {copied ? <Check size={11} className="inline-pill__copy-success" /> : <Copy size={11} />}
                        <span>{copied ? 'Copied' : 'Copy'}</span>
                    </button>
                </span>

                {title && title !== domain && (
                    <span className="inline-pill__popover-title">{title}</span>
                )}

                <span className="inline-pill__popover-url" title={href}>
                    {href}
                </span>

                <button
                    type="button"
                    className="inline-pill__popover-open-action"
                    onClick={handleOpen}
                >
                    <span>Open in browser</span>
                    <ExternalLink size={11} />
                </button>
            </span>
        </span>
    );
}
