import { useEffect, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

export interface SavedConnection { id?: string; name: string; provider: string; base_url: string; api_key?: string; hosting?: string }
export default function SavedConnections({ onChoose }: { onChoose: (connection: SavedConnection) => void }) {
    const [saved, setSaved] = useState<SavedConnection[]>([]);
    const [error, setError] = useState('');
    const api = window.pywebview?.api;
    useEffect(() => {
        let active = true;
        if (api?.list_setup_connections) setupRequest(api.list_setup_connections()).then(value => {
            if (active && Array.isArray(value)) setSaved(value as SavedConnection[]);
        }).catch(() => {});
        return () => { active = false; };
    }, [api]);
    if (!saved.length) return null;
    const choose = async (id: string) => {
        try {
            if (!api?.get_setup_connection) throw new Error();
            const result = await setupRequest(api.get_setup_connection(id)) as SavedConnection;
            if (!result?.name || !result.base_url) throw new Error();
            onChoose(result);
        } catch { setError('Saved connection unavailable. Enter it manually.'); }
    };
    return <details><summary>Previously used connections</summary><div className="setup-details setup-stack">
        {saved.map(connection => <button key={connection.id} style={styles.secondaryBtn} onClick={() => void choose(connection.id!)}>
            {connection.name} · {connection.provider}
        </button>)}
        {error && <p role="alert">{error}</p>}
    </div></details>;
}
