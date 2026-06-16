// @ts-check
const { themes: prismThemes } = require('prism-react-renderer');

const config = {
  title: 'NeedleDrop',
  tagline: 'An MCP server for intelligent Apple Music library management',
  url: 'https://amattas.github.io',
  baseUrl: '/needle-drop/',
  organizationName: 'amattas',
  projectName: 'needle-drop',
  onBrokenLinks: 'throw',
  favicon: 'img/favicon.svg',
  themeConfig: {
    // No defaultMode: follow the visitor's OS/browser preference. (A manual
    // toggle persists in localStorage and overrides this — by design.)
    colorMode: { respectPrefersColorScheme: true },
    // Docusaurus's default prism theme (palenight) is a dark-background token
    // palette in BOTH modes — unreadable on light panels. Per-mode themes:
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.nightOwl,
      additionalLanguages: ['bash', 'python', 'json', 'toml'],
    },
    navbar: {
      title: 'NeedleDrop',
      logo: { src: 'img/logo.svg', width: 26, height: 26 },
      items: [
        { to: '/guide/getting-started', label: 'Guide', position: 'left' },
        { to: '/reference/cli', label: 'Reference', position: 'left' },
        { to: '/architecture', label: 'Architecture', position: 'left' },
        {
          href: 'https://github.com/amattas/needle-drop',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
  },
  presets: [
    [
      '@docusaurus/preset-classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.js',
          // No cut versions yet — `current` (main) serves at the site root. To
          // start versioning later (like the sibling pydmp site), add a
          //   versions: { current: { label: 'dev (main)', path: 'dev' } }
          // block here and run: npm run docusaurus docs:version X.Y.Z
        },
        blog: false,
        theme: { customCss: './src/css/custom.css' },
        sitemap: { lastmod: 'date', changefreq: 'weekly', priority: 0.5, filename: 'sitemap.xml' },
      },
    ],
  ],
  themes: [
    [
      // Self-hosted full-text search — no external service, indexed at build time.
      require.resolve('@easyops-cn/docusaurus-search-local'),
      {
        hashed: true,
        docsRouteBasePath: '/',
        indexBlog: false,
        highlightSearchTermsOnTargetPage: true,
      },
    ],
  ],
};
module.exports = config;
