import { useAppStore, type OpenArtifactTarget } from '../../store';

export function openArtifactPreview(artifact: OpenArtifactTarget): void {
    useAppStore.getState().openArtifactViewer(artifact);
}
