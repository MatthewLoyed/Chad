import { describe, it, expect } from 'vitest';
import { getFileTypeInfo } from './artifactTypes';

describe('artifactTypes', () => {
    it('correctly classifies mp4 video files', () => {
        const info = getFileTypeInfo('explainer_motion.mp4');
        expect(info.label).toBe('Video');
        expect(info.colorClass).toBe('artifact-card--video');
        expect(info.themeColor).toBe('#f43f5e');
        expect(info.canPreview).toBe(true);
    });

    it('correctly classifies webm video files', () => {
        const info = getFileTypeInfo('animation.webm');
        expect(info.label).toBe('Video');
        expect(info.colorClass).toBe('artifact-card--video');
        expect(info.canPreview).toBe(true);
    });

    it('correctly classifies html files', () => {
        const info = getFileTypeInfo('motion.html');
        expect(info.label).toBe('HTML File');
        expect(info.canPreview).toBe(true);
    });
});
