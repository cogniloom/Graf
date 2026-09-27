const {test} = require('node:test');
const assert = require('node:assert/strict');
const {selectVersion} = require('./release_version.cjs');
const select = (number, series = '', base = '0.1.0') => selectVersion({base, series, number});

test('build number is patch version with rc suffix', () => {
  assert.deepEqual(select(5), {version: '0.1.5', tag: 'v0.1.5rc'});
  assert.equal(select(6).tag, 'v0.1.6rc');
  assert.equal(select(7).tag, 'v0.1.7rc');
});
test('reruns reuse the same version without consulting mutable release history', () => {
  assert.deepEqual(select(5), select(5));
});
test('series override controls major and minor; patch always comes from run number', () => {
  assert.equal(select(8, '0.2').tag, 'v0.2.8rc');
  assert.equal(select(9, '1.0').tag, 'v1.0.9rc');
  assert.equal(select(10, '', '2.3.99').tag, 'v2.3.10rc');
});
test('malformed series and build numbers fail closed', () => {
  for (const series of ['v1.0', '1.0.0', '01.0', '1.0rc', '1.0\n']) {
    assert.throws(() => select(1, series));
  }
  for (const number of [0, -1, '01', '2rc', '', '1\n']) assert.throws(() => select(number));
});
