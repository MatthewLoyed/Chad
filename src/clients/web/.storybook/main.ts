import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
    stories: ['../src/stories/**/*.stories.@(ts|tsx)'],
    addons: ['@storybook/addon-themes', '@storybook/addon-docs'],

    framework: {
        name: '@storybook/react-vite',
        options: {},
    },

    viteFinal: async (config) => {
        // Remove base: './' — it breaks Storybook's asset loading
        config.base = '/';
        return config;
    }
};

export default config;
