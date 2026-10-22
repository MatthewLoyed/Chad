import { Download, X } from 'lucide-react';
import ArtifactViewer from '../data-display/ArtifactViewer';
import { downloadArtifact } from '../data-display/artifactFiles';
import type { OpenArtifactTarget } from '../../store';
import './ArtifactSidePanel.css';

interface ArtifactSidePanelProps {
    artifact: OpenArtifactTarget;
    onClose: () => void;
}

export default function ArtifactSidePanel({ artifact, onClose }: ArtifactSidePanelProps) {
    const { token, filename, serverUrl } = artifact;

    return (
        <aside className="artifact-side-panel" aria-label="Artifact preview">
            <header className="artifact-side-panel__header">
                <h3 className="artifact-side-panel__title" title={filename}>
                    {filename}
                </h3>
                <div className="artifact-side-panel__actions">
                    <button
                        type="button"
                        className="artifact-side-panel__btn"
                        onClick={() => downloadArtifact(token, filename, serverUrl)}
                        title="Save to system"
                    >
                        <Download size={16} />
                        <span>Save</span>
                    </button>
                    <button
                        type="button"
                        className="artifact-side-panel__btn artifact-side-panel__btn--close"
                        onClick={onClose}
                        title="Close preview"
                    >
                        <X size={18} />
                    </button>
                </div>
            </header>
            <div className="artifact-side-panel__body">
                <ArtifactViewer token={token} filename={filename} serverUrl={serverUrl} />
            </div>
        </aside>
    );
}
