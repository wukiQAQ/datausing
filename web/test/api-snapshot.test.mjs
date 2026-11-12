import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.js';

const originalFetch = globalThis.fetch;
const snapshotHeaders = {
  "X-FlowMask-Result-Id": "snapshot",
  "X-FlowMask-Policy-Version": "v1",
};
test.afterEach(() => {
  globalThis.fetch = originalFetch;
  delete globalThis.window;
  delete globalThis.document;
});

test('strategy preview sends snapshot identity and policy without original input', async () => {
  let sent;
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.fetch = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return new Response(JSON.stringify({ result_id: 'snapshot' }), { status: 200 });
  };
  await api.previewSnapshot({ result_id: 'snapshot', policy_version: 'v1', text: '13800138000' }, 'encrypt');
  assert.equal(sent.url, '/api/v1/detect/results/snapshot/preview');
  assert.deepEqual(sent.body, { strategy: 'encrypt', policy_version: 'v1' });
});

test('snapshot export does not resubmit input or detection options', async () => {
  let sent;
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.document = {
    createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} },
  };
  globalThis.fetch = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return new Response('masked', { status: 200, headers: snapshotHeaders });
  };
  await api.exportSnapshot({ result_id: 'snapshot', policy_version: 'v1', text: '13800138000', enable_l3: true }, 'block', 'txt');
  assert.equal(sent.url, '/api/v1/detect/export');
  assert.deepEqual(sent.body, { result_id: 'snapshot', policy_version: 'v1', strategy: 'block', fmt: 'txt' });
});

test('a snapshot invalidated while downloading never creates a download link', async () => {
  globalThis.window = { localStorage: { getItem: () => null } };
  let current = true;
  let clicked = false;
  globalThis.document = {
    createElement: () => ({ click() { clicked = true; }, remove() {} }),
    body: { appendChild() {} },
  };
  globalThis.fetch = async () => {
    current = false;
    return new Response('masked', { status: 200, headers: snapshotHeaders });
  };
  await assert.rejects(
    api.exportSnapshot({ result_id: 'snapshot', policy_version: 'v1' }, 'block', 'txt', 'masked.txt', () => current),
    /409/,
  );
  assert.equal(clicked, false);
});

test('an export response from a different snapshot is not downloaded', async () => {
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.fetch = async () => new Response('masked', {
    headers: { ...snapshotHeaders, "X-FlowMask-Result-Id": "different" },
  });
  await assert.rejects(
    api.exportSnapshot({ result_id: 'snapshot', policy_version: 'v1' }, 'block', 'txt'),
    /409/,
  );
});

test('uploads distinguish explicit model switches from omitted defaults', async () => {
  let sent;
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.fetch = async (url) => {
    sent = new URL(url, 'http://localhost').searchParams;
    return new Response('{}', { status: 200 });
  };
  const file = new File(['sample'], 'sample.txt');
  for (const upload of [api.detectFile, api.importTraffic, (input, opts) => api.batchTasks([input], opts)]) {
    await upload(file, { enableL2: false, enableL3: true });
    assert.equal(sent.get('enable_l2'), 'false');
    assert.equal(sent.get('enable_l3'), 'true');
    await upload(file);
    assert.equal(sent.has('enable_l2'), false);
    assert.equal(sent.has('enable_l3'), false);
  }
});
