import { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Loader2, ChevronRight } from 'lucide-react';
import { readCache, writeCache } from '../../utils/localCache';
import './SkillsCatalog.css';

interface SkillEntry {
    name: string;
    description: string;
    version?: string;
    tags?: string[];
    subagent?: string | null;
    enabled?: boolean;
}

/** Read-only catalog of domain expertise guides Chad loads on demand. */
export default function SkillsCatalog({ onUseSkill }: { onUseSkill?: (name: string) => void }) {
    const [skills, setSkills] = useState<SkillEntry[]>(() => {
        return readCache<SkillEntry[]>('skills_catalog') ?? [];
    });
    const [loading, setLoading] = useState(() => !readCache<SkillEntry[]>('skills_catalog'));
    const [selected, setSelected] = useState<string | null>(null);
    const [content, setContent] = useState<string | null>(null);
    const [contentLoading, setContentLoading] = useState(false);

    const fetchSkills = useCallback(async () => {
        const api = window.pywebview?.api;
        if (!api?.get_skills_catalog) {
            setLoading(false);
            return;
        }
        if (!readCache('skills_catalog')) {
            setLoading(true);
        }
        try {
            const result = await api.get_skills_catalog();
            const list = Array.isArray(result) ? (result as SkillEntry[]) : [];
            setSkills(list);
            writeCache('skills_catalog', list);
        } catch (e) {
            console.error('[Skills] load failed:', e);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchSkills();
    }, [fetchSkills]);

    const openSkill = async (name: string) => {
        setSelected(name);
        setContent(null);
        const api = window.pywebview?.api;
        if (!api?.get_skill_content) return;
        setContentLoading(true);
        try {
            const res = (await api.get_skill_content(name)) as {
                success?: boolean;
                content?: string;
            };
            if (res?.success && res.content) setContent(res.content);
            else setContent('Could not load skill content.');
        } finally {
            setContentLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="skills-empty">
                <Loader2 size={22} className="animate-spin" />
                <span>Loading skills…</span>
            </div>
        );
    }

    return (
        <div className="skills-layout">
            <div className="skills-list">
                {skills.length === 0 ? (
                    <p className="skills-empty">No skills in catalog.</p>
                ) : (
                    skills.map((s) => (
                        <button
                            key={s.name}
                            type="button"
                            className={`skills-card ${selected === s.name ? 'is-active' : ''}`}
                            onClick={() => openSkill(s.name)}
                        >
                            <div className="skills-card__head">
                                <span className="skills-card__name">/{s.name.replaceAll('_', '-')}</span>
                                <ChevronRight size={14} />
                            </div>
                            <p className="skills-card__desc">{s.description}</p>
                            {s.tags && s.tags.length > 0 && (
                                <div className="skills-card__tags">
                                    {s.tags.slice(0, 4).map((t) => (
                                        <span key={t} className="skills-tag">
                                            {t}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </button>
                    ))
                )}
            </div>
            <div className="skills-preview">
                {selected && onUseSkill && (
                    <button type="button" className="ws-btn" onClick={() => onUseSkill(selected)}>
                        Use /{selected.replaceAll('_', '-')} in chat
                    </button>
                )}
                {!selected ? (
                    <p className="skills-preview__placeholder">
                        Select a skill, then use it in chat with your inputs.
                        For example, /microsoft-assignment 5 sets up assignment 5.
                    </p>
                ) : contentLoading ? (
                    <div className="skills-empty">
                        <Loader2 size={18} className="animate-spin" />
                    </div>
                ) : (
                    <motion.pre
                        key={selected}
                        className="skills-preview__body"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                    >
                        {content}
                    </motion.pre>
                )}
            </div>
        </div>
    );
}
