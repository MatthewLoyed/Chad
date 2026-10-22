import type { Preview } from '@storybook/react';

// Global styles — CSS variables, component styles, fonts
import '../src/index.css';

const preview: Preview = {
    parameters: {
        // Dark background matching Chad's #0a0b10
        backgrounds: {
            options: {
                "chad-dark": { name: 'chad-dark', value: '#0a0b10' },
                "chad-panel": { name: 'chad-panel', value: '#0f1219' },
                white: { name: 'white', value: '#ffffff' }
            }
        },
        // Disable animations in visual regression snapshots
        motion: { disabled: true },
        controls: {
            matchers: {
                color: /(background|color)$/i,
                date: /Date$/i,
            },
        },
        // Layout defaults for isolated component stories
        layout: 'centered',
    },

    // Global story-level decorators can be added here
    decorators: [],

    initialGlobals: {
        backgrounds: {
            value: 'chad-dark'
        }
    }
};

export default preview;
