import { useEffect, useRef } from 'react';
import { BookOpen, Zap } from 'lucide-react';
import type { useSlashCommands } from './useSlashCommands';

export default function SlashCommandMenu({ menu }: { menu: ReturnType<typeof useSlashCommands> }) {
    const menuRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        menuRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' });
    }, [menu.active]);
    if (!menu.matches.length) return null;
    return <div ref={menuRef} className="chat-input__fast-track-menu" role="listbox" aria-label="Slash commands">
        {menu.matches.map((option, index) => <button
            type="button" role="option" aria-selected={index === menu.active}
            key={option.alias} className="chat-input__fast-track-item"
            onClick={() => menu.select(option)}
        >
            {option.kind === 'tool' ? <Zap size={14} /> : <BookOpen size={14} />}
            <span className="chat-input__fast-track-alias">/{option.alias}</span>
            <span className="chat-input__fast-track-label">{option.label}</span>
        </button>)}
    </div>;
}
