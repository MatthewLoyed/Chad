import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, Check } from 'lucide-react';
import './Dropdown.css';

export interface DropdownOption {
    value: string;
    label: string;
}

interface DropdownProps {
    value: string;
    options: DropdownOption[];
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    className?: string;
}

export default function Dropdown({
    value,
    options,
    onChange,
    placeholder = 'Select...',
    disabled = false,
    className = ''
}: DropdownProps) {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    // Get selected label
    const selectedOption = options.find(opt => opt.value === value);
    const displayLabel = selectedOption ? selectedOption.label : placeholder;

    // Toggle dropdown
    const handleToggle = () => {
        if (!disabled) setIsOpen(!isOpen);
    };

    // Select option
    const handleSelect = (optionValue: string) => {
        onChange(optionValue);
        setIsOpen(false);
    };

    // Close on click outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };

        if (isOpen) {
            document.addEventListener('mousedown', handleClickOutside);
        }
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [isOpen]);

    return (
        <div
            className={`chad-dropdown ${className} ${isOpen ? 'chad-dropdown--open' : ''} ${disabled ? 'chad-dropdown--disabled' : ''}`}
            ref={containerRef}
        >
            <div className="chad-dropdown__trigger" onClick={handleToggle}>
                <span className="chad-dropdown__label">{displayLabel}</span>
                <ChevronDown
                    size={16}
                    className={`chad-dropdown__icon ${isOpen ? 'chad-dropdown__icon--open' : ''}`}
                />
            </div>

            <AnimatePresence>
                {isOpen && (
                    <motion.div
                        className="chad-dropdown__menu"
                        initial={{ opacity: 0, y: -5, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: -5, scale: 0.98 }}
                        transition={{ duration: 0.15, ease: "easeOut" }}
                    >
                        <div className="chad-dropdown__scroll">
                            {options.map((option) => (
                                <div
                                    key={option.value}
                                    className={`chad-dropdown__option ${option.value === value ? 'chad-dropdown__option--selected' : ''}`}
                                    onClick={() => handleSelect(option.value)}
                                >
                                    <span className="chad-dropdown__option-text">{option.label}</span>
                                    {option.value === value && (
                                        <Check size={14} className="chad-dropdown__check" />
                                    )}
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
