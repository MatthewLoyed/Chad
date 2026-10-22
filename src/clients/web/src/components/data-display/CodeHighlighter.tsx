import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

// Isolated so react-syntax-highlighter (a very large dep) can be code-split
// out of the first-paint bundle and only fetched when a code block renders.
export default function CodeHighlighter({ language, code }: { language: string; code: string }) {
    return (
        <SyntaxHighlighter
            PreTag="div"
            language={language}
            style={vscDarkPlus}
            customStyle={{ margin: 0, borderRadius: '0 0 8px 8px' }}
        >
            {code}
        </SyntaxHighlighter>
    );
}
