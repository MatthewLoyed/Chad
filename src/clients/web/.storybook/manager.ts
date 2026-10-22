import { addons } from 'storybook/manager-api';
import { themes } from 'storybook/theming';

addons.setConfig({
    theme: {
        ...themes.dark,
        brandTitle: 'Chad UI',
        brandTarget: '_self',
        colorPrimary: '#8b5cf6',
        colorSecondary: '#6366f1',
        appBg: '#0a0b10',
        appContentBg: '#0f1219',
        appBorderColor: 'rgba(255,255,255,0.08)',
        appBorderRadius: 8,
        textColor: '#ffffff',
        textInverseColor: '#0a0b10',
        barBg: '#0f1219',
        barTextColor: 'rgba(255,255,255,0.6)',
        barSelectedColor: '#8b5cf6',
        inputBg: '#161b24',
        inputBorder: 'rgba(255,255,255,0.1)',
        inputTextColor: '#ffffff',
        inputBorderRadius: 6,
    },
});
