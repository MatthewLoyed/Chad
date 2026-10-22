import type { ReactNode } from 'react';
import { 
    FaFileSignature, FaStickyNote, FaSearch, FaBrain, FaBolt, 
    FaCheckCircle, FaUsers, FaCalendarAlt, FaFolder, 
    FaTools, FaListAlt, FaSitemap, FaDiscord, FaGithub, FaDatabase,
    FaMousePointer, FaArrowsAlt, FaImages, FaMapMarkerAlt,
    FaWallet, FaCreditCard, FaChartLine
} from 'react-icons/fa';
import { 
    SiGoogledocs, SiYoutube, SiGoogle, SiGooglechrome, 
    SiGmail, SiTodoist, SiBrave, SiSlack, SiTelegram 
} from 'react-icons/si';
import type { ThinkingEvent } from '../store';
import { CanvasIcon } from '../components/common/CanvasIcon';

export interface ToolUIDisplay {
    title: string;
    icon: ReactNode;
    color: string;
    category: string;
}

const TOOL_METADATA: Record<string, { title: string; icon: ReactNode; color: string; category: string }> = {
    google_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    search_web: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    search_web_brave: { title: 'Searching Brave', icon: <SiBrave color="#FB542B" />, color: '#FB542B', category: 'browser' },
    open_browser: { title: 'Opening browser', icon: <SiGooglechrome color="#4285F4" />, color: '#4285F4', category: 'browser' },
    browser_subagent: { title: 'Automating browser', icon: <SiGooglechrome color="#34A853" />, color: '#34A853', category: 'browser' },
    browser_scroll: { title: 'Scrolling page', icon: <SiGooglechrome color="#34A853" />, color: '#34A853', category: 'browser' },
    browser_capture_text: { title: 'Capturing exact text', icon: <SiGooglechrome color="#34A853" />, color: '#34A853', category: 'browser' },
    open_url: { title: 'Opening browser', icon: <SiGooglechrome color="#4285F4" />, color: '#4285F4', category: 'browser' },
    read_url_content: { title: 'Retrieving webpage content', icon: <SiGooglechrome color="#4285F4" />, color: '#4285F4', category: 'browser' },
    write_file: { title: 'Writing file', icon: <FaFileSignature color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    write_to_file: { title: 'Writing file', icon: <FaFileSignature color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    replace_file_content: { title: 'Editing file', icon: <FaFileSignature color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    multi_replace_file_content: { title: 'Multi-editing file', icon: <FaFileSignature color="#10B981" />, color: '#10B981', category: 'filesystem' },
    read_file: { title: 'Reading file', icon: <FaStickyNote color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    read_artifact: { title: 'Reading artifact', icon: <FaStickyNote color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    read_pdf: { title: 'Reading PDF', icon: <FaStickyNote color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    read_local_file: { title: 'Reading file', icon: <FaStickyNote color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    view_file: { title: 'Reading file', icon: <FaStickyNote color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    fuzzy_find_local: { title: 'Locating files', icon: <FaSearch color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    list_directory: { title: 'Listing directory contents', icon: <FaSitemap color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    propose_file_plan: { title: 'Planning directory organization', icon: <FaListAlt color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    execute_approved_plan: { title: 'Executing file reorganization', icon: <FaBolt color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    file_manager: { title: 'Managing files', icon: <FaTools color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
    list_dir: { title: 'Scanning folder', icon: <FaFolder color="#F59E0B" />, color: '#F59E0B', category: 'filesystem' },
    run_command: { title: 'Executing command', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'filesystem' },
    hubspace_control: { title: 'Controlling smart home', icon: <FaBolt color="#EAB308" />, color: '#EAB308', category: 'smart_home' },
    get_canvas_notifications: { title: 'Retrieving notifications', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_canvas_courses: { title: 'Retrieving courses', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_canvas_assignments: { title: 'Retrieving assignments', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_assignment_submission: { title: 'Retrieving assignment submission', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_upcoming_assignments: { title: 'Retrieving upcoming assignments', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_canvas_calendar_events: { title: 'Retrieving calendar events', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_canvas_activity_stream: { title: 'Retrieving activity stream', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_course_announcements: { title: 'Retrieving course announcements', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_course_grades: { title: 'Retrieving course grades', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_recent_grades: { title: 'Retrieving recent grades', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_course_quizzes: { title: 'Retrieving course quizzes', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    get_course_discussions: { title: 'Retrieving discussions', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    read_discussion_topic: { title: 'Reading discussion topic', icon: <CanvasIcon />, color: '#E13939', category: 'canvas' },
    manage_connectors: { title: 'Managing connectors', icon: <FaBolt color="#6366F1" />, color: '#6366F1', category: 'system' },
    get_financial_summary: { title: 'Calculating net worth & balances', icon: <FaWallet color="#10B981" />, color: '#10B981', category: 'finance' },
    get_recent_transactions: { title: 'Fetching transactions', icon: <FaCreditCard color="#10B981" />, color: '#10B981', category: 'finance' },
    get_investment_portfolio: { title: 'Fetching investment holdings', icon: <FaChartLine color="#10B981" />, color: '#10B981', category: 'finance' },
    create_finance_link_token: { title: 'Creating bank link session', icon: <FaBolt color="#10B981" />, color: '#10B981', category: 'finance' },
    get_recurring_subscriptions: { title: 'Checking recurring bills & subscriptions', icon: <FaCalendarAlt color="#10B981" />, color: '#10B981', category: 'finance' },
    get_liabilities: { title: 'Checking credit card due dates & debt', icon: <FaCreditCard color="#EF4444" />, color: '#EF4444', category: 'finance' },
    get_investment_transactions: { title: 'Fetching investment trades & dividends', icon: <FaChartLine color="#10B981" />, color: '#10B981', category: 'finance' },
    refresh_financial_data: { title: 'Refreshing live bank data', icon: <FaBolt color="#10B981" />, color: '#10B981', category: 'finance' },
    search_gmail: { title: 'Searching Gmail', icon: <SiGmail color="#EA4335" />, color: '#EA4335', category: 'email' },
    read_gmail: { title: 'Reading email thread', icon: <SiGmail color="#EA4335" />, color: '#EA4335', category: 'email' },
    send_email: { title: 'Sending email', icon: <SiGmail color="#EA4335" />, color: '#EA4335', category: 'email' },
    create_doc: { title: 'Creating document', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'docs' },
    read_doc: { title: 'Reading document', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'docs' },
    update_doc: { title: 'Updating document', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'docs' },
    append_doc: { title: 'Appending to document', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'docs' },
    search_docs: { title: 'Searching documents', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'docs' },
    get_calendar_events: { title: 'Retrieving calendar events', icon: <FaCalendarAlt color="#4285F4" />, color: '#4285F4', category: 'calendar' },
    add_calendar_event: { title: 'Adding calendar event', icon: <FaCalendarAlt color="#4285F4" />, color: '#4285F4', category: 'calendar' },
    update_calendar_event: { title: 'Updating calendar event', icon: <FaCalendarAlt />, color: '#4285F4', category: 'calendar' },
    delete_calendar_event: { title: 'Deleting calendar event', icon: <FaCalendarAlt />, color: '#4285F4', category: 'calendar' },
    list_groupme_groups: { title: 'Listing GroupMe groups', icon: <FaUsers />, color: '#4285F4', category: 'communication' },
    read_groupme_messages: { title: 'Reading GroupMe messages', icon: <FaUsers />, color: '#4285F4', category: 'communication' },
    search_groupme_messages: { title: 'Searching GroupMe messages', icon: <FaSearch />, color: '#4285F4', category: 'communication' },
    read_outlook_emails: { title: 'Reading Outlook email', icon: <FaSearch />, color: '#4285F4', category: 'email' },
    search_outlook_emails: { title: 'Searching Outlook email', icon: <FaSearch />, color: '#4285F4', category: 'email' },
    manage_curiosity: { title: 'Managing discoveries and interests', icon: <FaBrain />, color: '#A855F7', category: 'general' },
    stay_silent: { title: 'Keeping this check silent', icon: <FaBrain />, color: '#A855F7', category: 'general' },
    check_app_exists: { title: 'Checking application availability', icon: <FaTools />, color: '#6366F1', category: 'system' },
    render_chart: { title: 'Rendering chart', icon: <FaChartLine />, color: '#22D3EE', category: 'visual' },
    search_contacts: { title: 'Searching contacts', icon: <FaUsers color="#4285F4" />, color: '#4285F4', category: 'contacts' },
    // Todoist
    get_daily_tasks: { title: 'Retrieving daily tasks', icon: <SiTodoist color="#E44332" />, color: '#E44332', category: 'todoist' },
    add_task: { title: 'Adding task', icon: <SiTodoist color="#E44332" />, color: '#E44332', category: 'todoist' },
    complete_task: { title: 'Completing task', icon: <FaCheckCircle color="#22C55E" />, color: '#22C55E', category: 'todoist' },
    // Communication
    slack_toolkit: { title: 'Slack', icon: <SiSlack color="#4A154B" />, color: '#4A154B', category: 'communication' },
    telegram_toolkit: { title: 'Telegram', icon: <SiTelegram color="#0088CC" />, color: '#0088CC', category: 'communication' },
    discord_toolkit: { title: 'Discord', icon: <FaDiscord color="#5865F2" />, color: '#5865F2', category: 'communication' },
    load_skill: { title: 'Applying skill', icon: <FaBrain color="#A855F7" />, color: '#A855F7', category: 'skills' },
    list_skills: { title: 'Listing skills', icon: <FaBrain color="#A855F7" />, color: '#A855F7', category: 'skills' },
    search_session_history: { title: 'Searching past conversations', icon: <FaBrain color="#A855F7" />, color: '#A855F7', category: 'general' },
    render_diagram: { title: 'Rendering diagram', icon: <FaSitemap color="#22D3EE" />, color: '#22D3EE', category: 'visual' },
    render_animation: { title: 'Rendering animation', icon: <FaBolt color="#22D3EE" />, color: '#22D3EE', category: 'visual' },
    compile_typst: { title: 'Compiling Typst PDF', icon: <FaFileSignature color="#F97316" />, color: '#F97316', category: 'visual' },
    render_marp: { title: 'Rendering slides', icon: <FaListAlt color="#8B5CF6" />, color: '#8B5CF6', category: 'visual' },
    generate_presentation: { title: 'Generating presentation', icon: <FaListAlt color="#EC4899" />, color: '#EC4899', category: 'visual' },
    generate_image: { title: 'Generating image', icon: <FaImages color="#EC4899" />, color: '#EC4899', category: 'visual' },
    kanban_board: { title: 'Updating kanban board', icon: <FaListAlt color="#F59E0B" />, color: '#F59E0B', category: 'system' },
    show_youtube_video: { title: 'Showing YouTube video', icon: <SiYoutube color="#FF0000" />, color: '#FF0000', category: 'display' },
    play_youtube_music: { title: 'Playing YouTube Music', icon: <SiYoutube color="#FF0000" />, color: '#FF0000', category: 'media' },
    show_map: { title: 'Showing map', icon: <FaMapMarkerAlt color="#34D399" />, color: '#34D399', category: 'display' },
    geocode: { title: 'Geocoding place', icon: <FaMapMarkerAlt color="#34D399" />, color: '#34D399', category: 'web' },
    search_places: { title: 'Searching places', icon: <FaSearch color="#34D399" />, color: '#34D399', category: 'web' },
    get_route: { title: 'Getting directions', icon: <FaArrowsAlt color="#34D399" />, color: '#34D399', category: 'web' },
    // Google Drive (native)
    google_drive_search: { title: 'Searching Google Drive', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'drive' },
    google_drive_read_file: { title: 'Reading from Drive', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'drive' },
    google_drive_list_folder: { title: 'Listing Drive folder', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'drive' },
    google_drive_create_doc: { title: 'Creating Google Doc', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'drive' },
    google_drive_edit_doc: { title: 'Editing Google Doc', icon: <SiGoogledocs color="#4285F4" />, color: '#4285F4', category: 'drive' },
    // Sandbox / coding
    run_sandboxed_python: { title: 'Running Python code', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'coding' },
    run_sandboxed_bash: { title: 'Running shell command', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'coding' },
    run_script: { title: 'Running script', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'coding' },
    run_host_command: { title: 'Running command', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'coding' },
    // Chad Code — OpenHands local coding harness (epic #569)
    dispatch_coding_task: { title: 'Delegating coding task', icon: <FaGithub color="#10B981" />, color: '#10B981', category: 'coding' },
    check_coding_task: { title: 'Checking coding job', icon: <FaListAlt color="#10B981" />, color: '#10B981', category: 'coding' },
    stop_coding_task: { title: 'Stopping coding job', icon: <FaTools color="#10B981" />, color: '#10B981', category: 'coding' },
    // Orchestration / system
    switch_to_plan_mode: { title: 'Switching to plan mode', icon: <FaListAlt color="#A855F7" />, color: '#A855F7', category: 'orchestration' },
    propose_plan: { title: 'Proposing plan', icon: <FaListAlt color="#A855F7" />, color: '#A855F7', category: 'orchestration' },
    dispatch_scout: { title: 'Dispatching Scout', icon: <FaListAlt color="#A855F7" />, color: '#A855F7', category: 'delegation' },
    get_datetime: { title: 'Checking date and time', icon: <FaCalendarAlt color="#6366F1" />, color: '#6366F1', category: 'system' },
    ask_user_question: { title: 'Asking clarifying questions', icon: <FaListAlt color="#A855F7" />, color: '#A855F7', category: 'system' },
    click: { title: 'Clicking', icon: <FaMousePointer color="#0EA5E9" />, color: '#0EA5E9', category: 'os' },
    drag: { title: 'Dragging', icon: <FaArrowsAlt color="#0EA5E9" />, color: '#0EA5E9', category: 'os' },
    web_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    searxng_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    playwright_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    serper_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    tavily_search: { title: 'Searching the web', icon: <SiGoogle color="#4285F4" />, color: '#4285F4', category: 'browser' },
    search_images: { title: 'Searching for images', icon: <FaImages color="#4285F4" />, color: '#4285F4', category: 'browser' },
    // Skill sub-agents
    homework_assistant: { title: 'Working on homework', icon: <FaBrain color="#A855F7" />, color: '#A855F7', category: 'skills' },
    organize_folder: { title: 'Organizing folder', icon: <FaSitemap color="#6366F1" />, color: '#6366F1', category: 'filesystem' },
};

// When adding a native tool, always update this file:
//   1. TOOL_METADATA entry (icon, color, category, static fallback title)
//   2. resolveToolUI() branch if the label depends on tool params
// Mirror wording from the tool's format_action() / @tool_display on the backend.
// Present tense only — toPastTenseToolTitle() handles completed state.

// --- ISOLATED FORMATTERS FOR PARAMETER-BASED DYNAMIC TITLES ---

export function formatBrowserLabel(toolName: string, url: string): { title: string; icon?: ReactNode; color?: string } {
    if (!url) return { title: toolName === 'browser_subagent' ? 'Automating Browser' : 'Reading Web Page' };
    const lowerUrl = url.toLowerCase();
    if (lowerUrl.includes('youtube')) {
        return { title: 'Opening YouTube', icon: <SiYoutube color="#FF0000" />, color: '#FF0000' };
    }
    if (lowerUrl.includes('gmail')) {
        return { title: 'Opening Gmail', icon: <SiGmail color="#EA4335" />, color: '#EA4335' };
    }
    if (lowerUrl.includes('discord')) {
        return { title: 'Opening Discord', icon: <FaDiscord color="#5865F2" />, color: '#5865F2' };
    }
    if (lowerUrl.includes('slack')) {
        return { title: 'Opening Slack', icon: <SiSlack color="#4A154B" />, color: '#4A154B' };
    }
    try {
        const hostname = new URL(url.includes('://') ? url : `https://${url}`).hostname;
        const cleanHost = hostname.replace('www.', '');
        return {
            title: toolName === 'browser_subagent' ? `Automating browser on ${cleanHost}` : `Reading ${cleanHost}`
        };
    } catch {
        return {
            title: toolName === 'browser_subagent' ? `Automating: ${url}` : `Reading ${url}`
        };
    }
}

export function formatSearchLabel(query: string): string {
    if (!query) return 'Web Research';
    return `Searching for "${query.length > 30 ? query.substring(0, 27) + '...' : query}"`;
}

export function formatFileLabel(toolName: string, path: string): string {
    const fileName = path ? (path.split(/[\\/]/).pop() || path) : '';
    const fileSuffix = fileName ? ` ${fileName}` : ' file';
    if (toolName === 'multi_replace_file_content') {
        return `Multi-editing${fileSuffix}`;
    }
    const action = (toolName === 'write_file' || toolName === 'write_to_file') 
        ? 'Writing' 
        : toolName === 'create_doc' 
        ? 'Creating' 
        : toolName === 'replace_file_content'
        ? 'Editing'
        : 'Reading';
        
    return `${action}${fileSuffix}`;
}

export function formatDirLabel(path: string): string {
    if (!path) return 'Listing Directory';
    const dirName = path.split(/[\\/]/).pop() || path;
    return `Listing ${dirName}`;
}

export function formatHubspaceLabel(action: string, deviceName: string): string {
    if (action === 'list_devices') return 'Listing smart home devices';
    if (action && deviceName) {
        const friendlyAction = action === 'turn_on' ? 'Turning on' : action === 'turn_off' ? 'Turning off' : 'Setting brightness of';
        return `${friendlyAction} ${deviceName}`;
    }
    return 'Hubspace Control';
}

export function formatSkillLabel(skillName: string): string {
    if (!skillName) return 'Loading Skill';
    const friendlyName = skillName
        .split(/[_-]/)
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
    return `Using ${friendlyName} Skill`;
}

export function formatDiagramLabel(diagramType: string, outputFormat: string, title: string): string {
    const kind = diagramType || 'diagram';
    const fmt = (outputFormat || 'svg').toUpperCase();
    const label = title ? `"${title.length > 28 ? title.slice(0, 25) + '...' : title}"` : kind;
    return `Rendering ${label} as ${fmt}`;
}

export function formatAnimationLabel(mode: string, title: string): string {
    const labels: Record<string, string> = {
        frame: 'Rendering animation frame',
        filmstrip: 'Rendering animation filmstrip',
        diff: 'Diffing rendered animation',
    };
    const base = labels[mode] || 'Rendering animation';
    if (!title) return base;
    const short = title.length > 28 ? title.slice(0, 25) + '...' : title;
    return `${base} "${short}"`;
}

export function formatTypstLabel(title: string): string {
    if (!title) return 'Compiling Typst PDF';
    const short = title.length > 30 ? title.slice(0, 27) + '...' : title;
    return `Compiling Typst PDF "${short}"`;
}

export function formatMarpLabel(exportFormat: string, title: string): string {
    const fmt = (exportFormat || 'pdf').toUpperCase();
    if (!title) return `Rendering Marp slides as ${fmt}`;
    const short = title.length > 28 ? title.slice(0, 25) + '...' : title;
    return `Rendering deck "${short}" as ${fmt}`;
}

export function formatPresentonLabel(exportAs: string, template: string, title: string): string {
    const fmt = (exportAs || 'pptx').toUpperCase();
    const tpl = template || 'general';
    if (!title) return `Generating Presenton deck (${tpl}, ${fmt})`;
    const short = title.length > 28 ? title.slice(0, 25) + '...' : title;
    return `Generating deck "${short}" (${tpl}, ${fmt})`;
}

export function formatCommandLabel(commandLine: string): string {
    if (!commandLine) return 'Running command';
    return `Running ${commandLine.trim()}`;
}

const KANBAN_COLUMN_LABELS: Record<string, string> = {
    todo: 'Todo',
    in_progress: 'In Progress',
    needs_review: 'Needs Review',
    done: 'Done',
};

export function formatKanbanLabel(action: string, column?: string, cardTitle?: string): string {
    const columnLabel = column ? (KANBAN_COLUMN_LABELS[column] ?? column.replace(/_/g, ' ')) : 'board';

    if (action === 'list') {
        return column ? `Listing ${columnLabel} cards` : 'Listing kanban board';
    }
    if (action === 'update_status') {
        if (cardTitle) {
            return `Moving ${cardTitle} to ${columnLabel}`;
        }
        return `Moving card to ${columnLabel}`;
    }
    return 'Updating kanban board';
}

function truncateLabel(text: string, maxLen = 40): string {
    const trimmed = text.trim();
    if (trimmed.length <= maxLen) return trimmed;
    return trimmed.slice(0, maxLen - 3) + '...';
}

export function formatDriveSearchLabel(query?: string, fileType?: string): string {
    const q = truncateLabel(query || '');
    if (!q) return 'Searching Google Drive';
    if (fileType && fileType !== 'all') {
        return `Searching Drive for "${q}" (${fileType})`;
    }
    return `Searching Drive for "${q}"`;
}

export function formatDriveListFolderLabel(folderId?: string): string {
    if (!folderId || folderId === 'root') return 'Listing My Drive';
    return `Listing Drive folder ${truncateLabel(folderId, 24)}`;
}

export function formatDriveReadLabel(fileName?: string): string {
    if (fileName) return `Reading "${truncateLabel(fileName)}" from Drive`;
    return 'Reading file from Drive';
}

export function formatDriveCreateDocLabel(title?: string): string {
    const t = truncateLabel(title || '');
    return t ? `Creating Google Doc "${t}"` : 'Creating Google Doc';
}

export function formatDriveEditDocLabel(
    docName?: string,
    mode?: 'append' | 'find_replace' | 'replace_all' | 'edit',
): string {
    const target = docName ? `"${truncateLabel(docName)}"` : 'Google Doc';
    if (mode === 'append') return `Appending to ${target}`;
    if (mode === 'find_replace') return `Editing ${target}`;
    if (mode === 'replace_all') return `Replacing content in ${target}`;
    return `Editing ${target}`;
}

export function formatSandboxPythonLabel(code?: string): string {
    if (!code?.trim()) return 'Running Python code';
    const firstLine = code.split('\n').map((ln) => ln.trim()).find(Boolean) || '';
    const snippet = truncateLabel(firstLine, 36);
    return snippet ? `Running Python: ${snippet}` : 'Running Python code';
}

export function formatSandboxBashLabel(command?: string): string {
    const cmd = truncateLabel(command || '', 48);
    return cmd ? `Running ${cmd}` : 'Running shell command';
}

export function formatRunScriptLabel(scriptName?: string): string {
    const name = truncateLabel(scriptName || '');
    return name ? `Running script ${name}` : 'Running script';
}

export function formatSwitchToPlanModeLabel(reasoning?: string): string {
    const r = truncateLabel(reasoning || '', 50);
    return r ? `Switching to plan mode: ${r}` : 'Switching to plan mode';
}

export function formatGetDatetimeLabel(format?: string): string {
    const fmt = (format || 'datetime').toLowerCase();
    if (fmt === 'time') return 'Checking current time';
    if (fmt === 'date') return "Checking today's date";
    if (fmt === 'timestamp') return 'Getting timestamp';
    return 'Checking date and time';
}

export function formatClickLabel(clickType?: string, x?: unknown, y?: unknown): string {
    const where = x !== undefined && x !== null && y !== undefined && y !== null ? ` (${x}, ${y})` : '';
    switch ((clickType || 'left').toLowerCase()) {
        case 'right': return `Right-clicking${where}`;
        case 'double': return `Double-clicking${where}`;
        case 'move': return `Moving cursor to${where || ' target'}`;
        default: return `Clicking${where}`;
    }
}

export function formatDragLabel(
    fromX?: unknown, fromY?: unknown, toX?: unknown, toY?: unknown,
): string {
    if ([fromX, fromY, toX, toY].every((v) => v !== undefined && v !== null)) {
        return `Dragging (${fromX}, ${fromY}) → (${toX}, ${toY})`;
    }
    return 'Dragging';
}

export function formatMCPToolLabel(serverName: string, toolName: string, args: Record<string, unknown>): string {
    if (!serverName || !toolName) return 'Executing MCP Tool';
    
    let serverDisplay = serverName.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    if (serverDisplay.toLowerCase() === 'github') serverDisplay = 'GitHub';
    if (serverDisplay.toLowerCase() === 'google drive') serverDisplay = 'Google Drive';
    
    const toolDisplay = toolName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    
    const argItems: string[] = [];
    for (const [k, v] of Object.entries(args)) {
        if (k.startsWith('_') || v === null || v === undefined || v === '') continue;
        let valStr = typeof v === 'object' ? JSON.stringify(v) : String(v);
        if (valStr.length > 40) {
            valStr = valStr.substring(0, 37) + '...';
        }
        let keyDisplay = k;
        if (k === 'query') keyDisplay = '🔍';
        else if (k === 'path') keyDisplay = '📁';
        else if (['content', 'body', 'text'].includes(k)) keyDisplay = '📝';
        else if (k === 'url') keyDisplay = '🌐';
        
        argItems.push(`${keyDisplay}: ${valStr}`);
    }
    
    const argStr = argItems.length > 0 ? ` [${argItems.join(', ')}]` : '';
    return `${serverDisplay} ⚡ ${toolDisplay}${argStr}`;
}

const TOOL_TITLE_PHRASE_PAST: [string, string][] = [
    ['Multi-editing', 'Multi-edited'],
];

const TOOL_TITLE_VERB_PAST: Record<string, string> = {
    Adding: 'Added',
    Applying: 'Applied',
    Appending: 'Appended',
    Automating: 'Automated',
    Checking: 'Checked',
    Completing: 'Completed',
    Controlling: 'Controlled',
    Creating: 'Created',
    Editing: 'Edited',
    Enlisting: 'Enlisted',
    Executing: 'Executed',
    Getting: 'Got',
    Listing: 'Listed',
    Loading: 'Loaded',
    Locating: 'Located',
    Managing: 'Managed',
    Moving: 'Moved',
    Opening: 'Opened',
    Planning: 'Planned',
    Reading: 'Read',
    Replacing: 'Replaced',
    Retrieving: 'Retrieved',
    Scanning: 'Scanned',
    Searching: 'Searched',
    Sending: 'Sent',
    Setting: 'Set',
    Sifting: 'Sifted',
    Turning: 'Turned',
    Updating: 'Updated',
    Using: 'Used',
    Writing: 'Wrote',
};

/** Converts an active/pending tool title to past tense for success state. */
export function toPastTenseToolTitle(title: string): string {
    const cleaned = title.replace(/\.\.\.$/, '').trimEnd();

    if (cleaned.startsWith('Running ')) {
        return `Ran ${cleaned.slice('Running '.length)}`;
    }

    for (const [phrase, past] of TOOL_TITLE_PHRASE_PAST) {
        if (cleaned.startsWith(phrase)) {
            return past + cleaned.slice(phrase.length);
        }
    }

    const spaceIdx = cleaned.indexOf(' ');
    const firstWord = spaceIdx === -1 ? cleaned : cleaned.slice(0, spaceIdx);
    const rest = spaceIdx === -1 ? '' : cleaned.slice(spaceIdx);
    const pastVerb = TOOL_TITLE_VERB_PAST[firstWord];
    if (pastVerb) {
        return pastVerb + rest;
    }

    return cleaned;
}


/**
 * Resolves a raw ThinkingEvent into a friendly UI display object.
 */
export function resolveToolUI(event: ThinkingEvent): ToolUIDisplay {
    const rawName: unknown = event.name;
    const nameObject = rawName as { name?: unknown; tool_name?: unknown } | null;
    const candidateName = typeof rawName === 'string' ? rawName : nameObject?.name ?? nameObject?.tool_name;
    const toolName = typeof candidateName === 'string' && candidateName ? candidateName : 'unknown';
    const params = event.params || {};
    
    // Default fallback
    let display: ToolUIDisplay = {
        title: event.display_text || toolName.replace(/_/g, ' '),
        icon: <FaBolt />,
        color: '#6366F1',
        category: 'general'
    };

    // 1. Check direct metadata mapping
    if (TOOL_METADATA[toolName]) {
        display = { ...TOOL_METADATA[toolName] };
    }

    // 2. Dynamic Delegation Handling
    if (toolName === 'scout_gather' || toolName === 'dispatch_scout') {
        display = {
            title: event.display_text || 'Gathering evidence for plan…',
            icon: <FaBrain />,
            color: '#A855F7',
            category: 'delegation',
        };
    } else if (toolName.startsWith('delegate_to_')) {
        const agentName = toolName.replace('delegate_to_', '').replace(/_/g, ' ');
        display = {
            title: `Enlisting ${agentName} specialist...`,
            icon: <FaBrain />,
            color: '#A855F7', // Purple for intelligence/delegation
            category: 'delegation'
        };
    }

    // 3. Parameter-based dynamic titles
    if (toolName === 'open_browser' || toolName === 'open_url' || toolName === 'read_url_content' || toolName === 'browser_subagent') {
        const url = (params.url || params.Url || params.query || params.TaskName || '') as string;
        const res = formatBrowserLabel(toolName, url);
        display.title = res.title;
        if (res.icon) display.icon = res.icon;
        if (res.color) display.color = res.color;
    } else if (toolName === 'google_search' || toolName === 'search_web_brave' || toolName === 'search_web' || toolName === 'web_search') {
        const query = (params.query || params.Query || '') as string;
        display.title = formatSearchLabel(query);
    } else if (toolName === 'google_drive_search') {
        display.title = formatDriveSearchLabel(
            (params.query || '') as string,
            (params.file_type || '') as string,
        );
    } else if (toolName === 'google_drive_list_folder') {
        display.title = formatDriveListFolderLabel((params.folder_id || '') as string);
    } else if (toolName === 'google_drive_read_file') {
        display.title = formatDriveReadLabel(
            (params.name || params.file_name || params.title || '') as string,
        );
    } else if (toolName === 'google_drive_create_doc') {
        display.title = formatDriveCreateDocLabel((params.title || '') as string);
    } else if (toolName === 'google_drive_edit_doc') {
        const mode = params.append_text
            ? 'append'
            : params.find_text
            ? 'find_replace'
            : params.replace_all_content
            ? 'replace_all'
            : 'edit';
        display.title = formatDriveEditDocLabel(
            (params.title || params.doc_name || '') as string,
            mode as 'append' | 'find_replace' | 'replace_all' | 'edit',
        );
    } else if (toolName === 'run_sandboxed_python') {
        display.title = formatSandboxPythonLabel((params.code || '') as string);
    } else if (toolName === 'run_sandboxed_bash') {
        display.title = formatSandboxBashLabel((params.command || '') as string);
    } else if (toolName === 'run_script') {
        display.title = formatRunScriptLabel((params.script_name || '') as string);
    } else if (toolName === 'run_host_command') {
        display.title = formatCommandLabel((params.command || '') as string);
    } else if (toolName === 'switch_to_plan_mode') {
        display.title = formatSwitchToPlanModeLabel((params.reasoning || '') as string);
    } else if (toolName === 'propose_plan') {
        const steps = Array.isArray(params.steps) ? params.steps.length : 0;
        display.title = steps ? `Proposing ${steps}-step plan` : 'Proposing plan';
    } else if (toolName === 'dispatch_scout') {
        const brief = String(params.brief || '');
        display.title = brief
            ? `Dispatching Scout: ${brief.length > 60 ? `${brief.slice(0, 57)}...` : brief}`
            : 'Dispatching Scout';
    } else if (toolName === 'get_datetime') {
        display.title = formatGetDatetimeLabel((params.format || '') as string);
    } else if (toolName === 'show_youtube_video') {
        const title = (params.title || '') as string;
        display.title = title ? `Showing: ${title}` : 'Showing YouTube video';
        display.icon = <SiYoutube color="#FF0000" />;
        display.color = '#FF0000';
        display.category = 'display';
    } else if (toolName === 'play_youtube_music') {
        const mode = String(params.mode || 'play').toLowerCase();
        const query = String(params.query || '');
        const short = query.length > 40 ? `${query.slice(0, 37)}...` : query;
        if (params.video_id || params.playlist_id) {
            display.title = 'Opening YouTube Music';
        } else if (mode === 'search') {
            display.title = short ? `Searching YouTube Music: ${short}` : 'Searching YouTube Music';
        } else {
            display.title = short ? `Playing YouTube Music: ${short}` : 'Playing YouTube Music';
        }
        display.icon = <SiYoutube color="#FF0000" />;
        display.color = '#FF0000';
        display.category = 'media';
    } else if (toolName === 'show_map') {
        const title = (params.title || '') as string;
        display.title = title ? `Showing map: ${title}` : 'Showing map';
        display.icon = <FaMapMarkerAlt color="#34D399" />;
        display.color = '#34D399';
        display.category = 'display';
    } else if (toolName === 'geocode') {
        const q = (params.query || '') as string;
        display.title = q ? `Geocoding: ${q}` : 'Geocoding place';
    } else if (toolName === 'search_places') {
        const amenity = (params.amenity || params.query || '') as string;
        display.title = amenity ? `Searching places: ${amenity}` : 'Searching places';
    } else if (toolName === 'get_route') {
        display.title = 'Getting directions';
    } else if (
        toolName === 'write_file' || toolName === 'read_file' || toolName === 'create_doc' ||
        toolName === 'view_file' || toolName === 'write_to_file' || 
        toolName === 'replace_file_content' || toolName === 'multi_replace_file_content'
    ) {
        const path = (params.path || params.TargetFile || params.AbsolutePath || params.title || '') as string;
        display.title = formatFileLabel(toolName, path);
    } else if (toolName === 'list_dir' || toolName === 'list_directory') {
        const path = (params.DirectoryPath || params.path || '') as string;
        display.title = formatDirLabel(path);
    } else if (toolName === 'hubspace_control') {
        const action = (params.action || '') as string;
        const devName = (params.device_name || '') as string;
        display.title = formatHubspaceLabel(action, devName);
    } else if (toolName === 'load_skill') {
        const skillName = (params.skill_name || '') as string;
        display.title = formatSkillLabel(skillName);
    } else if (toolName === 'render_diagram') {
        display.title = formatDiagramLabel(
            (params.diagram_type || '') as string,
            (params.output_format || '') as string,
            (params.title || '') as string,
        );
    } else if (toolName === 'render_animation') {
        display.title = formatAnimationLabel(
            (params.mode || '') as string,
            (params.title || '') as string,
        );
    } else if (toolName === 'compile_typst') {
        display.title = formatTypstLabel((params.title || '') as string);
    } else if (toolName === 'render_marp') {
        display.title = formatMarpLabel(
            (params.export_format || '') as string,
            (params.title || '') as string,
        );
    } else if (toolName === 'generate_presentation') {
        display.title = formatPresentonLabel(
            (params.export_as || '') as string,
            (params.template || '') as string,
            (params.title || params.content || '') as string,
        );
    } else if (toolName === 'generate_image') {
        const prompt = (params.prompt || params.title || '') as string;
        const ar = (params.aspect_ratio || 'landscape') as string;
        display.title = prompt ? `Generating image (${ar}): "${prompt.slice(0, 40)}${prompt.length > 40 ? '…' : ''}"` : 'Generating image';
    } else if (toolName === 'run_command') {
        const cmd = (params.CommandLine || params.command || params.cmd || '') as string;
        display.title = formatCommandLabel(cmd);
    } else if (toolName === 'click') {
        display.title = formatClickLabel((params.click_type || '') as string, params.x, params.y);
    } else if (toolName === 'drag') {
        display.title = formatDragLabel(params.from_x, params.from_y, params.to_x, params.to_y);
    } else if (toolName === 'kanban_board') {
        const action = (params.action || '') as string;
        const column = (params.column || '') as string;
        const cardTitle = (params.card_title || params.title || '') as string;
        display.title = formatKanbanLabel(action, column, cardTitle || undefined);
    } else if (toolName === 'call_mcp_tool') {
        const server = (params.server_name || params.ServerName || '') as string;
        const tool = (params.tool_name || params.ToolName || '') as string;
        const args = (params.arguments || params.Arguments || {}) as Record<string, unknown>;
        display.title = formatMCPToolLabel(server, tool, args);
        
        const lowerServer = server.toLowerCase();
        if (lowerServer.includes('github')) {
            display.icon = <FaGithub />;
            display.color = '#24292F';
        } else if (lowerServer.includes('supabase')) {
            display.icon = <FaDatabase />;
            display.color = '#3ECF8E';
        } else if (lowerServer.includes('brave') || lowerServer.includes('search')) {
            display.icon = <SiBrave color="#FB542B" />;
            display.color = '#FB542B';
            display.category = 'browser';
        }
    }


    // 4. Manual overrides from event display_text (Source of Truth if provided)
    if (event.display_text && !event.display_text.toLowerCase().startsWith('executing ')) {
        display.title = event.display_text;
    }

    return display;
}
