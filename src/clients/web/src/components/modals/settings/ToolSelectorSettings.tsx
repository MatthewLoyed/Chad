import { useEffect, useState } from 'react';
import type { LocalModelInfo } from '../../../store';

export default function ToolSelectorSettings({ models }: { models: LocalModelInfo[] }) {
    const [selected, setSelected] = useState('');
    const [saved, setSaved] = useState('');
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const api = window.pywebview?.api;

    useEffect(() => {
        let mounted = true;
        api?.get_tool_selector_model?.().then((result: unknown) => {
            const value = result as { model: string; provider: string } | null;
            if (!mounted || !value) return;
            const key = `${value.provider}:${value.model}`;
            setSelected(key);
            setSaved(key);
        }).catch(() => { if (mounted) setMessage('Could not read the tool selector setting.'); });
        return () => { mounted = false; };
    }, [api]);

    const options = models.filter(m => ['lmstudio', 'ollama', 'custom'].includes(m.provider));
    return <section className="models-tab__section">
        <h3 className="models-tab__heading">Tool selector model</h3>
        <p className="models-tab__section-desc">
            Local model that chooses tools before the main chat model responds. Changes apply on the next tool selection.
        </p>
        <label htmlFor="tool-selector-model">Model</label>
        <div className="models-tab__routine-row">
        <select className="models-tab__routine-select" id="tool-selector-model" value={selected} disabled={busy}
            onChange={e => { setSelected(e.target.value); setMessage(''); }}>
            {!selected && <option value="">Choose a local model</option>}
            {selected && !options.some(m => `${m.provider}:${m.name}` === selected) &&
                <option value={selected}>{selected} (configured)</option>}
            {options.map(m => <option key={`${m.provider}:${m.name}`} value={`${m.provider}:${m.name}`}>
                {m.display_name || m.name} ({m.provider})
            </option>)}
        </select>
        <button className="models-tab__refresh" type="button" disabled={busy || !selected || selected === saved}
            onClick={async () => {
                if (!api?.set_tool_selector_model) return;
                setBusy(true);
                try {
                    const split = selected.indexOf(':');
                    const ok = await api.set_tool_selector_model(selected.slice(split + 1), selected.slice(0, split));
                    if (ok === true) { setSaved(selected); setMessage('Tool selector model saved.'); }
                    else setMessage('Could not save the tool selector model. Try again.');
                } catch { setMessage('Could not save the tool selector model. Check the Brain connection.'); }
                finally { setBusy(false); }
            }}>{busy ? 'Saving…' : 'Save tool selector'}</button>
        </div>
        {options.length === 0 && <p>Rescan local models to choose a tool selector.</p>}
        {message && <p role="status">{message}</p>}
    </section>;
}
