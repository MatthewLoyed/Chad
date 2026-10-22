import { Zap } from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import AutomationsTab from '../modals/settings/AutomationsTab';

export default function AutomationsWorkspace() {
    return (
        <WorkspacePane
            icon={Zap}
            title="Automations"
            subtitle="Background daemons, heartbeat, webhooks, scheduled tasks, and autonomous run history."
        >
            <AutomationsTab />
        </WorkspacePane>
    );
}
