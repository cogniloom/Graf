// Called by actions/github-script after the build and tests succeed.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

module.exports = async function publish({github, context, core}, env = process.env) {
  if (context.eventName !== 'push' || context.ref !== 'refs/heads/main') {
    throw new Error('Candidates may only be published by a push to main');
  }
  const tag = env.RELEASE_TAG;
  if (!/^v\d+\.\d+\.\d+-rc\.[1-9]\d*$/.test(tag || '')) {
    throw new Error('Invalid candidate tag');
  }
  const repo = context.repo;
  const api = github.rest.repos;
  let ref;
  try {
    ref = (await github.rest.git.getRef({...repo, ref: `tags/${tag}`})).data;
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  if (ref && (ref.object.type !== 'commit' || ref.object.sha !== context.sha)) {
    throw new Error('Candidate tag already points to a different commit');
  }
  // Include drafts so a failed upload can be resumed on a rerun.
  const releases = await github.paginate(api.listReleases, {...repo, per_page: 100});
  let release = releases.find(item => item.tag_name === tag);
  if (release && !release.draft) {
    if (!ref) throw new Error('Published candidate is missing its tag');
    core.info(`Keeping published release ${tag} unchanged (including manual promotion).`);
    return;
  }
  if (release && release.target_commitish !== context.sha) {
    throw new Error('Candidate draft targets a different commit');
  }
  const archiveName = `docworm-${tag.slice(1)}.tar.gz`;
  const checksumName = `${archiveName}.sha256`;
  const archive = fs.readFileSync(path.join(env.RELEASE_DIRECTORY, archiveName));
  const checksum = fs.readFileSync(path.join(env.RELEASE_DIRECTORY, checksumName));
  const expected = `${crypto.createHash('sha256').update(archive).digest('hex')}  ${archiveName}\n`;
  if (checksum.toString() !== expected) throw new Error('Release archive checksum mismatch');
  if (!release) {
    release = (await api.createRelease({
      ...repo, tag_name: tag, target_commitish: context.sha,
      name: tag, draft: true, prerelease: true,
      generate_release_notes: true, make_latest: 'false',
    })).data;
  }
  // Drafts stay hidden until both assets have uploaded successfully.
  const assets = await github.paginate(api.listReleaseAssets, {...repo, release_id: release.id, per_page: 100});
  for (const [name, data] of [[archiveName, archive], [checksumName, checksum]]) {
    const existing = assets.find(item => item.name === name);
    if (existing) await api.deleteReleaseAsset({...repo, asset_id: existing.id});
    await api.uploadReleaseAsset({
      ...repo, release_id: release.id, url: release.upload_url, name, data,
      headers: {'content-type': 'application/octet-stream', 'content-length': data.length},
    });
  }
  if (!ref) {
    await github.rest.git.createRef({...repo, ref: `refs/tags/${tag}`, sha: context.sha});
  }
  await api.updateRelease({
    ...repo, release_id: release.id, draft: false, prerelease: true, make_latest: 'false',
  });
  core.info(`Published candidate ${tag} at ${context.sha}`);
};
