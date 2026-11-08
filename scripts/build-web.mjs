import { cp, mkdir, rm, writeFile } from 'node:fs/promises';

const apiUrl = process.env.PUBLIC_API_URL;
if (!apiUrl || !/^https:\/\/[^\s]+$/.test(apiUrl)) {
  throw new Error(
    'Set PUBLIC_API_URL to your HTTPS Render backend URL before building the Vercel frontend.',
  );
}
await rm('dist', { recursive: true, force: true });
await mkdir('dist/assets', { recursive: true });
await cp('web', 'dist/assets', { recursive: true });
await cp('web/index.html', 'dist/index.html');
await writeFile(
  'dist/config.js',
  `export const API_URL = ${JSON.stringify(apiUrl.replace(/\/$/, ''))};\n`,
);
