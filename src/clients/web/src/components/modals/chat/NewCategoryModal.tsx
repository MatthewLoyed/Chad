import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import Modal from '../base/Modal';
import './NewCategoryModal.css';

/**
 * Starter templates for the New Category form.
 *
 * These pre-fill an EDITABLE persona description — they are a writing prompt, not a canned
 * agent. Chad ships with no categories: the value of a domain agent comes from the user's
 * own data accumulating inside it, so a preset "Financial advisor" persona would be generic
 * filler until it has been used. Keep these short and second-person.
 */
export interface CategoryTemplate {
    key: string;
    name: string;
    icon: string;
    color: string;
    persona: string;
}

const CATEGORY_TEMPLATES: CategoryTemplate[] = [
    {
        key: 'financial',
        name: 'Financial',
        icon: '💰',
        color: '#4ade80',
        persona:
            'You help me manage my money: budgeting, spending, saving, and planning ahead. '
            + 'Remember my accounts, recurring bills, and financial goals as I mention them. '
            + 'Be concrete and numbers-first. You are not a licensed advisor — flag when '
            + 'something warrants a real professional.',
    },
    {
        key: 'fitness',
        name: 'Fitness',
        icon: '🏋️',
        color: '#f97316',
        persona:
            'You help me with training, nutrition, and recovery. Remember my current program, '
            + 'lifts, injuries, and goals. Be practical and progressive — favour consistency '
            + 'over intensity, and flag anything that sounds like it needs a doctor.',
    },
    {
        key: 'school',
        name: 'School',
        icon: '🎓',
        color: '#60a5fa',
        persona:
            'You help me with coursework: assignments, deadlines, exam prep, and understanding '
            + 'hard material. Remember my classes, professors, and due dates. Explain concepts '
            + 'rather than just handing me answers.',
    },
    {
        key: 'research',
        name: 'Research',
        icon: '🔬',
        color: '#a78bfa',
        persona:
            'You help me investigate topics in depth. Remember the threads I am pursuing and '
            + 'what I have already ruled out. Cite sources, separate established fact from '
            + 'speculation, and tell me when the evidence is thin.',
    },
    {
        key: 'project',
        name: 'Project',
        icon: '🚀',
        color: '#22d3ee',
        persona:
            'You help me build and ship this project. Remember its architecture, decisions '
            + 'already made, and what is currently blocked. Be direct about tradeoffs and '
            + 'push back when an approach will cause problems later.',
    },
];

const PRESET_COLORS = ['#4ade80', '#f97316', '#60a5fa', '#a78bfa', '#22d3ee', '#f472b6', '#facc15'];

interface NewCategoryModalProps {
    isOpen: boolean;
    onClose: () => void;
    onCreate: (payload: {
        name: string; description: string; icon: string; color: string; persona: string;
    }) => Promise<void> | void;
}

export default function NewCategoryModal(props: NewCategoryModalProps) {
    return props.isOpen ? <NewCategoryForm {...props} /> : null;
}

function NewCategoryForm({ isOpen, onClose, onCreate }: NewCategoryModalProps) {
    const [templateKey, setTemplateKey] = useState<string | null>(null);
    const [name, setName] = useState('');
    const [icon, setIcon] = useState('📁');
    const [color, setColor] = useState(PRESET_COLORS[0]);
    const [persona, setPersona] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const applyTemplate = (template: CategoryTemplate | null) => {
        if (!template) {
            setTemplateKey(null);
            return;
        }
        setTemplateKey(template.key);
        setName(template.name);
        setIcon(template.icon);
        setColor(template.color);
        setPersona(template.persona);
    };

    const handleCreate = async () => {
        const trimmed = name.trim();
        if (!trimmed) {
            setError('Give the category a name.');
            return;
        }
        setSaving(true);
        setError(null);
        try {
            await onCreate({ name: trimmed, description: '', icon, color, persona: persona.trim() });
            onClose();
        } catch (err) {
            console.error('Failed to create category:', err);
            setError('Could not create the category.');
            setSaving(false);
        }
    };

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="New category" className="new-category-modal">
            <p className="new-category-hint">
                A category keeps its own chats and its own memory. What you tell a Financial
                category stays out of every other conversation.
            </p>

            <label className="new-category-label">Start from</label>
            <div className="new-category-templates">
                <button
                    type="button"
                    className={`new-category-template ${templateKey === null ? 'is-selected' : ''}`}
                    onClick={() => applyTemplate(null)}
                >
                    <span className="new-category-template__icon">✏️</span>
                    <span>Blank</span>
                </button>
                {CATEGORY_TEMPLATES.map((template) => (
                    <button
                        key={template.key}
                        type="button"
                        className={`new-category-template ${templateKey === template.key ? 'is-selected' : ''}`}
                        onClick={() => applyTemplate(template)}
                    >
                        <span className="new-category-template__icon">{template.icon}</span>
                        <span>{template.name}</span>
                    </button>
                ))}
            </div>

            <label className="new-category-label" htmlFor="new-category-name">Name</label>
            <div className="new-category-name-row">
                <input
                    className="new-category-icon-input"
                    value={icon}
                    maxLength={2}
                    aria-label="Category icon"
                    onChange={(e) => setIcon(e.target.value)}
                />
                <input
                    id="new-category-name"
                    className="new-category-input"
                    value={name}
                    placeholder="Financial"
                    autoFocus
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); }}
                />
            </div>

            <label className="new-category-label">Colour</label>
            <div className="new-category-colors">
                {PRESET_COLORS.map((preset) => (
                    <button
                        key={preset}
                        type="button"
                        aria-label={`Colour ${preset}`}
                        className={`new-category-color ${color === preset ? 'is-selected' : ''}`}
                        style={{ background: preset }}
                        onClick={() => setColor(preset)}
                    />
                ))}
            </div>

            <label className="new-category-label" htmlFor="new-category-persona">
                How should Chad act here?
            </label>
            <textarea
                id="new-category-persona"
                className="new-category-textarea"
                value={persona}
                rows={6}
                placeholder="You help me manage my money: budgeting, spending, and planning ahead..."
                onChange={(e) => setPersona(e.target.value)}
            />

            {error && <p className="new-category-error">{error}</p>}

            <div className="new-category-footer">
                <button type="button" className="new-category-btn" onClick={onClose} disabled={saving}>
                    Cancel
                </button>
                <button
                    type="button"
                    className="new-category-btn new-category-btn--primary"
                    onClick={handleCreate}
                    disabled={saving || !name.trim()}
                >
                    {saving ? <Loader2 size={14} className="animate-spin" /> : null}
                    {saving ? 'Creating...' : 'Create category'}
                </button>
            </div>
        </Modal>
    );
}
