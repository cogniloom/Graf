// Release series selection shared by the build and the publication guard.
const pattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
function parts(version) {
  if (!pattern.test(version || '')) throw new Error(`Invalid release version: ${version}; expected X.Y.Z`);
  return version.split('.').map(BigInt);
}
function compare(a, b) {
  const left = parts(a), right = parts(b);
  for (let i = 0; i < 3; i++) {
    if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
  }
  return 0;
}
function releaseVersion(release) {
  return /^v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?:rc|-rc\.?[1-9]\d*)?$/.exec(release.tag_name || '')?.[1];
}
function stableVersions(releases) {
  return releases.filter(r => !r.draft && r.prerelease === false).map(releaseVersion).filter(Boolean);
}
function assertOpen(version, releases) {
  if (stableVersions(releases).some(v => compare(v, version) >= 0)) {
    throw new Error(`Version ${version} is already released or older than a stable release; use a new build or a newer RELEASE_SERIES`);
  }
}
function selectVersion({base, series = '', number}) {
  const [major, minor] = parts(base);
  if (!/^[1-9]\d*$/.test(String(number))) throw new Error('Invalid build number');
  if (series && !/^(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(series)) {
    throw new Error('Invalid RELEASE_SERIES; expected X.Y');
  }
  const version = `${series || `${major}.${minor}`}.${number}`;
  return {version, tag: `v${version}rc`};
}
module.exports = {selectVersion, assertOpen};
