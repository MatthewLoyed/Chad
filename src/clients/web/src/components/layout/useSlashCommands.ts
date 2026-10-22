import { useEffect, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useAppStore } from '../../store';
import type { FastTrackCommand } from '../../store';
import { readCache, writeCache } from '../../utils/localCache';

interface SlashSkill { name: string; description: string; enabled?: boolean }
interface SlashOption { alias: string; label: string; kind: 'skill' | 'tool' | 'browse' }
export const skillAlias = (name: string) => name.toLowerCase().replaceAll('_', '-');

export function useSlashCommands(value: string, onSelect: (text: string) => void, commands: FastTrackCommand[]) {
    const apiReady = useAppStore(s => s.apiReady);
    const [skills, setSkills] = useState<SlashSkill[]>(() => readCache<SlashSkill[]>('skills_catalog') || []);
    const [active, setActive] = useState(-1);
    const [dismissed, setDismissed] = useState<string | null>(null);
    const isSlash = /^\/[\w-]*$/.test(value);
    useEffect(() => {
        if (!apiReady || !isSlash) return;
        let cancelled = false;
        const api = window.pywebview?.api;
        if (api?.get_skills_catalog) {
            Promise.resolve(api.get_skills_catalog()).then(result => {
                if (!cancelled && Array.isArray(result)) {
                    setSkills(result as SlashSkill[]);
                    writeCache('skills_catalog', result);
                }
            }).catch(() => { /* Cached entries remain available; Brain validates invocations. */ });
        }
        return () => { cancelled = true; };
    }, [apiReady, isSlash]);
    const [previousValue, setPreviousValue] = useState(value);
    if (previousValue !== value) {
        setPreviousValue(value);
        setActive(-1);
    }

    const options = new Map<string, SlashOption>();
    commands.forEach(c => options.set(c.alias, { alias: c.alias, label: c.label, kind: 'tool' }));
    skills.filter(s => s.enabled !== false).forEach(s => options.set(skillAlias(s.name), {
        alias: skillAlias(s.name), label: s.description, kind: 'skill',
    }));
    options.set('skills', { alias: 'skills', label: 'Browse available skills', kind: 'browse' });
    const matches = isSlash && dismissed !== value
        ? [...options.values()].filter(o => o.alias.startsWith(value.slice(1).toLowerCase()))
        : [];
    const select = (option: SlashOption) => {
        const text = `/${option.alias}${option.kind === 'skill' ? ' ' : ''}`;
        setDismissed(text);
        setActive(-1);
        onSelect(text);
    };
    const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (!matches.length || event.shiftKey) return false;
        if (event.key === 'Escape') {
            setDismissed(value); event.preventDefault(); return true;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            const next = event.key === 'ArrowDown' ? active + 1 : (active < 0 ? matches.length - 1 : active - 1);
            setActive((next + matches.length) % matches.length);
            event.preventDefault(); return true;
        }
        if (event.key === 'Tab' || (event.key === 'Enter' && active >= 0)) {
            select(matches[Math.min(Math.max(active, 0), matches.length - 1)]);
            event.preventDefault(); return true;
        }
        return false;
    };
    return { matches, active, select, onKeyDown };
}
