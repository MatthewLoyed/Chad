import { useEffect, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

interface Profile { status: string; suggested: Record<string, unknown>; note: string; source?: string }
export default function ProviderSettings({ modelId, provider }: { modelId: string; provider: string }) {
    const [profile, setProfile] = useState<Profile | null>(null);
    const api = window.pywebview?.api;
    useEffect(() => {
        let active = true;
        setProfile(null);
        if (api?.get_model_settings_profile) {
            setupRequest(api.get_model_settings_profile(modelId, 8192, provider))
                .then(result => { if (active) setProfile(result as Profile); }).catch(() => {});
        }
        return () => { active = false; };
    }, [api, modelId, provider]);
    return <details><summary>Model settings and compatibility</summary><div className="setup-details">
        <p style={styles.hint}>{profile?.note || 'Keep the model server’s settings. Context, sampling, thinking and MTP are not verified by the chat test. Chad preserves your saved overrides.'}</p>
        {profile?.status === 'suggested' && <>
            <p style={styles.hint}>Suggested context: {String(profile.suggested.context_length)} tokens · parallel predictions: 1 · evaluation batch: at most 512.</p>
            <p style={styles.hint}>MTP / speculative decoding: off in the curated profile. Thinking: off for the initial test. Apply compatible settings in your model app, then retest.</p>
            <p style={styles.hint}>These are suggestions. Configured values and effective server settings have not been inspected here.</p>
            {profile.source && <a href={profile.source} target="_blank" rel="noreferrer">Model publisher / artifact source</a>}
        </>}
    </div></details>;
}
