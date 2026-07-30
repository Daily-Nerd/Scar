import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'SCAR',
  tagline: 'Version control for negative knowledge',
  favicon: 'img/favicon.svg',

  future: {
    v4: true,
  },

  url: 'https://daily-nerd.github.io',
  // Pages URL is case-sensitive; must match the repo name exactly.
  baseUrl: '/Scar/',
  organizationName: 'Daily-Nerd',
  projectName: 'Scar',

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  // Required even with a single locale; also sets <html lang>.
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/Daily-Nerd/Scar/tree/main/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'SCAR',
      items: [
        {type: 'docSidebar', sidebarId: 'docsSidebar', position: 'left', label: 'Docs'},
        {to: '/changelog', label: 'Changelog', position: 'left'},
        {href: 'https://pypi.org/project/scar-cli/', label: 'PyPI', position: 'right'},
        {href: 'https://github.com/Daily-Nerd/Scar', label: 'GitHub', position: 'right'},
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {label: 'Quickstart', to: '/quickstart'},
        {label: 'Methodology', to: '/methodology'},
        {label: 'llms.txt', href: 'https://daily-nerd.github.io/Scar/llms.txt'},
        {label: 'GitHub', href: 'https://github.com/Daily-Nerd/Scar'},
        {label: 'PyPI', href: 'https://pypi.org/project/scar-cli/'},
      ],
      copyright: 'Apache-2.0 licensed. Built with Docusaurus.',
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'diff'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
