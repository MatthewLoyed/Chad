/** Tight crop — the art only spans ~17×19 user units; the old viewBox wasted ~70% as padding. */
const VIEW_BOX = '-3 -5 21 23';

export function ChadBuddySvg({ size }: { size: number }) {
    return (
        <svg xmlns="http://www.w3.org/2000/svg" viewBox={VIEW_BOX} width={size} height={size}>
            <g className="chad-avatar__shadow">
                <rect x="2" y="15" width="11" height="1" fill="#000000" opacity="0.45" />
            </g>
            <g fill="#5B21B6">
                <rect x="2" y="11" width="1" height="4" />
                <rect x="5" y="11" width="1" height="4" />
                <rect x="10" y="11" width="1" height="4" />
                <rect x="13" y="11" width="1" height="4" />
            </g>
            <g className="chad-avatar__body">
                <g className="chad-avatar__breathe">
                    <g>
                        <rect x="-1" y="3" width="17" height="1" fill="#0F172A" />
                        <rect x="3" y="-3" width="9" height="6" fill="#0F172A" />
                        <rect x="3" y="1" width="9" height="1" fill="#A78BFA" />
                    </g>
                    <rect x="1" y="5" width="13" height="7" fill="#7C3AED" />
                    <rect x="3" y="12" width="9" height="1" fill="#6D28D9" />
                    <rect x="-1" y="8" width="2" height="3" fill="#7C3AED" />
                    <rect x="14" y="8" width="2" height="3" fill="#7C3AED" />
                    <g fill="#0F172A">
                        <g className="chad-avatar__eyes-blink">
                            <rect x="4" y="7" width="1" height="2" />
                            <rect x="10" y="7" width="1" height="2" />
                        </g>
                    </g>
                    <rect x="3" y="6" width="3" height="1" fill="#5B21B6" opacity="0.7" />
                    <rect x="9" y="6" width="3" height="1" fill="#5B21B6" opacity="0.7" />
                </g>
            </g>
        </svg>
    );
}
