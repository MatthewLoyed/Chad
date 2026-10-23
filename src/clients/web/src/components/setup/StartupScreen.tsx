import { Loader2 } from 'lucide-react';
import './StartupScreen.css';
import { styles } from './SetupWizard.styles';

export default function StartupScreen({ failed, onRetry }: { failed?: boolean; onRetry?: () => void }) {
    return <div style={styles.overlay}>
        <section className="startup-card" style={styles.card} role="status" aria-live="polite">
            {!failed && <Loader2 size={24} className="startup-spinner" aria-hidden="true" />}
            <h1 style={styles.title}>{failed ? 'Chad could not finish starting' : 'Starting Chad...'}</h1>
            <p style={styles.hint}>{failed
                ? 'The local service has not responded yet. Try again. If it keeps happening, close Chad and open it again.'
                : 'Getting things ready. The first launch can take a little longer.'}</p>
            {!failed && <div className="startup-progress" role="progressbar" aria-label="Starting Chad"><span /></div>}
            {failed && <button style={styles.primaryBtn} onClick={onRetry}>Try again</button>}
        </section>
    </div>;
}
