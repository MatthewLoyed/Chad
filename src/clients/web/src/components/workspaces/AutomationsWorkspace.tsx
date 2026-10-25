import { Zap } from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import AutomationsTab from '../modals/settings/AutomationsTab';
import RoutinesWorkspace from './RoutinesWorkspace';
import { useAppStore } from '../../store';
import './AutomationsWorkspace.css';

export default function AutomationsWorkspace() {
    const saved = useAppStore((s) => s.activeWorkspace === 'routines');
    const navigate = useAppStore((s) => s.setActiveWorkspace);
    return (
        <section className="automations-workspace">
            <nav className="automations-workspace__nav" aria-label="Automations views">
                <button type="button" aria-current={!saved ? 'page' : undefined} onClick={() => navigate('automations')}>Schedules & triggers</button>
                <button type="button" aria-current={saved ? 'page' : undefined} onClick={() => navigate('routines')}>Saved actions</button>
            </nav>
            <div className="automations-workspace__view" hidden={!saved}>
                <RoutinesWorkspace />
            </div>
            <div className="automations-workspace__view" hidden={saved}>
                <WorkspacePane
                    icon={Zap}
                    title="Automations"
                    subtitle="Schedule tasks and one-time reminders, or choose what Chad checks in the background."
                >
                    <AutomationsTab />
                </WorkspacePane>
            </div>
        </section>
    );
}
