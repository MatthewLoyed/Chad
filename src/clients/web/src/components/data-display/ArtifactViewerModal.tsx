import { Download } from 'lucide-react';
import Modal from '../modals/base/Modal';
import ArtifactViewer from './ArtifactViewer';
import { downloadArtifact } from './artifactFiles';
import './ArtifactViewer.css';

interface ArtifactViewerModalProps {
    isOpen: boolean;
    onClose: () => void;
    filename: string;
    token: string;
    serverUrl?: string | null;
}

export default function ArtifactViewerModal({
    isOpen,
    onClose,
    filename,
    token,
    serverUrl,
}: ArtifactViewerModalProps) {
    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={filename}
            className="artifact-viewer-modal"
            bodyClassName="artifact-viewer-modal__body"
        >
            <ArtifactViewer token={token} filename={filename} serverUrl={serverUrl} />
            <div className="artifact-viewer-modal__footer">
                <button
                    type="button"
                    className="artifact-viewer-modal__download"
                    onClick={() => downloadArtifact(token, filename, serverUrl)}
                >
                    <Download size={16} />
                    Save
                </button>
            </div>
        </Modal>
    );
}
