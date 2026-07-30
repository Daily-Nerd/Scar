// Copies repo-root CHANGELOG.md into docs/ at build time so the site never
// drifts from the release-please changelog. The output is gitignored; the
// deploy workflow's paths filter includes CHANGELOG.md so releases redeploy.
import {readFileSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const changelog = readFileSync(join(here, '..', '..', 'CHANGELOG.md'), 'utf8');

const header = [
  '---',
  'sidebar_position: 6',
  'title: Changelog',
  'description: Release history, synced verbatim from CHANGELOG.md at build time.',
  'slug: /changelog',
  '---',
  '',
  '',
].join('\n');

// The changelog starts with "# Changelog"; drop it so the frontmatter title
// is the only h1 on the page.
const body = changelog.replace(/^# Changelog\s*\n/, '');

writeFileSync(join(here, '..', 'docs', 'changelog.md'), header + body);
console.log('synced CHANGELOG.md -> website/docs/changelog.md');
