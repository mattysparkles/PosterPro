import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const dashboardSource = await readFile(new URL('../pages/app.js', import.meta.url), 'utf8');
const activeDashboard = dashboardSource.slice(
  dashboardSource.indexOf('const renderDashboardSummary = () => {'),
  dashboardSource.indexOf('const sectionContent = { overview: renderDashboardSummary() }'),
);

test('the active Dashboard visibly mounts the operator command input', () => {
  assert.match(activeDashboard, /aria-label="Dashboard AI operator"/);
  assert.match(activeDashboard, /aria-label="Ask PosterPro command"/);
  assert.match(activeDashboard, /Preview command/);
  assert.match(activeDashboard, /runOperatorCommand\(\{ applyLive: true \}\)/);
});

test('the Dashboard supports speech transcription and a local audio recording', () => {
  assert.match(dashboardSource, /window\.SpeechRecognition \|\| window\.webkitSpeechRecognition/);
  assert.match(dashboardSource, /new MediaRecorder\(stream\)/);
  assert.match(activeDashboard, /Record audio/);
  assert.match(activeDashboard, /<audio controls src=\{recordedAudioUrl\}/);
  assert.match(dashboardSource, /Audio is not uploaded/);
});
