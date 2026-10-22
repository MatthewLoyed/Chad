import type { Meta, StoryObj } from '@storybook/react-vite';
import ArtifactCard from '../components/data-display/ArtifactCard';
import type { ThinkingEvent } from '../store';

const meta = {
    title: 'Data Display/ArtifactCard',
    component: ArtifactCard,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Download card rendered when a tool call produces a file artifact ' +
                    '(via download_token). Shown inline below AI messages and in the active turn.',
            },
        },
    },
} satisfies Meta<typeof ArtifactCard>;

export default meta;
type Story = StoryObj<typeof meta>;

const makeArtifact = (overrides: Partial<ThinkingEvent>): ThinkingEvent => ({
    type: 'tool_end',
    name: 'write_file',
    success: true,
    correlation_id: 'artifact-story-1',
    download_token: 'tok_abc123',
    filename: 'output.txt',
    timestamp: Date.now(),
    ...overrides,
});

export const TextFile: Story = {
    name: 'Text File',
    args: {
        artifact: makeArtifact({ filename: 'report.txt', name: 'write_file' }),
    },
};

export const PythonScript: Story = {
    name: 'Python Script',
    args: {
        artifact: makeArtifact({ filename: 'analyse_data.py', name: 'run_sandboxed_python' }),
    },
};

export const SpreadsheetExport: Story = {
    name: 'Spreadsheet / XLSX',
    args: {
        artifact: makeArtifact({ filename: 'results.xlsx', name: 'write_file' }),
    },
};

export const PDFDocument: Story = {
    name: 'PDF Document',
    args: {
        artifact: makeArtifact({ filename: 'summary_report.pdf', name: 'write_file' }),
    },
};

export const LongFilename: Story = {
    name: 'Long Filename',
    args: {
        artifact: makeArtifact({
            filename: 'very_long_descriptive_filename_that_should_truncate_gracefully_in_the_card_ui.json',
            name: 'write_file',
        }),
    },
};
