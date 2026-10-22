import type { ReactNode } from 'react';
import { FileText, Globe, File, FileCode, Image, Video } from 'lucide-react';
import { getFileExtension } from './artifactFiles';

export interface FileTypeInfo {
    icon: ReactNode;
    label: string;
    colorClass: string;
    themeColor: string;
    canPreview: boolean;
}

export const getFileTypeInfo = (filename: string, toolName?: string): FileTypeInfo => {
    const extension = getFileExtension(filename);

    switch (extension) {
        case 'docx':
        case 'doc':
            return {
                icon: <FileText className="artifact-icon--word" />,
                label: 'Word Document',
                colorClass: 'artifact-card--word',
                themeColor: '#2b579a',
                canPreview: true,
            };
        case 'html':
        case 'htm':
            return {
                icon: <Globe className="artifact-icon--html" />,
                label: 'HTML File',
                colorClass: 'artifact-card--html',
                themeColor: '#e34c26',
                canPreview: true,
            };
        case 'pdf':
            return {
                icon: <FileText className="artifact-icon--pdf" />,
                label: 'PDF Document',
                colorClass: 'artifact-card--pdf',
                themeColor: '#f40f02',
                canPreview: true,
            };
        case 'md':
        case 'markdown':
            return {
                icon: <FileText className="artifact-icon--markdown" />,
                label: 'Markdown',
                colorClass: 'artifact-card--markdown',
                themeColor: '#38bdf8',
                canPreview: true,
            };
        case 'txt':
            return {
                icon: <FileText className="artifact-icon--text" />,
                label: 'Text File',
                colorClass: 'artifact-card--text',
                themeColor: '#94a3b8',
                canPreview: true,
            };
        case 'png':
        case 'jpg':
        case 'jpeg':
        case 'gif':
        case 'webp':
        case 'svg':
            return {
                icon: <Image className="artifact-icon--image" />,
                label: extension === 'svg' ? 'SVG Diagram' : 'Image',
                colorClass: 'artifact-card--image',
                themeColor: '#a78bfa',
                canPreview: true,
            };
        case 'mp4':
        case 'webm':
        case 'mov':
            return {
                icon: <Video className="artifact-icon--video" />,
                label: 'Video',
                colorClass: 'artifact-card--video',
                themeColor: '#f43f5e',
                canPreview: true,
            };
        case 'pptx':
            return {
                icon: <FileText className="artifact-icon--pptx" />,
                label: 'Presentation',
                colorClass: 'artifact-card--pptx',
                themeColor: '#d24726',
                canPreview: false,
            };
        case 'csv':
        case 'xlsx':
        case 'xls':
            return {
                icon: <FileText className="artifact-icon--excel" />,
                label: 'Spreadsheet',
                colorClass: 'artifact-card--excel',
                themeColor: '#217346',
                canPreview: true,
            };
        case 'json':
        case 'xml':
        case 'yaml':
        case 'yml':
            return {
                icon: <FileCode className="artifact-icon--code" />,
                label: 'Data File',
                colorClass: 'artifact-card--code',
                themeColor: '#f59e0b',
                canPreview: true,
            };
        default:
            return {
                icon: <File className="artifact-icon--default" />,
                label:
                    toolName?.replace(/build_/, '').replace(/_/g, ' ').toUpperCase() || 'File',
                colorClass: 'artifact-card--default',
                themeColor: '#818cf8',
                canPreview: false,
            };
    }
};
