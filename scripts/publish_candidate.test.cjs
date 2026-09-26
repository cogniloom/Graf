const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const publish = require('./publish_candidate.cjs');

function fixture(t, options = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'candidate-test-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const tag = 'v0.1.0-rc.42';
  const name = 'graf-0.1.0-rc.42.tar.gz';
  const data = Buffer.from('synthetic archive');
  fs.writeFileSync(path.join(dir, name), data);
  fs.writeFileSync(path.join(dir, `${name}.sha256`), `${crypto.createHash('sha256').update(data).digest('hex')}  ${name}\n`);
  const calls = [];
  const draft = {id: 7, tag_name: tag, draft: true, target_commitish: 'abc', upload_url: 'https://example.invalid/upload'};
  const repos = {};
  for (const method of ['createRelease', 'uploadReleaseAsset', 'deleteReleaseAsset', 'updateRelease']) {
    repos[method] = async args => {
      calls.push([method, args]);
      if (options.failUpload && method === 'uploadReleaseAsset') throw new Error('upload failed');
      return {data: draft};
    };
  }
  repos.listReleases = 'releases';
  repos.listReleaseAssets = 'assets';
  const github = {
    rest: {repos, git: {
      getRef: async () => {
        if (options.ref) return {data: {object: {type: 'commit', sha: options.ref}}};
        throw Object.assign(new Error('missing'), {status: options.refError || 404});
      },
      createRef: async args => { calls.push(['createRef', args]); },
    }},
    paginate: async method => method === 'releases' ? (options.releases || []) : (options.assets || []),
  };
  const context = {eventName: 'push', ref: 'refs/heads/main', sha: 'abc', repo: {owner: 'owner', repo: 'repo'}};
  return {args: {github, context, core: {info() {}}}, env: {RELEASE_TAG: tag, RELEASE_DIRECTORY: dir}, calls, draft};
}

test('uploads to a draft then tags exact tested commit and publishes prerelease', async t => {
  const f = fixture(t);
  await publish(f.args, f.env);
  assert.deepEqual(f.calls.map(c => c[0]), ['createRelease', 'uploadReleaseAsset', 'uploadReleaseAsset', 'createRef', 'updateRelease']);
  assert.equal(f.calls[0][1].draft, true);
  assert.equal(f.calls[0][1].target_commitish, 'abc');
  assert.equal(f.calls[3][1].sha, 'abc');
  assert.equal(f.calls[4][1].prerelease, true);
  assert.equal(f.calls[4][1].make_latest, 'false');
});
for (const prerelease of [true, false]) {
  test(`rerun preserves published release (prerelease=${prerelease})`, async t => {
    const f = fixture(t, {ref: 'abc', releases: [{tag_name: 'v0.1.0-rc.42', draft: false, prerelease}]});
    await publish(f.args, f.env);
    assert.deepEqual(f.calls, []);
  });
}
test('upload failure never tags or publishes', async t => {
  const f = fixture(t, {failUpload: true});
  await assert.rejects(publish(f.args, f.env), /upload failed/);
  assert.deepEqual(f.calls.map(c => c[0]), ['createRelease', 'uploadReleaseAsset']);
});
test('rerun resumes draft and replaces partial asset', async t => {
  const f = fixture(t, {assets: [{id: 9, name: 'graf-0.1.0-rc.42.tar.gz'}]});
  f.args.github.paginate = async method => method === 'releases' ? [f.draft] : [{id: 9, name: 'graf-0.1.0-rc.42.tar.gz'}];
  await publish(f.args, f.env);
  assert.equal(f.calls[0][0], 'deleteReleaseAsset');
  assert.equal(f.calls.at(-1)[0], 'updateRelease');
});
test('conflicting tag fails before mutations', async t => {
  const f = fixture(t, {ref: 'wrong'});
  await assert.rejects(publish(f.args, f.env), /different commit/);
  assert.deepEqual(f.calls, []);
});
test('API access error fails closed', async t => {
  const f = fixture(t, {refError: 403});
  await assert.rejects(publish(f.args, f.env), {status: 403});
  assert.deepEqual(f.calls, []);
});
test('bad checksum fails before mutations', async t => {
  const f = fixture(t);
  fs.appendFileSync(path.join(f.env.RELEASE_DIRECTORY, 'graf-0.1.0-rc.42.tar.gz'), 'corrupt');
  await assert.rejects(publish(f.args, f.env), /checksum mismatch/);
  assert.deepEqual(f.calls, []);
});
test('manual runs cannot publish', async t => {
  const f = fixture(t);
  f.args.context.eventName = 'workflow_dispatch';
  await assert.rejects(publish(f.args, f.env), /push to main/);
  assert.deepEqual(f.calls, []);
});
